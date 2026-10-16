-- =====================================================================
-- EveryDay 迁移脚本 002：v1.1 数据结构
--
-- ⚠️ 关于本文件的来源
--   交接文档 P1 写的是「实现本文档附录的全部 Schema 变更」，
--   但**附录只给了参考资料清单，没有 Schema 正文**。
--   因此这里的表与字段是**按 P2~P5 的需求反推设计**的：
--     P2 优先级 / 标签 / 子任务 / 依赖
--     P3 通知（含稍后提醒）与番茄钟专注记录
--     P4 历史统计与回收站（软删除）
--     P5 屏幕时间统计
--   设计与取舍记录在 docs/adr/0002-v11-schema.md，需人工确认。
--
-- 约定（与 001 一致）：
--   日期 YYYY-MM-DD，时间 HH:MM，日期时间 YYYY-MM-DDTHH:MM:SS，布尔 0/1
--   全部语句幂等：新表用 IF NOT EXISTS；ALTER TABLE ADD COLUMN 没有
--   IF NOT EXISTS，靠执行器逐语句执行并容忍 "duplicate column name" 兜住。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1) task_item 扩展
--    priority        0=紧急 1=高 2=普通 3=低（默认普通）
--    parent_task_id  子任务指向父任务；NULL 表示顶层任务
--    deleted_at      软删除时间戳（回收站）；NULL 表示未删除
--
--    说明：这两列**不加外键**。SQLite 的 ALTER TABLE ADD COLUMN 只在默认值
--    为 NULL 时才允许带 REFERENCES，而且给已有表补外键需要整表重建。
--    v1.1 的删除是软删除（回收站），父子关系不会产生孤儿，
--    因此由服务层维护层级即可，见 docs/adr/0002-v11-schema.md。
-- ---------------------------------------------------------------------
ALTER TABLE task_item ADD COLUMN priority INTEGER NOT NULL DEFAULT 2;
ALTER TABLE task_item ADD COLUMN parent_task_id INTEGER DEFAULT NULL;
ALTER TABLE task_item ADD COLUMN deleted_at TEXT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_task_item_priority  ON task_item (priority);
CREATE INDEX IF NOT EXISTS idx_task_item_parent    ON task_item (parent_task_id);
CREATE INDEX IF NOT EXISTS idx_task_item_deleted   ON task_item (deleted_at);

-- ---------------------------------------------------------------------
-- 2) 标签（P2）
--    一个任务多个标签；标签是自由文本，用连表而不是逗号串，
--    这样「按标签筛选」可以直接走索引。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_tag (
    task_id INTEGER NOT NULL,
    tag     TEXT    NOT NULL CHECK (length(trim(tag)) BETWEEN 1 AND 30),
    PRIMARY KEY (task_id, tag),
    FOREIGN KEY (task_id) REFERENCES task_item (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_tag_tag ON task_tag (tag);

-- ---------------------------------------------------------------------
-- 3) 前置依赖（P2）
--    task_id 依赖 depends_on_id：depends_on 未完成时 task 不可勾选。
--    环（A→B→A）由服务层用拓扑排序拒绝，DB 层只保证不自指与去重。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_dependency (
    task_id       INTEGER NOT NULL,
    depends_on_id INTEGER NOT NULL,
    PRIMARY KEY (task_id, depends_on_id),
    CHECK (task_id <> depends_on_id),
    FOREIGN KEY (task_id)       REFERENCES task_item (id) ON DELETE CASCADE,
    FOREIGN KEY (depends_on_id) REFERENCES task_item (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_dependency_dep ON task_dependency (depends_on_id);

-- ---------------------------------------------------------------------
-- 4) 任务历史（P4）
--    完成率热力图、连续打卡等统计都从这里算。
--    task_id **不设外键**：任务被彻底清理后历史仍要保留，
--    否则统计会随着删除而缩水。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL,
    action      TEXT    NOT NULL
                        CHECK (action IN ('created', 'updated', 'completed',
                                          'uncompleted', 'deleted', 'restored')),
    occurred_at TEXT    NOT NULL,
    detail      TEXT    NULL
);

CREATE INDEX IF NOT EXISTS idx_task_history_task ON task_history (task_id);
CREATE INDEX IF NOT EXISTS idx_task_history_time ON task_history (occurred_at);

-- ---------------------------------------------------------------------
-- 5) 专注记录（P3 番茄钟 / P4 专注时长统计）
--    planned_minutes 计划时长，actual_seconds 实际专注秒数，
--    completed 表示是否跑完整段（中途放弃也算记录）。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS focus_record (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         INTEGER NULL,
    started_at      TEXT    NOT NULL,
    ended_at        TEXT    NULL,
    planned_minutes INTEGER NOT NULL CHECK (planned_minutes BETWEEN 1 AND 180),
    actual_seconds  INTEGER NOT NULL DEFAULT 0 CHECK (actual_seconds >= 0),
    completed       INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)),
    FOREIGN KEY (task_id) REFERENCES task_item (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_focus_record_started ON focus_record (started_at);
CREATE INDEX IF NOT EXISTS idx_focus_record_task    ON focus_record (task_id);

-- ---------------------------------------------------------------------
-- 6) 屏幕时间（P5）
--    按「日期 + 应用」聚合的秒数。采样是 2 秒一次的高频动作，
--    若按样本存储会迅速膨胀；这里由服务层做会话合并后再 UPSERT 累加。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS screen_time_record (
    day      TEXT    NOT NULL,
    app_name TEXT    NOT NULL,
    seconds  INTEGER NOT NULL DEFAULT 0 CHECK (seconds >= 0),
    PRIMARY KEY (day, app_name)
);

CREATE INDEX IF NOT EXISTS idx_screen_time_day ON screen_time_record (day);

-- ---------------------------------------------------------------------
-- 7) 提醒记录（P3）
--    用于「每天同一任务只提醒一次」以及「稍后提醒 5 分钟」。
--    snooze_until 为空表示未推迟；notified_at 为空表示尚未通知。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reminder_log (
    task_id      INTEGER NOT NULL,
    remind_date  TEXT    NOT NULL,
    notified_at  TEXT    NULL,
    snooze_until TEXT    NULL,
    PRIMARY KEY (task_id, remind_date),
    FOREIGN KEY (task_id) REFERENCES task_item (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reminder_log_date ON reminder_log (remind_date);

-- ---------------------------------------------------------------------
-- 8) 应用设置
--    app_settings 是键值表，番茄钟时长、通知开关、屏幕时间开关等
--    新配置项**不需要迁移**即可生效，这是当初选键值表的收益之一。
-- ---------------------------------------------------------------------

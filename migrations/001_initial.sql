-- =====================================================================
-- EveryDay 迁移脚本 001：初始结构
-- 对应《每日计划软件 V1 技术实现文档》第四章「数据模型设计」
--
-- 约定：
--   * 日期存 TEXT，格式 YYYY-MM-DD
--   * 时间存 TEXT，格式 HH:MM
--   * 日期时间存 TEXT，格式 YYYY-MM-DDTHH:MM:SS
--   * 布尔存 INTEGER，0 / 1
--   * 全部语句幂等（IF NOT EXISTS），且由 schema_migrations 账本保证只执行一次
-- =====================================================================

-- ---------------------------------------------------------------------
-- 迁移账本（基础设施表，非业务实体）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL,
    checksum   TEXT NULL
);

-- ---------------------------------------------------------------------
-- 任务实体 TaskItem
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_item (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    title               TEXT    NOT NULL
                                CHECK (length(trim(title)) BETWEEN 1 AND 100),
    description         TEXT    NULL,
    type                TEXT    NOT NULL
                                CHECK (type IN ('once', 'daily', 'weekly', 'monthly')),
    due_date            TEXT    NULL,
    remind_time         TEXT    NULL,
    is_completed        INTEGER NOT NULL DEFAULT 0
                                CHECK (is_completed IN (0, 1)),
    last_completed_date TEXT    NULL,
    created_at          TEXT    NOT NULL
);

-- 今日列表查询：按类型与日期过滤
CREATE INDEX IF NOT EXISTS idx_task_item_type        ON task_item (type);
CREATE INDEX IF NOT EXISTS idx_task_item_completed   ON task_item (is_completed);
CREATE INDEX IF NOT EXISTS idx_task_item_due_date    ON task_item (due_date);
CREATE INDEX IF NOT EXISTS idx_task_item_created_at  ON task_item (created_at);

-- ---------------------------------------------------------------------
-- 预设模板实体 PresetTemplate
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS preset_template (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 100),
    type        TEXT NOT NULL CHECK (type IN ('once', 'daily', 'weekly', 'monthly')),
    remind_time TEXT NULL,
    category    TEXT NOT NULL DEFAULT '其他'
);

CREATE INDEX IF NOT EXISTS idx_preset_template_category ON preset_template (category);

-- ---------------------------------------------------------------------
-- 应用设置 AppSettings
--
-- 实现说明：文档把 AppSettings 描述为「实体」，其字段是设置的集合。
-- 这里采用「单表键值对」而非「单行多列」存储，原因是设置项会随版本增长，
-- 键值表可以在不改表结构、不写 ALTER TABLE 迁移的前提下新增配置项。
-- 对外暴露的仍然是文档定义的 AppSettings 数据类，字段与文档一一对应。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- 首次创建时插入默认模板（文档 5.7：覆盖工作、健康、学习等常见场景）
-- ---------------------------------------------------------------------
INSERT INTO preset_template (title, type, remind_time, category)
SELECT '晨间计划', 'daily', '08:30', '工作'
WHERE NOT EXISTS (SELECT 1 FROM preset_template WHERE title = '晨间计划');

INSERT INTO preset_template (title, type, remind_time, category)
SELECT '每日站会', 'daily', '09:30', '工作'
WHERE NOT EXISTS (SELECT 1 FROM preset_template WHERE title = '每日站会');

INSERT INTO preset_template (title, type, remind_time, category)
SELECT '写工作日报', 'daily', '18:00', '工作'
WHERE NOT EXISTS (SELECT 1 FROM preset_template WHERE title = '写工作日报');

INSERT INTO preset_template (title, type, remind_time, category)
SELECT '周一计划会', 'weekly', '10:00', '工作'
WHERE NOT EXISTS (SELECT 1 FROM preset_template WHERE title = '周一计划会');

INSERT INTO preset_template (title, type, remind_time, category)
SELECT '每月账单核对', 'monthly', '20:00', '财务'
WHERE NOT EXISTS (SELECT 1 FROM preset_template WHERE title = '每月账单核对');

INSERT INTO preset_template (title, type, remind_time, category)
SELECT '喝水提醒', 'daily', '15:00', '健康'
WHERE NOT EXISTS (SELECT 1 FROM preset_template WHERE title = '喝水提醒');

INSERT INTO preset_template (title, type, remind_time, category)
SELECT '晚间运动 30 分钟', 'daily', '20:30', '健康'
WHERE NOT EXISTS (SELECT 1 FROM preset_template WHERE title = '晚间运动 30 分钟');

INSERT INTO preset_template (title, type, remind_time, category)
SELECT '背单词 20 分钟', 'daily', '21:30', '学习'
WHERE NOT EXISTS (SELECT 1 FROM preset_template WHERE title = '背单词 20 分钟');

INSERT INTO preset_template (title, type, remind_time, category)
SELECT '读书 30 分钟', 'daily', '22:00', '学习'
WHERE NOT EXISTS (SELECT 1 FROM preset_template WHERE title = '读书 30 分钟');

INSERT INTO preset_template (title, type, remind_time, category)
SELECT '每周复盘', 'weekly', '19:00', '学习'
WHERE NOT EXISTS (SELECT 1 FROM preset_template WHERE title = '每周复盘');

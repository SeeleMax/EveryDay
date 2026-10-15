# EveryDay 架构说明

对应《每日计划软件 V1 技术实现文档》第三章「系统架构」。

---

## 1. 分层与依赖方向

```
┌──────────────────────────────────────────────────────────────┐
│ 表示层  everyday.ui                                          │
│   MainWindow · FloatWindow · TaskDialog · PresetDialog        │
│   SettingsDialog · TrayIcon · TaskRow · AppContext           │
│   （唯一依赖 PySide6 的包）                                   │
└───────────────────────────┬──────────────────────────────────┘
                            │ 只通过 AppContext 取服务、发信号
┌───────────────────────────▼──────────────────────────────────┐
│ 服务层  everyday.services                                    │
│   TaskService · CalendarService · IcsParser · StartupService  │
│   PresetService · BackupService · SettingsService            │
│   （纯标准库，无 GUI 依赖，可完整单测）                        │
└───────────────────────────┬──────────────────────────────────┘
┌───────────────────────────▼──────────────────────────────────┐
│ 数据层  everyday.db                                          │
│   Database（连接/PRAGMA/事务）· MigrationRunner · 三个仓储     │
└───────────────────────────┬──────────────────────────────────┘
┌───────────────────────────▼──────────────────────────────────┐
│ 实体层  everyday.models                                      │
│   TaskItem · PresetTemplate · AppSettings                    │
│   （实体自带业务规则，纯函数式，无 IO）                        │
└──────────────────────────────────────────────────────────────┘

横切关注点：paths · timeutils · logging_setup · exceptions
```

**关键约束**：依赖箭头只能向下。`services` 不 import `ui`，
`db` 不 import `services`，`models` 不 import 任何项目内模块（只依赖 `timeutils`）。

这样做的收益：`services` / `db` / `models` 的测试不需要 GUI、
不需要数据库文件，用内存 SQLite 即可，运行时间在毫秒级。

---

## 2. 类图

```mermaid
classDiagram
    direction TB

    class TaskItem {
        +int id
        +str title
        +str description
        +TaskType type
        +date due_date
        +time remind_time
        +bool is_completed
        +date last_completed_date
        +datetime created_at
        +is_visible_on(day) bool
        +is_overdue_on(day) bool
        +needs_rollover_on(day) bool
        +rolled_over(day) TaskItem
        +with_completion(done, day) TaskItem
        +sort_key() tuple
        +to_row() dict
        +from_row(row)$ TaskItem
    }

    class TaskType {
        <<enumeration>>
        ONCE
        DAILY
        WEEKLY
        MONTHLY
        +label str
        +is_recurring bool
        +from_value(v)$ TaskType
    }

    class PresetTemplate {
        +int id
        +str title
        +TaskType type
        +time remind_time
        +str category
        +create_task(day, title) TaskItem
        +from_task(task, category)$ PresetTemplate
        +to_row() dict
        +from_row(row)$ PresetTemplate
    }

    class AppSettings {
        +bool auto_start
        +bool float_window_visible
        +float float_window_left
        +float float_window_top
        +str calendar_ics_path
        +int calendar_refresh_minutes
        +str log_level
        +bool startup_backup_enabled
        +to_kv() dict
        +from_kv(values)$ AppSettings
        +merged(**changes) AppSettings
    }

    class TaskService {
        -TaskRepository _repo
        -date _last_rollover_day
        +rollover(day) int
        +rollover_if_needed(day) int
        +list_today(day) list
        +refresh(day) list
        +progress_today(day) TaskProgress
        +create(task) TaskItem
        +update(task) TaskItem
        +delete(id) bool
        +set_completed(id, done, day) TaskItem
        +validate(title, type, due)$ str
    }

    class TaskProgress {
        <<frozen>>
        +int completed
        +int total
        +ratio float
        +label str
        +is_all_done bool
    }

    class CalendarService {
        -CalendarSnapshot _snapshot
        -str _last_error
        +refresh(source, day) bool
        +today_events(day) list
        +upcoming(day) list
        +next_event(moment) CalendarEvent
        +last_error str
    }

    class CalendarEvent {
        <<frozen>>
        +str uid
        +str summary
        +datetime start
        +datetime end
        +str location
        +bool all_day
        +last_day date
        +time_label str
        +overlaps_day(day) bool
    }

    class StartupService {
        +is_supported()$ bool
        +is_enabled() bool
        +enable(script) bool
        +disable() bool
        +apply(enabled, script) bool
        +current_command() str
    }

    class BackupService {
        -Path _directory
        -int _keep
        +run_startup_backup(source) Path
        +available() list
    }

    class PresetService {
        +list_all() list
        +create(t) PresetTemplate
        +update(t) PresetTemplate
        +delete(id) bool
        +instantiate(id, day, title) TaskItem
        +save_as_template(task, category) PresetTemplate
    }

    class SettingsService {
        +load() AppSettings
        +save(s) AppSettings
        +update(**changes) AppSettings
        +seed_from_template() bool
        +export_to_json(path) Path
    }

    class Database {
        -str _path
        -Connection _connection
        -RLock _lock
        +connect() Connection
        +transaction() ContextManager
        +query(sql, params) list
        +query_one(sql, params) Row
        +execute(sql, params) int
        +executescript(sql)
        +close()
    }

    class MigrationRunner {
        -Database _database
        +run() list
        +pending() list
        +applied_versions() dict
        +current_version() str
    }

    class TaskRepository {
        +add(task) int
        +update(task) bool
        +delete(id) bool
        +get(id) TaskItem
        +list_all() list
        +update_completion(id, done, day) bool
    }

    class PresetRepository {
        +add(t) int
        +update(t) bool
        +delete(id) bool
        +get(id) PresetTemplate
        +list_all() list
        +categories() list
    }

    class SettingsRepository {
        +load() AppSettings
        +save(s)
        +get_raw(key, default) str
        +set_raw(key, value)
    }

    class AppContext {
        <<QObject>>
        +tasks_changed Signal
        +settings_changed Signal
        +calendar_changed Signal
        +float_visibility_changed Signal
        +task_service TaskService
        +today_tasks() list
        +progress() TaskProgress
        +set_float_visible(v)
        +update_settings(**c) AppSettings
    }

    class MainWindow {
        +float_toggle_requested Signal
        +refresh_all()
        +create_task()
        +create_from_template()
        +edit_task(id)
        +delete_task(id)
        +open_settings()
    }

    class FloatWindow {
        +visibility_change_requested Signal
        +refresh_all()
        +restore_position()
        +persist_position()
    }

    class TrayIcon {
        +show_main_requested Signal
        +float_toggle_requested Signal
        +autostart_toggle_requested Signal
        +quit_requested Signal
        +sync_float_visible(v)
        +sync_autostart(v)
    }

    TaskItem --> TaskType
    PresetTemplate --> TaskType
    PresetTemplate ..> TaskItem : create_task
    TaskService --> TaskRepository
    TaskService ..> TaskProgress : progress_today
    TaskService ..> TaskItem
    TaskRepository ..> TaskItem
    PresetRepository ..> PresetTemplate
    SettingsRepository ..> AppSettings
    PresetService --> PresetRepository
    SettingsService --> SettingsRepository
    CalendarService ..> CalendarEvent
    MigrationRunner --> Database
    TaskRepository --> Database
    PresetRepository --> Database
    SettingsRepository --> Database
    AppContext --> TaskService
    AppContext --> CalendarService
    AppContext --> PresetService
    AppContext --> SettingsService
    AppContext --> StartupService
    MainWindow --> AppContext
    FloatWindow --> AppContext
    TrayIcon --> AppContext
```

---

## 3. 实体关系图（ER）

```mermaid
erDiagram
    TASK_ITEM {
        INTEGER id PK "自增主键"
        TEXT    title "必填，1~100 字符"
        TEXT    description "可空"
        TEXT    type "once|daily|weekly|monthly"
        TEXT    due_date "YYYY-MM-DD，单次任务必填"
        TEXT    remind_time "HH:MM，可空"
        INTEGER is_completed "0/1"
        TEXT    last_completed_date "上次完成日期"
        TEXT    created_at "YYYY-MM-DDTHH:MM:SS"
    }

    PRESET_TEMPLATE {
        INTEGER id PK "自增主键"
        TEXT    title "必填，1~100 字符"
        TEXT    type "once|daily|weekly|monthly"
        TEXT    remind_time "HH:MM，可空"
        TEXT    category "默认『其他』"
    }

    APP_SETTINGS {
        TEXT key PK "设置项名"
        TEXT value "字符串化的取值"
    }

    SCHEMA_MIGRATIONS {
        TEXT version PK "迁移版本号，如 001"
        TEXT applied_at "执行时间"
        TEXT checksum "脚本 SHA-256"
    }

    TASK_ITEM ||..o{ PRESET_TEMPLATE : "逻辑派生（另存为模板），无外键"
```

**说明**

* 三个业务表之间**没有物理外键**：任务与模板是彼此独立的概念，
  模板只是「常用任务的速记」，删除模板不影响已创建的任务（文档 5.7）。
* `APP_SETTINGS` 采用键值对而非单行多列：设置项会随版本增长，
  键值表新增配置无需 `ALTER TABLE`，对外仍暴露字段固定的 `AppSettings` 类。
* 日期时间全部以 `TEXT` 存储，转换集中在 `everyday/timeutils.py`，
  保证「写入什么格式、读回什么格式」全局只有一处定义。
* `TASK_ITEM` 上有 4 个索引（`type` / `is_completed` / `due_date` / `created_at`），
  对应今日列表的过滤与排序路径。

---

## 4. 时序图

### 4.1 应用启动

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户 / 开机自启
    participant M as main.py
    participant A as Application (app.py)
    participant P as paths
    participant L as logging_setup
    participant D as Database
    participant R as MigrationRunner
    participant B as BackupService
    participant S as 服务层
    participant Q as QApplication
    participant W as MainWindow / FloatWindow / Tray

    U->>M: python main.py [--minimized]
    M->>A: parse_args + Application(options).run()
    A->>P: data_dir()
    A->>L: setup_logging(level, logs_dir)
    A->>D: Database(database_path)
    A->>B: run_startup_backup(db)  ← 先备份再迁移
    B-->>A: backups/everyday-YYYY-MM-DD.db
    A->>R: run()
    R->>R: applied_versions() 查账本
    R->>D: executescript(001_initial.sql)
    R->>D: INSERT schema_migrations(version, checksum)
    A->>S: 构造 6 个服务
    A->>S: SettingsService.seed_from_template()
    A->>S: StartupService 与注册表对账
    A->>Q: 创建 QApplication 子类（notify 兜异常）
    A->>S: TaskService.rollover_if_needed(today)
    A->>S: CalendarService.refresh(ics_path)
    A->>W: 构造窗口与托盘
    A->>W: tray.show()
    alt 非 --minimized
        A->>W: MainWindow.show()
    else --minimized
        A->>W: 仅显示悬浮窗（若配置开启）
    end
    A->>Q: exec() 进入事件循环
```

### 4.2 勾选完成一个单次任务

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant R as TaskRow
    participant W as MainWindow
    participant C as AppContext
    participant T as TaskService
    participant Repo as TaskRepository
    participant DB as SQLite
    participant F as FloatWindow
    participant Tray as TrayIcon

    U->>R: 点击复选框
    R->>W: completion_changed(task_id, true)
    W->>T: set_completed(task_id, true, today)
    T->>Repo: get(task_id)
    Repo->>DB: SELECT ... WHERE id=?
    DB-->>Repo: row
    Repo-->>T: TaskItem
    T->>T: task.with_completion(true, today)
    T->>Repo: update(task)
    Repo->>DB: UPDATE task_item SET is_completed=1, last_completed_date=?
    W->>C: notify_tasks_changed()
    C-->>W: tasks_changed
    C-->>F: tasks_changed
    W->>T: refresh(today) → list_today()
    Note over W: 单次任务已完成 → is_visible_on 返回 False → 从列表消失
    W->>T: progress_today(today)
    Note over W: 分母含「今天已完成因而隐藏的单次任务」，进度条不回退
    F->>T: refresh(today)
    C-->>Tray: 更新托盘进度提示
```

### 4.3 循环任务跨午夜重置

```mermaid
sequenceDiagram
    autonumber
    participant Timer as QTimer (1 小时)
    participant A as Application
    participant T as TaskService
    participant Repo as TaskRepository
    participant DB as SQLite
    participant C as AppContext
    participant W as MainWindow / FloatWindow

    Timer->>A: timeout
    A->>A: today = timeutils.today_local()
    alt today == _current_day
        A-->>Timer: 无变化，直接返回
    else 日期已变更
        A->>A: _current_day = today
        A->>T: rollover(today)
        loop 每个任务
            T->>T: needs_rollover_on(today)?
            Note over T: 循环任务 且 已完成 且 上次完成日期 != 今天
            T->>Repo: update(task.rolled_over(today))
            Repo->>DB: UPDATE ... is_completed=0
        end
        T-->>A: reset_count
        A->>C: notify_tasks_changed()
        C-->>W: tasks_changed → 重新拉取今日列表
    end
```

### 4.4 日历加载失败时的降级

```mermaid
sequenceDiagram
    autonumber
    participant Timer as QTimer (30 分钟)
    participant CS as CalendarService
    participant IO as read_source
    participant P as ics_parser
    participant UI as MainWindow

    Timer->>CS: refresh(source, today)
    CS->>IO: 读取文件 / HTTP
    alt 读取或解析失败
        IO-->>CS: 抛异常
        CS->>CS: _last_error = str(exc)
        Note over CS: **保留上一次成功的 _snapshot，不清空**
        CS-->>Timer: False
        Timer->>UI: calendar_changed
        UI->>UI: 侧栏显示旧日程 + 「数据源加载失败」提示
    else 成功
        IO-->>CS: ICS 文本
        CS->>P: load_events(text, today, today+6)
        P->>P: 解析 → 展开 RRULE → 按窗口过滤 → 排序
        P-->>CS: list[CalendarEvent]
        CS->>CS: 替换 _snapshot，清空 _last_error
        CS-->>Timer: True
        Timer->>UI: calendar_changed → 重建今日日程列表
    end
```

---

## 5. 关键设计决策

| 决策 | 选择 | 理由 | 代价 |
|---|---|---|---|
| GUI 框架 | PySide6 (Qt6) | V1 要求的无边框透明置顶窗、不占任务栏、系统托盘、多屏 DPI，Python 生态里只有 Qt 能全部原生支持；也是 WPF 最贴近的对应物 | 引入唯一的第三方依赖（约 150MB） |
| ICS 解析 | 自研子集 | 只需要「单次 + 常见循环」并按 7 天窗口按需展开；第三方库为完整 RFC 引入可观依赖，与本项目「零配置单机」定位不符 | 不支持 `BYDAY` / `TZID` 等高级特性（见 known-issues） |
| 设置存储 | 键值表 + 模板播种 | 避免「文件与数据库两份设置互相覆盖」；新增配置项无需迁移 | 设置值以字符串存储，类型转换集中在 `AppSettings` |
| 数据库连接 | 单长连接 + 可重入锁 | 单机单用户最简最快；避免 `:memory:` 每次连接都是新空库的陷阱；UI 与定时器线程可共享 | 不支持真并发写（桌面场景不需要） |
| 备份实现 | SQLite 在线备份 API | WAL 模式下直接复制 `.db` 会丢掉 `-wal` 中未检查点的提交，得到过期备份 | 需要源连接保持打开 |
| 图标 | `QPainter` 代码绘制 | 仓库保持纯文本、无二进制资产；任意 DPI 下按需重渲染 | 无法使用设计师提供的复杂图标 |
| 窗口解耦 | `AppContext` 信号总线 | 避免窗口互相直接调用导致「改了主窗口忘了刷悬浮窗」 | 多一层间接 |
| 迁移账本 | 记录 SHA-256 | 能发现「已执行的迁移被偷偷改动」 | 开发期调整迁移会产生告警（不阻断） |

---

## 6. 线程模型

应用**只使用 Qt 主线程**处理界面与业务，不引入工作线程：

* 数据库访问是本地 SQLite，毫秒级，不构成 UI 阻塞
* 日历网络订阅有 15 秒超时，是唯一的潜在阻塞点。**已知风险**：
  网络不可达时主线程会等待至超时（见 `docs/known-issues.md` KI-05），
  后续应移入 `QThread` 或 `QNetworkAccessManager`
* `Database` 内部使用 `threading.RLock` 串行化所有语句，
  因此未来引入工作线程时数据层已具备基本的并发安全

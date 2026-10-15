# 已知问题清单

> **诚实声明**：本文件列出所有「未解决 / 未验证 / 有取舍」的问题，不做隐瞒。
> 最后更新：v1.0.0 验证完成后。

**当前验证状态**：单元测试 230 项全部通过；核心服务覆盖率 86.2%；
端到端启动/退出冒烟 12/12 与 14/14 全部通过。
详见 [acceptance-report.md](acceptance-report.md)。

---

## 一、本次验证发现并已修复的缺陷

这些都是**只有真正运行才会暴露**的问题，全部已补回归测试。

### BF-01 点「退出应用」退不掉（已修复，致命）

| 项目 | 说明 |
|---|---|
| 现象 | 托盘菜单「退出应用」点了没反应，主窗口与悬浮窗都消失了但进程仍在运行 |
| 根因 | `QApplication.quit()` 会先给所有顶层窗口派发 close 事件。主窗口与悬浮窗为实现「关闭 = 最小化到托盘」都调用了 `event.ignore()`，**只要有一个窗口拒绝关闭，事件循环就不会退出** |
| 发现方式 | 端到端启动冒烟脚本挂死；看门狗超时后用 `faulthandler.dump_traceback(all_threads=True)` 打出调用栈定位；再用两个 `closeEvent` 里 `ignore()` 的窗口写出最小复现 |
| 修复 | 新增 `AppContext.begin_shutdown()`；两个窗口在 `context.shutting_down` 或 `QApplication.closingDown()` 时放行关闭；`Application._quit()` 先置标志再 `quit()` |
| 回归测试 | `tests/test_ui_smoke.py::ShutdownBehaviourTest`（4 项）+ `tests/test_app_startup.py`（端到端退出） |
| 教训 | 「关闭 = 隐藏」这个 Qt 惯用法与 `quit()` 的语义冲突，静态审查完全看不出来 |

### BF-02 `--data-dir` 下界面显示并打开错误的目录（已修复）

| 项目 | 说明 |
|---|---|
| 现象 | 传 `--data-dir D:\tmp\everyday` 时，主窗口状态栏显示的仍是 `%LOCALAPPDATA%\EveryDay\everyday.db`，设置页「打开数据目录」也打开默认目录 |
| 根因 | `MainWindow` 与 `SettingsDialog` 调用的是**不带参数**的 `paths.database_path()` / `paths.data_dir()`，而 `--data-dir` 只传进了 `Application` 内部，没有成为进程级数据根目录 |
| 附带影响 | `main()` 在解析参数之后、应用数据目录之前就调用 `setup_logging()`，因此即使传了 `--data-dir`，也会在默认目录下创建并长期持有一个日志文件 |
| 修复 | `Application.run()` 把 `--data-dir` 写入 `EVERYDAY_DATA_DIR`（`paths` 早已为此预留的环境变量）；`main()` 的早期日志改用解析后的目录 |
| 回归测试 | `test_app_startup.py::test_data_dir_override_never_touches_default_location`、`test_status_bar_shows_actual_database_path`；`test_ui_smoke.py::test_status_bar_shows_current_data_directory` |

### BF-03 新建任务对话框忽略传入的默认日期（已修复）

| 项目 | 说明 |
|---|---|
| 现象 | `TaskDialog(default_date=X)` 新建任务时，日期控件停留在系统当天，`X` 被静默忽略 |
| 根因 | `_populate()` 在「新建」分支提前 `return`，跳过了日期写入 |
| 修复 | 日期改为无条件写入，编辑时优先用任务自身日期 |
| 回归测试 | `DialogTest::test_new_task_dialog_uses_default_date`（等 3 项） |

### BF-04 首次运行生成空数据库备份（已修复）

| 项目 | 说明 |
|---|---|
| 现象 | 全新安装第一次启动后，`backups/` 里出现一个内容为空的 `everyday-YYYY-MM-DD.db`，并占掉当天的备份名额 |
| 根因 | `create_backup()` 收到 `Database` 对象时先 `connect()`；而 `sqlite3.connect` 会顺手创建空库文件，于是备份了一个空数据库 |
| 修复 | 源库文件尚不存在（且非内存库）时直接跳过备份 |
| 回归测试 | `test_backup_service.py::test_unopened_database_object_returns_none_without_creating_file`；`test_app_startup.py::test_first_run_does_not_create_empty_backup` |

---

## 二、环境层面的问题（非代码问题）

### ENV-01 本机 `pip` 完全不可用

| 项目 | 说明 |
|---|---|
| 现象 | venv 的 pip 每次执行都会崩溃：`FileNotFoundError: [WinError 2]`（`platformdirs` 查询 `CSIDL_COMMON_APPDATA` 失败）；Anaconda 自带的 pip 则报 `OSError: [Errno 22] Invalid argument: ...\distlib\w32.exe` |
| 尝试过 | `PIP_CONFIG_FILE=nul`、`pip --isolated`、`--no-input`、改用 Anaconda 的 pip 装到 venv —— 均失败 |
| 影响 | 装不上 `pytest` / `pytest-cov` / `pyinstaller`。因此交付物 **D3 自包含可执行程序未产出** |
| 应对 | 验证改用标准库：`python -m unittest` 跑测试；自研 `tools/coverage_report.py`（基于 `trace`）测覆盖率，得到 86.2% |
| 需要你做 | 在你的环境里 `pip install -r requirements-dev.txt` 与 `pip install pyinstaller` 即可复现完整工具链 |

### ENV-02 Qt 提示找不到字体目录

| 项目 | 说明 |
|---|---|
| 现象 | 离屏模式下打印 `QFontDatabase: Cannot find font directory .../PySide6/lib/fonts` |
| 影响 | 无。Qt6 起不再随包分发字体，离屏渲染不需要字体；真实桌面会话使用系统字体 |
| 建议 | 忽略；若离屏截图需要文字，可用 `QT_QPA_FONTDIR` 指定字体目录 |

### ENV-03 被杀死的进程会留下子进程

| 项目 | 说明 |
|---|---|
| 现象 | 终止包装它的 shell 并不会终止已启动的 Python 应用进程，残留进程会长期持有日志与数据库文件，导致后续运行出现 `PermissionError` |
| 影响 | 仅影响调试过程，不影响产品 |
| 应对 | 重启前用 `Get-Process python` 检查；本仓库的冒烟脚本自带看门狗，正常路径不会挂死 |

---

## 三、功能与协议层面的取舍（仍然存在）

### KI-03 ICS 的 `TZID` 被忽略

`DTSTART;TZID=America/New_York:20261005T090000` 会被当作**本地时间** 09:00。
跨时区订阅的事件时间可能偏移数小时。`Z` 后缀（UTC）是**正确转换**的（已单测）。
完整支持需要解析 `VTIMEZONE` 与历史夏令时规则，成本超出 V1 范围。
后续可用 `zoneinfo`（3.9+ 标准库）补齐。

### KI-04 高级递归修饰符不支持

不支持 `BYDAY` / `BYMONTHDAY` / `BYSETPOS` / `BYYEARDAY` / `RECURRENCE-ID`。
`FREQ` 不是 `DAILY`/`WEEKLY`/`MONTHLY` 时按**单次事件**处理并记录 WARNING。
风险：「每月第二个周二」这类规则会退化为只出现一次。
好处：不崩溃、不静默丢弃，日志里有痕迹。

### KI-05 日历网络订阅在 UI 线程同步执行

`read_source()` 对 `http(s)://` 用 `urllib` 同步读取，超时 15 秒。
订阅地址不可达时界面可能卡顿最多 15 秒。启动时的加载发生在主窗口显示之前，
用户看不到；30 分钟一次的自动刷新可能可见。
后续改用 `QNetworkAccessManager`（异步、无额外线程）。

### KI-06 没有单实例互斥

重复启动会出现多个实例，各自持有托盘图标并写同一个数据库。
本次验证过程中就真的踩到过：一个残留进程占着日志文件，导致新实例写日志失败。
数据本身安全（WAL + `busy_timeout=5000`），但会出现重复图标。
后续用 `QLocalServer` / `QSharedMemory` 实现单实例激活。

### KI-07 循环任务的「参考日期」语义由本实现定义

V1 文档 4.1 的 `TaskItem` 只有 `DueDate`，但 5.5 要求「每周固定日期显示」
「每月固定日期显示」。本实现复用 `due_date` 作为**锚点**：
每周任务看 `due_date.weekday()`，每月任务看 `due_date.day`。
与文档字段一致，但语义是推断出来的。**需要你确认**：若原意是另加
`weekday` / `day_of_month` 字段，需新增迁移 `002`。

### KI-08 `DURATION` 属性被忽略

全天事件的 `DTEND` 排他语义**已正确处理**（`DTSTART;VALUE=DATE:20261005` +
`DTEND;VALUE=DATE:20261006` 只占 10-05，已单测）。
但没有 `DTEND` 而用 `DURATION` 表示时长的事件会被当作零时长。

### KI-09 每月 29/30/31 号任务在短月跳过而非顺延

锚点为 31 号的每月任务在 2 月不会出现（`day.day == due_date.day` 精确匹配）。
这是「不猜用户意图」的选择。**需要你确认**：若应顺延到月末，
改为「本月最后一天或 `due_date.day`」即可。

---

## 四、工程与体验层面的待办

### KI-10 `ui/` 未计入覆盖率

`pyproject.toml` 把 `ui/*` 排除在覆盖率统计外——Qt 的绘制与事件循环在离屏
模式下覆盖数字没有意义。UI 改为由 38 项冒烟测试 + 7 项启动集成测试
覆盖行为正确性（窗口构造、信号连接、操作落库、退出行为）。
未覆盖的仍是**真实鼠标/键盘交互**与**真实多屏**。

### KI-11 悬浮窗拖动时会触发设置写入

每次拖动结束都会 `update_settings()` → 发 `settings_changed` → 两个窗口 `refresh_all()`。
已有「位置未变则跳过」的短路，正常使用无感。后续改为退出时统一写入一次。

### KI-12 日志级别切换只对根 logger 生效

设置页保存后执行 `logging.getLogger().setLevel(...)`。当前代码没有对子 logger
单独设过级别，因此实际无影响。后续可遍历 `loggerDict` 统一设置。

### KI-13 数据库 schema 只到 `001`

仅有一个迁移脚本。迁移执行器已支持多脚本、数值排序、幂等、校验和告警，
为 v1.1 准备好了。

### KI-14 应用退出时不会写「最后使用时间」之类的统计

V1 没有统计需求，因此没有埋点表。v1.1 的历史统计需要新增表与写入逻辑。

---

## 五、明确「不做」的事

| 项目 | 原因 |
|---|---|
| 云同步 / 多设备 | V1 定位单机，文档未要求 |
| 提醒弹窗（Toast 通知） | V1.1 需求 1。V1 的 `remind_time` 只用于**排序**，不触发通知 |
| 任务优先级 / 标签 / 子任务 / 依赖 | V1.1 需求 3、6 |
| 统计面板 / 热力图 / 回收站 / 撤销 | V1.1 需求 4、5 |
| 屏幕时间统计 | V1.1 需求 10 |
| PerMonitorV2 DPI 清单 | V1.1 需求 7。Qt6 默认已启用高 DPI 缩放，但**多屏混合 DPI 的动态切换未验证** |
| 列表虚拟化 | v1.1 P6。当前用 `QListWidget` + `setItemWidget`，500 项时会变慢，届时需重构为 `QListView` + `QAbstractListModel` |

---

## 六、需要你确认的问题

| 编号 | 问题 | 影响 |
|---|---|---|
| Q-01 | KI-07：循环任务的「参考日期」用 `due_date` 作锚点是否符合原意？ | 不符则需新增迁移 002 与字段 |
| Q-02 | KI-09：月末任务（29/30/31 号）应跳过还是顺延到月末？ | 产品决策 |
| Q-03 | KI-05：是否要把日历网络读取改为异步？ | 是否引入 `QNetworkAccessManager` |
| Q-04 | KI-06：是否需要单实例互斥？ | 托盘重复图标问题 |
| Q-05 | 远端仓库地址与默认分支名？ | Git 仓库已在本地建好并提交，只差远端与推送 |

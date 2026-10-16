# 已知问题清单

> **诚实声明**：本文件列出所有「未解决 / 未验证 / 有取舍」的问题，不做隐瞒。
> 最后更新：v1.0.0 验证完成后。

**当前验证状态**：`pytest` 236 项通过 + 2 项平台跳过；核心服务覆盖率 85%（`pytest-cov`）；
`ruff check` 0 警告；端到端启动/退出冒烟 12/12 与 14/14 通过；已在真实窗口下成功启动；
自包含可执行程序 `release/EveryDay.exe`（81.1 MB）已产出并通过构建自检。
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

### BF-05 数据目录建不出来时裸崩溃、且用户看不到任何信息（已修复）

| 项目 | 说明 |
|---|---|
| 现象 | 目标数据目录无法创建时，程序在**日志初始化之前**抛出 `PermissionError` 直接死掉。用 `pythonw.exe` 启动（双击快捷方式、开机自启）时没有控制台，用户**完全看不到任何提示**，程序就无声无息地消失了 |
| 发现方式 | 在真实桌面启动时真的遇到了 `WinError 5`（我自己强删目录造成的 pending-delete 状态） |
| 修复 | 拆出纯函数 `paths.resolve_data_dir()`（只算路径、不建目录），使调用方拿得到目标路径；`main()` 捕获 `OSError` 并输出可读中文提示（目标路径 + 可能原因 + `--data-dir` 解决办法），同时用 Windows 原生消息框兜底，退出码 2 |
| 回归测试 | `test_app_startup.py::DataDirectoryResolutionTest`（5 项，含「ctypes 不可用时也不能抛」） |
| 附带收益 | 这个修复让后续所有排查都省事了：同样的失败现在直接给出中文原因 |

### BF-06 QSS 用 `px` 字号导致 Qt 每次启动打印字体警告（已修复）

| 项目 | 说明 |
|---|---|
| 现象 | 每次启动 stderr 都出现 `QFont::setPointSize: Point size <= 0 (-1), must be greater than 0` |
| 根因 | QSS 里写 `font-size: 13px` 时 Qt 只设置字体的**像素尺寸**，点数字号保持 `-1`（未指定）；之后任何对字体调用 `setPointSize` 的代码路径都会触发该警告 |
| 修复 | 所有 `font-size` 改用 `pt`（对 DPI 缩放与用户的「字体大小」辅助功能设置也更正确）；`widgets.strikeout_font()` 对点数做兜底校验 |
| 验证 | 修复后真实窗口启动的 stderr **完全干净，无任何 Qt 警告** |
| 回归测试 | `test_ui_smoke.py::StrikeoutFontTest`（3 项） |

---

### 打包（PyInstaller）踩到的坑 —— PKG-01 ~ PKG-04

这四个不是产品缺陷，而是「打包成功 ≠ 能运行」的典型来源。
四个都已固化进 `tools/build_exe.ps1`，改脚本前请先读。

#### PKG-01 PATH 污染导致 Qt 起不来（最隐蔽，时有时无）

| 项目 | 说明 |
|---|---|
| 现象 | 产物启动时 `ImportError: DLL load failed while importing QtCore: 找不到指定的程序`（`ERROR_PROC_NOT_FOUND`）。日志里迁移、配置播种都正常，只有 Qt 加载失败 |
| 根因 | PyInstaller 会**顺着 PATH** 收集被依赖的 DLL。开发机上装了 PostgreSQL / Anaconda 之类自带 OpenSSL 与 ICU 的软件时，会把 `LIBPQ.dll`、`libcrypto-3-x64.dll`、`libssl-3-x64.dll`、`icudt73.dll`、`icuuc.dll`、`krb5_64.dll`、`gssapi64.dll`、`comerr64.dll` 一并收进产物。其中**旧版 `icuuc.dll` 抢占了 Qt 的 ICU 绑定**，导致符号对不上 |
| 为什么难查 | 同一个命令两次构建，一次产出 81 MB（可用）、一次 97 MB（不可用）——仅仅因为 PATH 扫描的结果不同。先后排除了「Qt 模块排除项」「VC 运行时版本混用」等假设都不成立 |
| 定位手段 | 用 `pyi-archive_viewer -l` 列出两个产物的**文件名集合**做差集，一眼看到失败版多出那 8 个 DLL |
| 修复 | 构建前把 PATH 净化为「系统目录 + venv」。见 `build_exe.ps1` 里的 `$buildPath` |
| 附带教训 | PyInstaller **没有 `--exclude-binary`**（只有 `--exclude-module` 与 `--upx-exclude`），所以只能从环境入手 |

#### PKG-02 PyInstaller 的 PySide6 钩子漏收文件

| 项目 | 说明 |
|---|---|
| 现象 | 同上那个 QtCore DLL 错误，但与环境无关、稳定复现 |
| 根因 | PyInstaller 6.22.3 自带的 PySide6 钩子没有把必需的文件全部收进去 |
| 修复 | 显式加 `--collect-binaries PySide6 --collect-data PySide6`。注意**单文件模式两个都要**：只用 `--collect-binaries` 时目录模式可用、单文件模式仍失败 |
| 体积取舍 | 只加 `--collect-binaries` 时目录模式 168 MB；两个都加时 217 MB / 单文件 97 MB。加上 `--collect-data` 会捎带 QML、翻译等用不到的东西，但为了单文件可用只能接受 |

#### PKG-03 src 布局导致 PyInstaller 找不到本项目代码

| 项目 | 说明 |
|---|---|
| 现象 | 产物只有 **7.9 MB**（正常 81 MB），运行后静默退出、退出码 -1，连日志都没有 |
| 根因 | 本项目是 src 布局，`main.py` 只在**运行时**把 `src` 插进 `sys.path`；PyInstaller 的静态分析不执行那段代码，于是找不到 `everyday` 包，也就连带不会打包 PySide6 |
| 修复 | 显式加 `--paths src`。日志里会打印分析搜索路径，可据此确认 |

#### PKG-04 打包成功 ≠ 能运行

| 项目 | 说明 |
|---|---|
| 现象 | PyInstaller 退出码 0、产物体积也正常，但一运行就崩；而 `--noconsole` 把错误全咽了，只能靠日志文件反查 |
| 修复 | `build_exe.ps1` 在打包后会**真的把产物启动一次**（`--data-dir` 指向临时目录），等日志出现「初始化完成，进入事件循环」才算通过，否则打印日志尾部并让脚本失败 |
| 教训 | 上面 PKG-01 之所以折腾很久，就是因为没有这一步。**先让工具自证，再谈交付** |

---

## 二、环境层面的问题（非代码问题）

### ENV-01 `pip` 只在 Anaconda 那条链路上坏掉（此前结论已更正）

| 项目 | 说明 |
|---|---|
| **更正** | 我最初报告「本机 pip 完全不可用」是**错的**——当时只试了 2 个解释器。实际逐个核对的结果是： |
| ⛔ Anaconda 派生的 venv | 崩在 `platformdirs` 查询 `CSIDL_COMMON_APPDATA` 失败；且该 venv 的 `import ctypes` 直接报 `DLL load failed while importing _ctypes`（venv 创建时 `venvlauncher.exe` 复制失败留下的隐患） |
| ⛔ `E:\Anaconda\python.exe` 自带的 pip | 报 `OSError: [Errno 22] Invalid argument: ...\pip\_vendor\distlib\w32.exe`（文件损坏） |
| ✅ **官方 Python 3.13**（`%LOCALAPPDATA%\Programs\Python\Python313`） | **完全正常**，`ctypes` 也正常 |
| 处置 | 用官方 Python 3.13 重建了 `.venv`，装好 `PySide6-Essentials` / `pytest` / `pytest-cov` / `ruff`。验证工具链因此从「unittest + 自研 trace 覆盖率」升级为「pytest + pytest-cov + ruff」 |
| 影响 | 交付物 D3 仍未产出（PyInstaller 未安装），但已无环境障碍；打包命令见验收报告 §5.1 |

### ENV-02 依赖体积：别用 `PySide6` 元包

| 项目 | 说明 |
|---|---|
| 现象 | `pip install PySide6` 会连带下载 `PySide6-WebEngine`（**131.7 MB**，内含 Chromium）、`Addons`、`Pdf`，总计 200+ MB |
| 影响 | 安装极慢（实测 1~3 MB/s 时仅 WebEngine 就要 1~2 分钟），且运行时另占数十 MB 内存 |
| 处置 | `requirements.txt` / `pyproject.toml` 改为 **`PySide6-Essentials`（82.2 MB）**。导入的包名仍是 `PySide6`，**代码零改动**。实测安装耗时 138 秒 |
| 附带 | 期间 pip 缓存被反复重试撑到 **6.8 GB**，已清理 |

### ENV-03 DSH 沙箱禁止在已有路径之外新建文件

| 项目 | 说明 |
|---|---|
| 现象 | **后台作业**里 `mkdir` 一个新目录、写一个新文件都返回 `WinError 5 拒绝访问`；但对**已存在**的目录/文件读写正常（后台作业里 `mkdir logs` 成功而 `mkdir backups` 失败，仅因为 `logs` 已被前台创建过） |
| 影响 | 从本会话直接启动应用时，`%LOCALAPPDATA%\EveryDay` 无法建立，应用报「无法建立数据目录」并以退出码 2 结束 |
| 不是产品问题 | 前台 shell 与真实用户会话不受此限制；`--data-dir` 指向工作区内路径即可正常启动（本次就是这样在桌面真实跑起来的） |
| 附带坑 | 对 `%LOCALAPPDATA%\EveryDay` 做 `Remove-Item -Recurse -Force` 后，该路径会进入 **pending-delete** 状态，短时间内重建必然 `WinError 5`。等一段时间或换个目录名即可 |

### ENV-04 被杀死的进程会留下子进程

| 项目 | 说明 |
|---|---|
| 现象 | 终止包装它的 shell 并不会终止已启动的 Python 应用进程；venv 的 `python.exe` 还是个**启动器存根**，真实解释器是它的子进程（所以量内存时要找带窗口标题的那个） |
| 影响 | 残留进程长期持有日志与数据库文件，导致后续运行出现 `PermissionError` |
| 应对 | 重启前用 `Get-Process python` 检查；冒烟脚本自带看门狗，正常路径不会挂死 |

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
模式下覆盖数字没有意义。UI 改为由 **49 项 UI 冒烟测试 + 12 项启动集成测试**
覆盖行为正确性（窗口构造、信号连接、操作落库、退出行为、字体健壮性）。
未覆盖的仍是**真实鼠标/键盘交互**与**真实多屏**。

### KI-15 常驻内存 129 MB，未达文档的 <50MB（技术栈约束）

| 项目 | 说明 |
|---|---|
| 实测 | EveryDay 空闲态 **129.1 MB 工作集 / 59.0 MB 私有** |
| 对照 | 同一解释器、同一平台插件下的**最小 Qt6 空窗口**：36.4 MB / 18.0 MB |
| 结论 | 文档「<50MB」是按 WPF 假设写的。**PySide6/Qt6 单是框架基线就 36.4 MB**，该指标在当前技术栈上不可达 |
| 但 | EveryDay 自身约 93 MB 的开销偏大，值得在 v1.1 P6 剖析 |
| 优先怀疑 | ① 两个顶层窗口（悬浮窗启用 `WA_TranslucentBackground`，Windows 上需要 DWM 合成表面）② `QListWidget` + `setItemWidget` 的行控件方案（v1.1 计划改 `QListView` + `QAbstractListModel` 虚拟化）③ 应用级 QSS 的解析缓存 ④ 图标位图 |
| 建议 | 若 <50MB 是硬指标，需要重新评估技术选型（例如用原生 Win32 或 Tkinter），而不是继续优化 Qt |

### KI-16 应用退出后被托盘图标残留

终止进程（任务管理器结束进程、本会话的强制 kill）时不会走 `aboutToQuit`，
Windows 托盘区可能短暂残留一个死图标，鼠标划过即消失。正常通过托盘菜单
「退出应用」时不会出现（该路径已由端到端测试覆盖）。

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

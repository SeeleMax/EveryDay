# EveryDay · 每日计划软件

Windows 桌面任务管理应用。自定义任务、循环/单次、完成状态、实时日历（ICS）、
预设模板、桌面悬浮窗、系统托盘、开机自启。

> 当前版本 **v1.0.0**，对应《每日计划软件 V1 技术实现文档》。

---

## 1. 五分钟跑起来

### 1.1 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10 1809 及以上（Windows 11 亦可） |
| Python | 3.9 及以上（推荐 3.11 / 3.12） |
| IDE | PyCharm 2023+（社区版即可） |

### 1.2 安装依赖

```powershell
cd EveryDay
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`requirements.txt` 只有一个运行期依赖：`PySide6`（Qt6）。
**业务逻辑层（models / db / services）只用 Python 标准库**，
因此即使不装 PySide6 也能跑通全部单元测试。

### 1.3 运行

```powershell
python main.py
```

### 1.4 在 PyCharm 中运行

1. `File → Open` 打开 `EveryDay` 目录
2. `Settings → Project → Python Interpreter` 新建虚拟环境并安装 `requirements.txt`
3. 右键 `main.py → Run 'main'`
4. （推荐）`Run → Edit Configurations` 把工作目录设为项目根目录

命令行参数：

| 参数 | 作用 |
|---|---|
| `--minimized` | 静默启动，只驻留托盘与悬浮窗（开机自启使用） |
| `--data-dir <路径>` | 覆盖数据目录（调试用，不影响真实数据） |
| `--log-level DEBUG` | 覆盖日志级别 |
| `--version` | 显示版本号 |

### 1.5 直接运行可执行文件（免装 Python）

`release/EveryDay.exe` 是自包含的单文件程序（**81.1 MB**），双击即用，
目标机器**不需要安装 Python 或任何依赖**：

```powershell
.\release\EveryDay.exe                 # 正常启动
.\release\EveryDay.exe --minimized     # 只驻留托盘与悬浮窗
.\release\EveryDay.exe --data-dir D:\everyday-data   # 换数据目录
```

首次启动会把自己解包到临时目录，因此比源码运行慢 1~3 秒，之后走缓存会快一些。
数据仍然写在 `%LOCALAPPDATA%\EveryDay`。

重新打包：

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
# 启动更快的目录模式（约 168 MB）：
powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1 -Onedir -Clean
```

脚本会在打包**之前净化 PATH**、在打包**之后真的启动一次产物做自检**
（日志出现「初始化完成，进入事件循环」才算通过）。

> 为什么必须净化 PATH：PyInstaller 会顺着 PATH 收集被依赖的 DLL。
> 开发机上若装了 PostgreSQL / Anaconda 这类自带 OpenSSL 与 ICU 的软件，
> 它们的 DLL 会被收进产物，其中旧版 `icuuc.dll` 会抢占 Qt 的 ICU 绑定，
> 产物运行时报 `ImportError: DLL load failed while importing QtCore`。
> 这个污染**时有时无**，所以脚本同时加了产物自检来兜住。
> 详细排查记录见 [docs/known-issues.md](docs/known-issues.md) 第二节。

### 1.6 跑测试

```powershell
python -m pytest -q
python -m ruff check .
```

### 1.7 验证与自检工具

仓库自带几个零依赖的检查脚本（只用标准库 + PySide6）：

| 命令 | 作用 |
|---|---|
| `python -m pytest -q` | 236 项单元测试 + UI 冒烟 + 端到端启动集成 |
| `python tools/coverage_report.py` | 覆盖率备选方案（基于标准库 `trace`，无需 `pytest-cov`） |
| `python tools/smoke_startup.py` | 真实启动一次应用并核对落盘产物，可加 `--minimized` / `--data-dir` |
| `python tools/make_icon.py` | 用界面图标生成 exe 用的 `.ico`（构建时调用） |
| `powershell -File tools\build_exe.ps1` | 打包 + 产物自检 |

当前实测结果（`pytest` 9.1.1 + `pytest-cov` 7.1.0 + `ruff` 0.17.0）：

| 项目 | 结果 |
|---|---|
| 测试 | **236 通过 + 2 项平台跳过**，0 失败 |
| 核心服务覆盖率 | **85%**（要求 ≥ 60%） |
| 静态检查 | **`ruff check` 0 警告** |
| 端到端冒烟 | 12/12 与 14/14 项通过，退出码 0 |
| 真实窗口启动 | ✅ 桌面实测成功，stderr 无 Qt 警告 |
| 常驻内存 | ⚠️ 129 MB（未达文档 <50MB，Qt6 基线本身即 36MB） |

详见 [docs/acceptance-report.md](docs/acceptance-report.md)。

> **依赖请用 `PySide6-Essentials` 而不是 `PySide6` 元包**：元包会连带下载
> `PySide6-WebEngine`（131.7 MB 的 Chromium）等本应用完全用不到的东西，
> 安装体积从约 82 MB 涨到 200+ MB。导入的包名仍是 `PySide6`，代码无需改动。

---

## 2. 功能一览

| 编号 | 功能 | 说明 |
|---|---|---|
| F1 | 开机自启 | 写入当前用户注册表 `Run` 项，无需管理员权限 |
| F2 | 悬浮窗 | 无边框、半透明、始终置顶、不进任务栏；可拖动，位置被记忆；跨屏失效时自动回主屏 |
| F3 | 自定义任务 | 新建 / 编辑 / 删除，标题必填且 ≤100 字符 |
| F4 | 完成状态 | 勾选即持久化；单次任务完成后隐藏，循环任务次日自动重置 |
| F5 | 循环 / 单次 | 单次 / 每日 / 每周 / 每月；跨午夜由定时器自动校对 |
| F6 | 实时日历 | 读取本地 `.ics` 文件或网络订阅地址，展示今日日程 |
| F7 | 预设任务 | 内置 10 个模板，支持一键盘创建与增删改 |

其它：系统托盘、数据库启动备份（保留 5 份）、按日滚动日志（保留 7 天）、
三层全局异常捕获、WAL 模式。

---

## 3. 目录结构

```
EveryDay/
├── main.py                     启动入口（PyCharm 运行这个文件）
├── requirements.txt            运行期依赖
├── requirements-dev.txt        开发/测试依赖
├── pyproject.toml              打包与 pytest / coverage / ruff 配置
├── config/
│   └── appsettings.example.json  配置模板（字段说明 + 首启播种）
├── migrations/
│   └── 001_initial.sql         建表语句 + 默认模板数据
├── docs/
│   ├── architecture.md         架构说明（类图 / 时序图 / ER 图）
│   ├── acceptance-report.md    验收报告
│   ├── known-issues.md         已知问题（诚实清单）
│   └── final-delivery.md       交付索引
├── src/everyday/
│   ├── app.py                  启动装配（日志→库→迁移→备份→服务→界面）
│   ├── paths.py                路径唯一真相来源
│   ├── logging_setup.py        Serilog 对应物：按日滚动日志
│   ├── exceptions.py           三层异常捕获
│   ├── timeutils.py            日期时间序列化
│   ├── models/                 TaskItem / PresetTemplate / AppSettings
│   ├── db/                     SQLite 连接、幂等迁移、三个仓储
│   ├── services/               任务 / 日历 / ICS 解析 / 自启 / 预设 / 备份 / 设置
│   └── ui/                     PySide6 窗口、对话框、托盘、图标（代码绘制）
└── tests/                      单元测试（内存 SQLite，无 Mock 数据库）
```

### 分层与依赖方向

```
ui  ──▶  services  ──▶  db  ──▶  models
 │           │
 └───────────┴──▶  paths / timeutils / logging_setup / exceptions
```

`ui` 是唯一依赖 Qt 的包。服务层不依赖表示层，因此业务规则可以在无 GUI
环境下完整测试。

---

## 4. 数据存放位置

数据与程序目录分离，升级程序不会丢数据（文档第八章）：

```
%LOCALAPPDATA%\EveryDay\
├── everyday.db               SQLite 数据库（WAL 模式）
├── appsettings.json          运行期配置
├── backups\                  启动备份，保留最近 5 份（everyday-YYYY-MM-DD.db）
└── logs\                     按日滚动日志，保留 7 天（everyday.log）
```

在「设置 → 数据与日志」里可以直接打开这两个目录，或把配置导出成 JSON。
调试时用 `--data-dir` 指向别处即可，不会碰到真实数据。

---

## 5. 配置项说明

运行期配置的**唯一真相来源是数据库**（`app_settings` 表）。
`config/appsettings.example.json` 有两个用途：首次运行时播种、以及作为字段文档。

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `auto_start` | bool | `false` | 开机自启，写入 `HKCU\...\CurrentVersion\Run` |
| `float_window_visible` | bool | `true` | 启动后显示悬浮窗 |
| `float_window_left` / `float_window_top` | number | `60` | 悬浮窗位置（像素） |
| `calendar_ics_path` | string | `""` | 本地路径 / `file://` / `https://` 订阅地址 |
| `calendar_refresh_minutes` | int | `30` | 日历自动刷新间隔 |
| `log_level` | string | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `startup_backup_enabled` | bool | `true` | 启动时备份数据库 |

---

## 6. 常见问题

**Q：托盘图标不见了？**
Windows 会把新图标收进「隐藏的图标」溢出区，拖到任务栏即可常驻。

**Q：关闭主窗口后程序还在跑？**
这是设计行为（文档第七章）。关闭 = 最小化到托盘，退出请用托盘菜单「退出应用」。

**Q：日历订阅地址加载失败？**
会保留上一次成功的数据并在界面提示，不会清空日程。检查地址是否可直接返回
`.ics` 内容（有些服务返回的是网页而非 ICS）。

**Q：开机自启没生效？**
任务管理器的「启动」页可能会禁用本项。另外自启是用
`pythonw.exe main.py --minimized` 实现的，若移动了项目目录需在设置里重新勾选一次。

**Q：循环任务第二天没重置？**
重置在三种时机触发：应用启动时、当天首次刷新列表时、以及运行期间每小时一次
的日期变更检查（文档 5.5）。若应用一直没运行，下次启动时会补做。

---

## 7. 开发约定

* 私有成员 `_camelCase`，公共成员 `snake_case`（PEP 8）
* 所有公共 API 必须有中文文档字符串
* 禁止吞异常：最低限度也要 `logger.warning`
* 禁止在 UI 线程做阻塞 IO；数据库访问集中在 `db` 层
* 数据库访问必须是参数化查询，禁止字符串拼接 SQL
* 日志禁止写入敏感信息（日历订阅地址可能含 token，注意只在 DEBUG 级别记录）
* 新增第三方依赖前先写 ADR

---

## 8. 许可

[MIT](LICENSE)

---

English docs: [README.en.md](README.en.md)

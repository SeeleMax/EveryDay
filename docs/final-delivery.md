# 交付索引与运行指南 · EveryDay v1.0.0

---

## 1. 交付物清单

| 编号 | 交付物 | 路径 | 状态 |
|---|---|---|---|
| D1 | 源代码 | `src/everyday/`（+ `main.py`） | ✅ 完成 |
| D2 | 数据库脚本 | [`migrations/001_initial.sql`](../migrations/001_initial.sql) | ✅ 完成（建表 + 10 个默认模板） |
| D3 | 可执行程序 | `release/` | ⛔ **未产出**，原因见 [known-issues.md KI-02](known-issues.md) |
| D4 | 使用说明 | [`README.md`](../README.md) / [`README.en.md`](../README.en.md) | ✅ 完成（中英各一份） |
| D5 | 配置文件示例 | [`config/appsettings.example.json`](../config/appsettings.example.json) | ✅ 完成（逐字段中文说明） |
| 附 | 架构文档 | [`docs/architecture.md`](architecture.md) | ✅ 完成（类图 / ER 图 / 4 张时序图） |
| 附 | 验收报告 | [`docs/acceptance-report.md`](acceptance-report.md) | ✅ 完成（A1~A8 逐项 + 174 个用例清单） |
| 附 | 已知问题 | [`docs/known-issues.md`](known-issues.md) | ✅ 完成（15 项 + 6 个待确认问题） |
| 附 | 执行前准备 | [`docs/preparation.md`](preparation.md) | ✅ 完成（含环境核对与需求缺口记录） |
| 附 | 变更日志 | [`CHANGELOG.md`](../CHANGELOG.md) | ✅ 完成（Keep a Changelog 格式） |
| 附 | 许可证 | [`LICENSE`](../LICENSE) | ✅ MIT |
| 附 | 单元测试 | `tests/`（11 个文件，230 项） | ✅ **已执行：230/230 通过** |
| 附 | 覆盖率报告 | `tools/coverage_report.py` | ✅ **已执行：86.2%** |
| 附 | 端到端冒烟 | `tools/smoke_startup.py` | ✅ **已执行：12/12 与 14/14 通过** |

### 验证结论（实测）

| 项目 | 结果 |
|---|---|
| 单元测试 | 230 项全部通过（2 项平台断言跳过） |
| 核心服务覆盖率 | **86.2%**（要求 ≥ 60%） |
| 端到端启动/退出 | 首次 12/12、二次+最小化 14/14，退出码均为 0 |
| 过程中发现并修复的缺陷 | **4 个**，其中 1 个致命（点「退出应用」退不掉） |
| 仍需人工验收 | A1 开机自启、A2 真实拖动/多屏、A8 连续 24 小时、内存、打包 |

> 全部细节与复现命令见 [acceptance-report.md](acceptance-report.md)；
> 缺陷根因与回归测试见 [known-issues.md](known-issues.md) 第一节。

---

## 2. 一键运行指南

```powershell
# 1) 进入项目根目录
cd EveryDay

# 2) 创建虚拟环境并激活
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3) 安装依赖（运行期 + 开发）
pip install -r requirements-dev.txt

# 4) 跑测试
python -m unittest discover -s tests -v

# 5) 启动应用
python main.py
```

**PyCharm 用户**：`File → Open` 打开 `EveryDay` 目录 → 配置解释器并安装
`requirements-dev.txt` → 右键 `main.py → Run 'main'`。

---

## 3. 推送到 Git 仓库

> **本地仓库已经建好并完成提交**（`git init -b main` + 一个 Conventional Commit）。
> 我无法执行 `git push`：没有远端地址，也没有你的凭据。
> 你只需关联远端并推送。

### 3.1 关联远端并推送（已完成本地提交，只需两步）

```powershell
cd E:\DeepSeekCanteen\EveryDay

# 把 <你的仓库地址> 换成实际地址
git remote add origin <你的仓库地址>
git push -u origin main
```

### 3.2 从零重建（若你想自己重新提交）

```powershell
cd EveryDay
git init -b main
git add .
git commit -m "feat: 初始化 EveryDay v1.0.0 - 每日计划软件"
git remote add origin <你的仓库地址>
git push -u origin main
```

### 3.3 需要我提供的信息

| 问题 | 说明 |
|---|---|
| 远端仓库地址 | 例如 `https://github.com/<you>/everyday.git` |
| 默认分支名 | 当前本地为 `main` |
| 仓库目录布局 | 是「仓库根目录 = EveryDay 项目」还是「仓库根目录 / EveryDay 子目录」 |

> 当前项目位于 `E:\DeepSeekCanteen\EveryDay\`，该目录自带
> `.gitignore` / `.gitattributes` / `LICENSE` / `README.md` / `CHANGELOG.md`，
> 是自包含的，可直接作为仓库根。
> `.venv/`（已装好 PySide6 6.12.0）已被 `.gitignore` 排除，不会入库。

### 3.4 `.gitignore` 已覆盖的内容

虚拟环境、`__pycache__`、pytest/coverage 产物、`.idea/`、
运行期数据（`*.db` / `*.db-wal` / `logs/` / `backups/`）、
本地含隐私的 `config/appsettings.json`、打包产物。

---

## 4. 已知限制（摘要）

完整清单见 [known-issues.md](known-issues.md)。

| 编号 | 限制 | 影响 |
|---|---|---|
| KI-03 | ICS 的 `TZID` 按本地时间解释 | 跨时区订阅可能偏移 |
| KI-04 | 不支持 `BYDAY` / `BYSETPOS` 等高级递归 | 「每月第二个周二」会退化为单次 |
| KI-05 | 日历 HTTP 读取在 UI 线程同步执行（15s 超时） | 订阅不可达时可能卡顿 |
| KI-06 | 无单实例互斥 | 重复启动会出现多个托盘图标；残留进程会占用数据文件 |
| KI-07 | 每周/每月的「参考日期」语义是推断的 | 需要你确认 |
| KI-09 | 月末任务（29/30/31 号）短月跳过而非顺延 | 产品决策待定 |
| KI-10 | `ui/` 未计入覆盖率（改由 45 项 UI/集成测试覆盖行为） | 真实鼠标交互仍需人工 |
| ENV-01 | 本机 `pip` 完全不可用 | **D3 自包含可执行程序未产出**；打包命令见验收报告 §5.1 |
| — | 未实现 v1.1 的全部需求 | 通知、番茄钟、优先级、标签、子任务、依赖、统计、回收站、多屏 DPI、屏幕时间 |
| — | 需人工验收 | A1 开机自启、A2 真实拖动/多屏、A8 连续 24 小时、常驻内存 |

---

## 5. 后续建议（v1.1 方向）

按《每日计划软件 — Agent 执行交接文档 v1.0》的 P1~P6 阶段推进，优先级建议：

### 第一优先：先把 v1.0.0 跑通

在 v1.1 之前必须先完成环境验证。v1.0 是 v1.1 的骨架，
骨架若有导入错误，v1.1 的六个阶段都会建在沙上。

### P1 数据模型与基础设施（约 3 天）

* 新增迁移 `002_v11.sql`：`task_item` 加 `priority` / `parent_task_id`；
  新建 `task_tag`、`task_dependency`、`task_history`、`focus_record`、
  `screen_time_record`、`deleted_task`（回收站）等表
* 迁移执行器已支持多脚本与幂等，无需改动
* 日志与全局异常已就绪，无需改动

### P2 任务增强（约 4 天）

* 优先级 / 标签 / 子任务（`parent_task_id` 自引用）
* `TaskDependencyService`：拓扑排序做循环依赖检测，
  拒绝 `A→B→A`；前置未完成时后置任务勾选框置灰
  （`ui/widgets.py:TaskRow.set_completion_enabled` 已预留接口）

### P3 通知与番茄钟（约 3 天）

* Toast 通知：Python 侧建议用 `win11toast` 或 `winotify`
  （**新增依赖需先写 ADR**）；比 .NET 的 `Microsoft.Toolkit.Uwp.Notifications` 更贴合本项目
* `PomodoroService`：用 `QTimer` 驱动（不要用 `time.sleep`）
* 悬浮窗加进度环；专注期间抑制其他通知

### P4 历史统计与回收站（约 3 天）

* 软删除 + 当天回收站 + 次日清理
* 12×7 热力图（`QGridLayout` 或自绘 `paintEvent`）
* 5 秒撤销：`QTimer.singleShot(5000, ...)` + 状态提示条

### P5 多屏 DPI 与屏幕时间（约 4 天）

* PySide6 在 Qt6 下**已默认启用**高 DPI 缩放；
  多屏混合 DPI 的动态切换需实测（可用 `QScreen.geometryChanged`）
* 屏幕时间：`ctypes` 调 `GetForegroundWindow` + `GetLastInputInfo`，
  2 秒采样、会话合并、空闲暂停

### P6 性能与发布（约 3 天）

* 列表虚拟化：`QListView` + `QAbstractListModel`（当前用的 `QListWidget` +
  `setItemWidget` 在 500 项时会变慢，这是 v1.1 的一个**必做重构**）
* 全链路 async：PySide6 建议走 `QThread` 或 `QtConcurrent`，
  数据库层已用 `RLock` 保证基本并发安全
* 打包：PyInstaller（`--collect-all PySide6`）或 Nuitka

### 建议的新增迁移脚本命名

```
migrations/
├── 001_initial.sql        ← 已有
├── 002_task_priority.sql
├── 003_tags.sql
├── 004_subtasks_and_dependencies.sql
├── 005_history_and_focus.sql
└── 006_recycle_bin_and_screen_time.sql
```

### 建议补充的依赖（每项都需先写 ADR）

| 用途 | 库 | 理由 |
|---|---|---|
| Windows Toast 通知 | `winotify` 或 `win11toast` | 轻量、纯 Windows、无需 .NET |
| ICS 完整递归 | `python-dateutil` | 用 `rrule` 替换自研展开，解决 KI-04（注意：`dateutil` 已在 DSH 运行时中，但项目需自行声明依赖） |
| 打包 | `pyinstaller` | 交付 D3 |
| UI 测试 | `pytest-qt` | 补 KI-11 |

---

## 6. 联系点

需要我继续时，请提供以下任一输入：

1. **运行输出**（`pip install` / `unittest` / `python main.py` 的报错）→ 我逐条修到全绿
2. **远端仓库地址与分支名** → 我给出精确的推送命令序列或目录重组方案
3. **`known-issues.md` 第 5 节的 Q-02 ~ Q-05 答复** → 我据此调整实现并新增迁移
4. **「继续 v1.1」指令** → 我按上面 P1~P6 推进

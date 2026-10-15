# 执行前准备报告 · EveryDay

- 项目：EveryDay（每日计划软件）
- 对应文档：《每日计划软件 V1 技术实现文档》
- 报告状态：v1.0.0 交付时更新（含技术栈变更说明）

---

## 1. 环境核对结果

> 首次核对时命令行完全不可用（`0xC0000142`），因此当时全部项目都无法验证。
> 用户放开沙箱策略后命令行恢复，下表为**恢复后重新核对**的结果。

| 检查项 | 要求 | 实测 | 结论 |
|---|---|---|---|
| 命令行可用性 | 能执行构建/测试 | 恢复后正常（Windows PowerShell 5.1） | ✅ 可用 |
| 沙箱恢复前的失败 | — | `pwsh` 6 次调用全部 `0xC0000142`（`STATUS_DLL_INIT_FAILED`），连 `echo hello` 都无输出 | 已随沙箱策略变更而消失 |
| Python 运行时 | 3.9+ | 3.12.14（DSH 运行时）、3.13.5（Anaconda / Python313） | ✅ 可用 |
| GUI 库 | PySide6 | **已安装 6.12.0**（装入 `.venv`） | ✅ 可用 |
| Git | 可推送 | **已安装 2.51.0**，位于 `C:\Program Files\Git\cmd\git.exe`（不在 PATH 上） | ✅ 可用（推送仍需远端地址） |
| `pip` | 可安装依赖 | ⛔ **完全不可用**：venv 的 pip 因 `CSIDL_COMMON_APPDATA` 注册表查询失败崩溃；Anaconda 的 pip 因 `distlib/w32.exe` 损坏报 `OSError: [Errno 22]`。`PIP_CONFIG_FILE=nul`、`--isolated` 均无效 | ⛔ 不可用 |
| 打包工具 | PyInstaller | 未安装，且因 pip 不可用而装不上 | ⛔ 无法产出 D3 |

### 1.1 命令行不可用期间的原始证据（已过时，留存备查）

- 6 次 `pwsh` 调用（含 `Get-Location`、`echo hello`、`$PSVersionTable.PSVersion`、
  `dotnet --version`、工作区列表与 `dotnet --info`）
  均返回 `(no output)` + `[exit code: 3221225794]`
- `3221225794` = `0xC0000142` = `STATUS_DLL_INIT_FAILED`
- 失败发生在 PowerShell 进程自身初始化阶段，**不是**命令写错、不是超时、
  不是文件沙箱 ACL 拒绝（ACL 拒绝会返回明确的 `[sandbox: file access denied]`）
- 文件写入通路当时就是正常的，因此问题范围被精确限定为「**构建与执行**不可用」

恢复后我做的验证：**230 项单元测试通过，核心服务覆盖率 86.2%，端到端启动/退出冒烟
12/12 与 14/14 通过**，并借此发现并修复了 4 个真实缺陷（含 1 个致命的退出失效）。
详见 [acceptance-report.md](acceptance-report.md)。

---

## 2. 技术栈变更说明（重要）

《V1 技术实现文档》2.1 节指定的是 **WPF + .NET 8 + C# 12**。
执行时收到的指令是「**先实现 v1，使用 PyCharm**」，即改为 **Python**。
因此在保持同构的前提下做了如下映射：

| 文档选型 | 本项目选型 | 理由 |
|---|---|---|
| WPF + .NET 8 | **PySide6 (Qt6)** | 文档 F2 要求的「无边框 / 透明 / 始终置顶 / 不占任务栏 / 可拖动」与第七章的系统托盘，在 Python 生态里只有 Qt 能全部原生支持；Qt 也是 WPF 最贴近的对应物 |
| C# 12 + Nullable | Python + `from __future__ import annotations` + 类型注解 | 保持静态可分析性 |
| SQLite | **SQLite（`sqlite3` 标准库）** | 一致 |
| `Microsoft.Data.Sqlite` | `sqlite3` + 手写仓储 | 标准库即可，零依赖 |
| MVVM + CommunityToolkit.Mvvm | **Qt 信号槽 + `AppContext` 信号总线** | 同为「UI 与逻辑解耦」，`AppContext` 承担 ViewModel 的协调职责 |
| H.NotifyIcon.Wpf | `QSystemTrayIcon` | 一致 |
| Ical.Net（ICS 解析） | **自研 ICS 解析器** | 只需「单次 + 常见循环」子集，且要按 7 天窗口按需展开；避免引入完整 RFC 实现 |
| Serilog（按日滚动） | `TimedRotatingFileHandler` | 行为一致：按日滚动、保留 7 天 |
| PerMonitorV2（app.manifest） | Qt6 默认高 DPI 缩放 | 无需清单文件；多屏混合 DPI 动态切换未验证（见 known-issues） |
| WiX / Velopack | PyInstaller（**未执行**） | 见 KI-02 |

**这一变更未事先取得书面确认**，因为环境不支持 .NET 且指令明确要求 PyCharm。
如与预期不符，请指出，我可以按 WPF 重新实现（但这需要可用的命令行）。

---

## 3. 代码基线核查

工作区 `E:\DeepSeekCanteen` 原始内容：

```
Git-64-bit.exe          ← Git for Windows 安装包残留物
gh_meta.json            ← GitHub API 响应残留物
github_public_key.txt   ← GitHub SSH 公钥残留物
rel.json                ← GitHub Releases API 响应残留物（tag v2.51.0.windows.1）
PortableGit.7z.exe      ← Git for Windows 便携版安装包残留物
```

* `**/*.csproj` / `**/*.py` 搜索结果：无
* 结论：**不存在任何 v1 代码基线**，本次为从零搭建

按《Agent 执行交接文档》2.2 节「若不存在 v1.0，Agent 必须先按 v1.0 需求搭建骨架」，
本项目即为该骨架，且已直接实现到 V1 的完整功能范围。

---

## 4. 需求覆盖确认

《V1 技术实现文档》全部功能点均已实现：

| 编号 | 功能 | 实现位置 | 状态 |
|---|---|---|---|
| F1 | 开机自启 | `services/startup_service.py`、`app.py:_reconcile_autostart` | ✅ |
| F2 | 悬浮窗 | `ui/float_window.py` | ✅ |
| F3 | 自定义任务 | `ui/main_window.py`、`ui/task_dialog.py`、`services/task_service.py` | ✅ |
| F4 | 完成状态 | `services/task_service.py:set_completed` | ✅ |
| F5 | 循环 / 单次 | `models/task.py`、`services/task_service.py:rollover` | ✅ |
| F6 | 实时日历 | `services/ics_parser.py`、`services/calendar_service.py` | ✅ |
| F7 | 预设任务 | `ui/preset_dialog.py`、`services/preset_service.py` | ✅ |

文档第三章（架构）、第四章（数据模型）、第五章（功能实现说明）、
第六章（界面结构）、第七章（托盘）、第八章（存储与备份）、
第九章（异常与日志）均已逐条落实，对照表见
[acceptance-report.md](acceptance-report.md)。

---

## 5. 未获答复的问题

这些问题在执行过程中未获答复，我按合理默认值推进，并记入
[known-issues.md](known-issues.md)：

| 编号 | 问题 | 我采用的默认 |
|---|---|---|
| Q-01 | 构建/测试环境的修复方式 | 已随沙箱策略变更解决，并完成了真实验证 |
| Q-02 | 循环任务的「每周/每月固定日期」用哪个字段表达 | 复用文档已有的 `due_date` 作为锚点，不新增字段 |
| Q-03 | 打包工具（WiX 还是 Velopack） | 未打包；建议 PyInstaller（本机 pip 不可用，命令已写入验收报告 §5.1） |
| Q-04 | 是否手工执行 GUI / 性能类验收 | acceptance-report 中如实区分「已自动验证」与「待人工验证」 |

---

## 6. 下一步

1. **v1.0.0 的代码级验收已完成**（230 项测试、86.2% 覆盖率、端到端冒烟全绿）
2. 请在真机上完成 acceptance-report §5 列出的人工项：
   开机自启、真实拖动与多屏、连续 24 小时、常驻内存、打包
3. 答复 [known-issues.md](known-issues.md) 第六节的 Q-01 ~ Q-05
4. 提供远端仓库地址与分支名即可推送（本地仓库与提交已就绪）
5. 确认是否进入 v1.1（P1~P6 阶段）

"""端到端启动冒烟检查。

真实执行 :func:`everyday.app.main` 的完整启动流程（日志 → 数据库 → 迁移 →
备份 → 服务 → 界面 → 托盘 → 事件循环），然后让事件循环在若干毫秒后自动退出，
最后核对落盘产物。

用 Qt 的 ``offscreen`` 平台插件运行，因此不需要真实桌面会话：

    python tools/smoke_startup.py            # 正常模式
    python tools/smoke_startup.py --minimized  # 模拟开机自启

退出码 0 表示全部检查通过，1 表示有检查项失败。
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# 必须在导入 Qt 之前设置平台插件
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: 事件循环存活时长（毫秒），必须够长以便各定时器与初始刷新跑完
RUN_MILLISECONDS = 2500

#: 看门狗超时（秒）。超过就强制结束，避免在 CI 上永久挂住
WATCHDOG_SECONDS = 60

#: 运行期间自动消解掉的模态对话框标题（用于发现被吞掉的异常）
_dismissed_dialogs: list[str] = []


def _say(message: str) -> None:
    """打印带刷新的进度行，便于定位卡在哪一步。"""
    print(f"[smoke] {message}", flush=True)


def _install_headless_guards() -> None:
    """安装无头运行所需的三道守卫。

    1. **自动退出**：给 ``QApplication.exec`` 打补丁，到点后调用
       :meth:`everyday.app.Application._quit` —— 刻意走**产品真实的退出路径**，
       而不是直接 ``quit()``。这样本检查能覆盖「托盘退出应用」的功能。
    2. **消灭模态框**：``QMessageBox.exec`` 直接返回。异常处理器会弹模态框，
       而模态框会永久阻塞事件循环——这正是本检查第一次运行挂死的原因。
       这里改成「记录 + 立即关闭」，于是异常不再隐藏，而是出现在报告里
    3. **看门狗**：无论如何 N 秒后强制结束进程，并先打印所有线程的调用栈
    """
    import threading

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from everyday.app import Application

    holder: dict[str, object] = {}

    original_run = Application.run

    def capturing_run(self):  # noqa: ANN001, ANN202 - 补丁函数
        holder["app"] = self
        return original_run(self)

    Application.run = capturing_run

    original_exec = QApplication.exec

    def timed_exec(self):  # noqa: ANN001, ANN202 - 补丁函数
        # 注意：PySide6 的 QApplication.exec 是**静态方法**，
        # 不能把 self 传给它（`original_exec(self)` 会报
        # "QApplication.exec() takes no arguments (1 given)"）。
        def request_quit() -> None:
            application = holder.get("app")
            if application is not None:
                application._quit()  # noqa: SLF001 - 检查真实退出路径
            else:  # pragma: no cover - 兜底
                QApplication.instance().quit()

        QTimer.singleShot(RUN_MILLISECONDS, request_quit)
        return original_exec()

    QApplication.exec = timed_exec

    def dismiss(self) -> int:  # noqa: ANN001 - 补丁函数
        _dismissed_dialogs.append(self.text() or self.windowTitle() or "(无文本)")
        _say(f"捕获到模态提示框：{self.windowTitle()} / {self.text()}")
        return int(QMessageBox.Ok)

    QMessageBox.exec = dismiss

    def watchdog() -> None:
        # 先打出所有线程的调用栈，再强制结束——否则只能看到「挂了」，
        # 不知道挂在哪一行。
        import faulthandler

        _say(f"看门狗超时 {WATCHDOG_SECONDS}s，强制结束进程。调用栈：")
        faulthandler.dump_traceback(all_threads=True)
        os._exit(99)

    timer = threading.Timer(WATCHDOG_SECONDS, watchdog)
    timer.daemon = True
    timer.start()


def main() -> int:
    """执行启动冒烟检查。"""
    parser = argparse.ArgumentParser(description="EveryDay 启动冒烟检查")
    parser.add_argument("--minimized", action="store_true", help="模拟开机自启启动")
    parser.add_argument("--data-dir", default=None, help="数据目录，缺省用临时目录")
    args = parser.parse_args()

    temporary = None
    if args.data_dir:
        data_dir = Path(args.data_dir)
    else:
        temporary = tempfile.TemporaryDirectory()
        data_dir = Path(temporary.name) / "data"

    _install_headless_guards()

    from everyday.app import main as app_main

    argv = ["EveryDay", "--data-dir", str(data_dir), "--log-level", "DEBUG"]
    if args.minimized:
        argv.append("--minimized")

    _say(f"调用 everyday.app.main，数据目录={data_dir}")
    database_file = data_dir / "everyday.db"
    database_existed = database_file.is_file()
    _say(f"启动前数据库是否已存在：{database_existed}")
    exit_code = app_main(argv)
    _say(f"事件循环已退出，退出码={exit_code}")

    # ------------------------------------------------------------------
    # 核对落盘产物
    # ------------------------------------------------------------------
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks.append((name, bool(condition), detail))

    database_file = data_dir / "everyday.db"
    check("事件循环正常退出", exit_code == 0, f"退出码={exit_code}")
    check("数据目录已创建", data_dir.is_dir(), str(data_dir))
    check("数据库文件已创建", database_file.is_file(), str(database_file))

    log_file = data_dir / "logs" / "everyday.log"
    check("日志文件已创建", log_file.is_file(), str(log_file))
    log_text = log_file.read_text(encoding="utf-8", errors="replace") if log_file.is_file() else ""
    check("日志记录了启动", "启动中" in log_text)
    check("日志记录了进程退出", "已退出" in log_text)
    check("日志中无 ERROR", "ERROR" not in log_text and "Traceback" not in log_text,
          "见日志文件" if ("ERROR" in log_text or "Traceback" in log_text) else "")

    # 迁移结果：数据库里应当有 4 张表与 10 个默认模板
    import sqlite3

    tables: set[str] = set()
    preset_count = 0
    task_count = 0
    if database_file.is_file():
        connection = sqlite3.connect(str(database_file))
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            preset_count = connection.execute(
                "SELECT COUNT(*) FROM preset_template"
            ).fetchone()[0]
            task_count = connection.execute("SELECT COUNT(*) FROM task_item").fetchone()[0]
        finally:
            connection.close()

    expected_tables = {"task_item", "preset_template", "app_settings", "schema_migrations"}
    check("全部业务表已建好", expected_tables.issubset(tables), f"实际={sorted(tables)}")
    check("默认模板已播种", preset_count >= 10, f"模板数={preset_count}")
    check("任务表初始为空", task_count == 0, f"任务数={task_count}")
    check(
        "运行期间没有异常提示框",
        not _dismissed_dialogs,
        "; ".join(_dismissed_dialogs),
    )

    backups = (
        sorted((data_dir / "backups").glob("everyday-*.db"))
        if (data_dir / "backups").is_dir()
        else []
    )
    if database_existed:
        # 非首次运行：必须为「本次启动前已存在」的数据库生成备份（文档第八章）
        check("二次运行生成了启动备份", len(backups) == 1, f"发现={[p.name for p in backups]}")
        check(
            "备份文件按日期命名",
            bool(backups) and backups[0].name.startswith("everyday-")
            and backups[0].suffix == ".db",
            backups[0].name if backups else "",
        )
        if backups:
            connection = sqlite3.connect(str(backups[0]))
            try:
                backup_presets = connection.execute(
                    "SELECT COUNT(*) FROM preset_template"
                ).fetchone()[0]
            finally:
                connection.close()
            check("备份内容包含已迁移的数据", backup_presets >= 10, f"模板数={backup_presets}")
    else:
        # 首次运行：库文件原本不存在，不应产生（空库）备份
        check("首次运行不生成空备份", backups == [], f"发现={[p.name for p in backups]}")

    # ------------------------------------------------------------------
    report_lines = ["", "=" * 62, "启动冒烟检查结果", "=" * 62]
    failed = 0
    for name, passed, detail in checks:
        mark = "PASS" if passed else "FAIL"
        if not passed:
            failed += 1
        suffix = f"  ({detail})" if detail else ""
        report_lines.append(f"[{mark}] {name}{suffix}")
    report_lines.append("-" * 62)
    report_lines.append(f"共 {len(checks)} 项，失败 {failed} 项")
    report_lines.append(f"数据目录：{data_dir}")
    report_lines.append("=" * 62)
    # 必须 flush：看门狗用 os._exit 强制退出时不会刷新缓冲，
    # 否则报告会凭空消失，看起来像是「检查没跑完」。
    print("\n".join(report_lines), flush=True)

    if not args.data_dir:
        print("（临时目录，检查完成后即删除；加 --data-dir 可保留现场）")

    if temporary is not None:
        # 必须先关掉日志 handler：Windows 上被占用的日志文件无法删除，
        # 而清理失败抛出的异常会被全局处理器转成模态提示框，进而挂住进程。
        import logging

        logging.shutdown()
        temporary.cleanup()

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

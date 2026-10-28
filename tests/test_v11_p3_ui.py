"""v1.1 P3 界面层测试：托盘通知端口、进度环、番茄钟控制器。

重点验证**定时器生命周期**：只在真正走秒时运行，暂停/结束/退出都要停表。
这是 P6「计时器生命周期管理」的前置要求，现在不测，后面必然返工。
"""

from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from everyday.db import FocusRepository
from everyday.services.notification import Notification
from everyday.services.pomodoro_service import PomodoroService, PomodoroState
from everyday.ui.notifier import TrayNotifier
from everyday.ui.pomodoro_controller import PomodoroController
from everyday.ui.progress_ring import ProgressRing
from tests.base import DatabaseTestCase


class _FakeTray:
    """只记录 ``notify`` 调用的托盘替身。"""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.snapshots: list = []

    def notify(self, title: str, message: str) -> None:
        self.messages.append((title, message))

    def sync_pomodoro(self, snapshot) -> None:
        self.snapshots.append(snapshot)


class TrayNotifierTest(unittest.TestCase):
    def test_without_tray_returns_false(self):
        notifier = TrayNotifier(None)
        self.assertFalse(notifier.notify(Notification(title="t", message="m")))

    def test_with_tray_delegates(self):
        tray = _FakeTray()
        notifier = TrayNotifier(tray)
        self.assertTrue(notifier.notify(Notification(title="该吃药了", message="8:00")))
        self.assertEqual(tray.messages, [("该吃药了", "8:00")])

    def test_suppressed_does_not_delegate(self):
        tray = _FakeTray()
        notifier = TrayNotifier(tray)
        notifier.set_suppressed(True)
        self.assertFalse(notifier.notify(Notification(title="t", message="m")))
        self.assertEqual(tray.messages, [])

    def test_tray_errors_are_swallowed(self):
        class Boom:
            def notify(self, title, message):
                raise RuntimeError("托盘炸了")

        notifier = TrayNotifier(Boom())
        self.assertFalse(notifier.notify(Notification(title="t", message="m")))


class ProgressRingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_ratio_is_stored_and_clamped(self):
        ring = ProgressRing()
        ring.set_progress(0.25, "18:45")
        self.assertAlmostEqual(ring.ratio, 0.25)

        ring.set_progress(1.5, "")
        self.assertEqual(ring.ratio, 1.0)

        ring.set_progress(-0.3, "")
        self.assertEqual(ring.ratio, 0.0)

    def test_render_does_not_crash(self):
        ring = ProgressRing()
        ring.resize(34, 34)
        for ratio in (0.0, 0.33, 0.99, 1.0):
            ring.set_progress(ratio, "10:00")
            image = ring.grab().toImage()
            self.assertFalse(image.isNull(), f"ratio={ratio} 时渲染失败")


class PomodoroControllerTest(DatabaseTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        super().setUp()
        self.focus_repo = FocusRepository(self.db)
        self.service = PomodoroService(self.focus_repo, default_minutes=25, break_minutes=5)
        self.tray = _FakeTray()
        from everyday.services.notification import RecordingNotifier

        self.notifier = RecordingNotifier()
        self.controller = PomodoroController(
            self.service, tray=self.tray, notifier=self.notifier
        )

    def tearDown(self) -> None:
        # 必须在关库**之前**停控制器：shutdown 会把进行中的专注落库，
        # 关库后再写就是「表不存在」。用 addCleanup 会晚一步，踩过这个坑。
        try:
            self.controller.shutdown()
        finally:
            super().tearDown()

    def test_timer_runs_only_while_focusing(self):
        self.assertFalse(self.controller.timer_active, "空闲时不该走秒")

        self.controller.start()
        self.assertTrue(self.controller.timer_active)

        self.controller.pause()
        self.assertFalse(self.controller.timer_active, "暂停必须停表")

        self.controller.resume()
        self.assertTrue(self.controller.timer_active)

    def test_timer_stops_when_focus_finishes(self):
        self.controller.start(minutes=1)
        self.assertTrue(self.controller.timer_active)

        # 直接推进到终点（等价于 60 次 1 秒 tick）
        self.service.tick(60)

        self.assertIs(self.service.state, PomodoroState.FINISHED)
        self.assertFalse(self.controller.timer_active, "结束后必须停表")
        self.assertEqual(self.notifier.titles(), ["专注完成"])

    def test_stop_reports_actual_minutes(self):
        self.controller.start(minutes=25)
        self.service.tick(600)
        self.controller.stop()

        self.assertFalse(self.controller.timer_active)
        self.assertEqual(self.notifier.titles(), ["专注已结束"])
        self.assertIn("10.0 分钟", self.notifier.sent[0].message)

    def test_tray_snapshot_is_synced(self):
        self.controller.start(minutes=10)
        self.assertTrue(self.tray.snapshots, "启动后应同步一次托盘状态")
        self.assertIs(self.tray.snapshots[-1].state, PomodoroState.FOCUSING)

    def test_break_keeps_timer_running_then_stops(self):
        self.controller.start(minutes=1)
        self.service.tick(60)
        self.controller.start_break()

        self.assertTrue(self.controller.timer_active, "休息时也要走秒")
        self.service.tick_break(5 * 60)
        self.controller._sync_timer()
        self.assertFalse(self.controller.timer_active, "休息结束回到空闲应停表")

    def test_shutdown_stops_timer_and_closes_record(self):
        self.controller.start(minutes=25)
        record_id = self.service.snapshot().record_id
        self.service.tick(120)

        self.controller.shutdown()
        self.assertFalse(self.controller.timer_active)

        record = self.focus_repo.get(record_id)
        self.assertIsNotNone(record)
        self.assertEqual(record.actual_seconds, 120, "退出前要把已专注时长落库")
        self.assertFalse(record.completed)

    def test_invalid_commands_are_logged_not_raised(self):
        # 空闲时调暂停/继续/结束都不该抛异常（托盘菜单可能被误点）
        self.controller.pause()
        self.controller.resume()
        self.controller.stop()
        self.controller.start_break()
        self.assertIs(self.service.state, PomodoroState.IDLE)


if __name__ == "__main__":
    unittest.main()

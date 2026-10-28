"""番茄钟界面控制器（v1.1 P3）。

把 :class:`PomodoroService`（纯逻辑）接到界面上的三样东西：

* **1 秒定时器**：只在真正需要走秒时运行
  （专注中 / 休息中）。空闲、暂停、已完成时**停掉定时器**——
  这是 P6「计时器生命周期管理」要求的最小实现，避免空转耗电与
  长时间运行后的定时器漂移。
* **托盘菜单**：开始 / 暂停 / 继续 / 结束，以及状态文案。
* **悬浮窗进度环**：比例与剩余时间。

把它单独放一个类的原因：定时器的启停与界面元素同步写在窗口里很容易
漏掉一条分支（比如「暂停时要停表」），集中在一处才能保证状态机、
定时器、界面三者永远一致。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal

from everyday.services.notification import Notification, NotificationPort
from everyday.services.pomodoro_service import PomodoroService, PomodoroState

logger = logging.getLogger(__name__)

#: 走秒间隔（毫秒）
TICK_INTERVAL_MS = 1000


class PomodoroController(QObject):
    """把番茄钟服务接到托盘与悬浮窗。"""

    #: 状态变化（界面可据此刷新其它部分，例如主窗口统计）
    changed = Signal()

    def __init__(
        self,
        service: PomodoroService,
        *,
        tray=None,
        float_window=None,
        notifier: NotificationPort | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._tray = tray
        self._float_window = float_window
        self._notifier = notifier
        self._paused_for_break = False

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)

        service.on_finished = self._on_finished
        self._refresh()

    # ------------------------------------------------------------------
    @property
    def service(self) -> PomodoroService:
        """底层的番茄钟服务（测试与状态查询用）。"""
        return self._service

    @property
    def timer_active(self) -> bool:
        """1 秒定时器当前是否在跑（生命周期测试用）。"""
        return self._timer.isActive()

    # ------------------------------------------------------------------
    # 用户命令
    # ------------------------------------------------------------------
    def start(self, task_id: int | None = None, minutes: int | None = None) -> None:
        """开始专注。"""
        try:
            self._service.start(task_id=task_id, minutes=minutes)
        except ValueError as exc:
            logger.warning("无法开始专注：%s", exc)
            return
        self._sync_timer()
        self._refresh()

    def pause(self) -> None:
        """暂停。"""
        try:
            self._service.pause()
        except ValueError as exc:
            logger.warning("无法暂停：%s", exc)
            return
        self._sync_timer()  # 暂停要停表
        self._refresh()

    def resume(self) -> None:
        """继续。"""
        try:
            self._service.resume()
        except ValueError as exc:
            logger.warning("无法继续：%s", exc)
            return
        self._sync_timer()
        self._refresh()

    def stop(self) -> None:
        """提前结束（已专注的时间照样记入统计）。"""
        try:
            record = self._service.stop()
        except ValueError as exc:
            logger.warning("无法结束专注：%s", exc)
            return
        self._sync_timer()
        self._refresh()
        if record is not None and self._notifier is not None:
            self._notifier.notify(
                Notification(
                    title="专注已结束",
                    message=f"本次专注 {record.actual_minutes:.1f} 分钟",
                    kind="focus",
                )
            )

    def start_break(self) -> None:
        """开始休息。"""
        try:
            self._service.start_break()
        except ValueError as exc:
            logger.warning("无法开始休息：%s", exc)
            return
        self._sync_timer()
        self._refresh()

    def skip_break(self) -> None:
        """跳过休息。"""
        try:
            self._service.skip_break()
        except ValueError as exc:
            logger.warning("无法跳过休息：%s", exc)
            return
        self._sync_timer()
        self._refresh()

    # ------------------------------------------------------------------
    # 定时器
    # ------------------------------------------------------------------
    def _on_tick(self) -> None:
        """每秒推进一次。"""
        state = self._service.state
        if state is PomodoroState.BREAK:
            snapshot = self._service.tick_break(1)
            self._refresh(snapshot)
            return
        if state is not PomodoroState.FOCUSING:
            # 理论上不该走到这里（定时器只在跑秒时启动），
            # 但状态若被外部改动，这里兜住并停表。
            self._sync_timer()
            return
        self._service.tick(1)
        self._refresh()

    def _sync_timer(self) -> None:
        """按状态启停定时器——这是生命周期管理的核心。"""
        should_run = self._service.state in (PomodoroState.FOCUSING, PomodoroState.BREAK)
        if should_run and not self._timer.isActive():
            self._timer.start()
        elif not should_run and self._timer.isActive():
            self._timer.stop()

    def _on_finished(self, record) -> None:
        """一段专注结束（跑完或中止都会走到这里）。

        ⚠️ 只有 ``completed=True`` 才说「专注完成」。中止时由 :meth:`stop`
        自己弹「专注已结束」，否则用户会同时收到两条互相矛盾的通知
        ——这是测试抓出来的真实缺陷。
        """
        self._sync_timer()
        self._refresh()
        if not record.completed:
            return
        if self._notifier is not None:
            self._notifier.notify(
                Notification(
                    title="专注完成",
                    message=f"完成了 {record.planned_minutes} 分钟专注，休息一下吧",
                    kind="focus",
                )
            )

    # ------------------------------------------------------------------
    # 界面同步
    # ------------------------------------------------------------------
    def _refresh(self, snapshot=None) -> None:
        """把状态同步到托盘与悬浮窗。"""
        current = snapshot if snapshot is not None else self._service.snapshot()
        if self._tray is not None:
            try:
                self._tray.sync_pomodoro(current)
            except Exception:
                logger.exception("同步托盘番茄钟状态失败")
        if self._float_window is not None:
            try:
                self._float_window.set_pomodoro(current)
            except Exception:
                logger.exception("同步悬浮窗进度环失败")
        self.changed.emit()

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        """退出前停表，避免定时器在窗口销毁后仍被触发。"""
        if self._timer.isActive():
            self._timer.stop()
        if self._service.is_active:
            try:
                self._service.stop()
            except ValueError:
                pass

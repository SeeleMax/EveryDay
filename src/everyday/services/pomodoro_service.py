"""番茄钟服务（v1.1 P3）。

设计要点
--------
1. **时间由 ``tick()`` 显式推进**，服务内部不读系统时钟。
   界面用 1 秒定时器调 ``tick()``；测试直接 ``tick(1500)`` 就能把
   整段专注跑完，不需要 sleep，也不会因为机器卡顿而抖动。
2. **一开始专注就落库**（``focus_record`` 只有 ``started_at``），
   结束时回写 ``actual_seconds`` 与 ``completed``。这样即使进程被强杀，
   「我今天专注了多久」也不会整段丢失。
3. 状态机很小但明确：``空闲 -> 专注 -> 暂停 -> 专注 -> 结束 -> 休息``，
   非法转移一律抛 ``ValueError`` 而不是静默忽略——静默忽略会让界面
   按钮的可点状态与实际状态不一致，这类 bug 很难查。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable

from everyday import timeutils
from everyday.db.repositories import FocusRepository
from everyday.models.focus import MAX_PLANNED_MINUTES, MIN_PLANNED_MINUTES, FocusRecord

logger = logging.getLogger(__name__)

#: 默认专注时长（分钟）。文档的番茄钟标准值。
DEFAULT_FOCUS_MINUTES = 25

#: 默认休息时长（分钟）
DEFAULT_BREAK_MINUTES = 5


class PomodoroState(str, Enum):
    """番茄钟状态。"""

    IDLE = "idle"
    FOCUSING = "focusing"
    PAUSED = "paused"
    BREAK = "break"
    FINISHED = "finished"

    @property
    def label(self) -> str:
        """面向界面的中文名称。"""
        return _STATE_LABELS[self]

    @property
    def is_running(self) -> bool:
        """是否处于「计时进行中」的状态。"""
        return self in (PomodoroState.FOCUSING, PomodoroState.BREAK)


_STATE_LABELS: dict[PomodoroState, str] = {
    PomodoroState.IDLE: "空闲",
    PomodoroState.FOCUSING: "专注中",
    PomodoroState.PAUSED: "已暂停",
    PomodoroState.BREAK: "休息中",
    PomodoroState.FINISHED: "已完成",
}


@dataclass(frozen=True)
class PomodoroSnapshot:
    """某一刻的番茄钟状态快照，供界面渲染。"""

    state: PomodoroState
    planned_seconds: int
    elapsed_seconds: int
    task_id: int | None = None
    record_id: int | None = None

    @property
    def remaining_seconds(self) -> int:
        """剩余秒数（不会为负）。"""
        return max(0, self.planned_seconds - self.elapsed_seconds)

    @property
    def ratio(self) -> float:
        """已走过的比例，``0.0~1.0``（悬浮窗进度环用）。"""
        if self.planned_seconds <= 0:
            return 0.0
        return min(1.0, self.elapsed_seconds / self.planned_seconds)

    @property
    def remaining_label(self) -> str:
        """``mm:ss`` 形式的剩余时间。"""
        minutes, seconds = divmod(self.remaining_seconds, 60)
        return f"{minutes:02d}:{seconds:02d}"

    @property
    def is_active(self) -> bool:
        """是否在专注或暂停中（有未结束的专注记录）。"""
        return self.state in (
            PomodoroState.FOCUSING,
            PomodoroState.PAUSED,
        )


class PomodoroService:
    """番茄钟的状态与持久化。

    ``on_finished`` 是结束回调，参数是本次的 :class:`FocusRecord`。
    界面用它来弹「专注完成」通知、刷新统计。
    """

    def __init__(
        self,
        repository: FocusRepository,
        *,
        default_minutes: int = DEFAULT_FOCUS_MINUTES,
        break_minutes: int = DEFAULT_BREAK_MINUTES,
    ) -> None:
        self._repo = repository
        self._default_minutes = self._clamp(default_minutes)
        self._break_minutes = max(1, int(break_minutes))
        self._state = PomodoroState.IDLE
        self._planned_seconds = 0
        self._elapsed_seconds = 0
        self._task_id: int | None = None
        self._record: FocusRecord | None = None
        #: 结束回调（参数为 FocusRecord）
        self.on_finished: Callable[[FocusRecord], None] | None = None

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------
    @property
    def state(self) -> PomodoroState:
        """当前状态。"""
        return self._state

    @property
    def default_minutes(self) -> int:
        """默认专注时长（分钟）。"""
        return self._default_minutes

    @property
    def break_minutes(self) -> int:
        """休息时长（分钟）。"""
        return self._break_minutes

    @property
    def is_focusing(self) -> bool:
        """是否正在专注（P3 要求：专注期间屏蔽通知）。"""
        return self._state is PomodoroState.FOCUSING

    @property
    def is_active(self) -> bool:
        """是否有未结束的专注。"""
        return self.snapshot().is_active

    def snapshot(self) -> PomodoroSnapshot:
        """当前状态快照。"""
        return PomodoroSnapshot(
            state=self._state,
            planned_seconds=self._planned_seconds,
            elapsed_seconds=self._elapsed_seconds,
            task_id=self._task_id,
            record_id=self._record.id if self._record is not None else None,
        )

    # ------------------------------------------------------------------
    # 状态流转
    # ------------------------------------------------------------------
    def start(
        self,
        task_id: int | None = None,
        minutes: int | None = None,
        moment: datetime | None = None,
    ) -> PomodoroSnapshot:
        """开始一段专注。

        :param task_id: 关联的任务（可为 ``None``，即不绑定任务）
        :param minutes: 本次时长，默认取构造时的 ``default_minutes``
        """
        if self._state is PomodoroState.FOCUSING:
            raise ValueError("已经在专注中，请先结束当前一段")
        if self._state is PomodoroState.PAUSED:
            raise ValueError("当前处于暂停状态，请先恢复或结束")

        planned = self._clamp(minutes if minutes is not None else self._default_minutes)
        started = moment or timeutils.now_local()
        record = FocusRecord(
            planned_minutes=planned,
            task_id=task_id,
            started_at=started,
            actual_seconds=0,
            completed=False,
        )
        self._repo.add(record)
        self._record = record
        self._planned_seconds = planned * 60
        self._elapsed_seconds = 0
        self._task_id = task_id
        self._state = PomodoroState.FOCUSING
        logger.info(
            "番茄钟开始：任务=%s 时长=%d 分钟 记录=%s", task_id, planned, record.id
        )
        return self.snapshot()

    def pause(self) -> PomodoroSnapshot:
        """暂停专注。"""
        if self._state is not PomodoroState.FOCUSING:
            raise ValueError("只有专注中才能暂停")
        self._state = PomodoroState.PAUSED
        logger.info("番茄钟暂停于 %d 秒", self._elapsed_seconds)
        return self.snapshot()

    def resume(self) -> PomodoroSnapshot:
        """从暂停恢复。"""
        if self._state is not PomodoroState.PAUSED:
            raise ValueError("只有暂停中才能恢复")
        self._state = PomodoroState.FOCUSING
        logger.info("番茄钟恢复")
        return self.snapshot()

    def tick(self, seconds: int = 1, moment: datetime | None = None) -> PomodoroSnapshot:
        """推进时间。

        ``seconds`` 可以一次给一大段（测试里常用），此时会在到达计划时长时
        **恰好停在计划时长上**并把多余的秒数丢弃——一段番茄钟不会因为
        定时器迟到而算成 26 分钟。
        """
        if self._state is not PomodoroState.FOCUSING:
            return self.snapshot()
        if seconds <= 0:
            return self.snapshot()

        self._elapsed_seconds = min(
            self._planned_seconds, self._elapsed_seconds + int(seconds)
        )
        if self._elapsed_seconds >= self._planned_seconds:
            self._finish(completed=True, moment=moment)
        return self.snapshot()

    def stop(self, moment: datetime | None = None) -> FocusRecord | None:
        """中止当前专注（未跑完）。

        与 :meth:`tick` 跑到终点不同，这里 ``completed=False``，
        但**已专注的秒数照样记入统计**。
        """
        if not self.snapshot().is_active:
            raise ValueError("当前没有进行中的专注")
        return self._finish(completed=False, moment=moment)

    def start_break(self, moment: datetime | None = None) -> PomodoroSnapshot:
        """开始休息（专注结束后调用）。"""
        if self._state is not PomodoroState.FINISHED:
            raise ValueError("只有刚结束一段专注才能开始休息")
        self._planned_seconds = self._break_minutes * 60
        self._elapsed_seconds = 0
        self._state = PomodoroState.BREAK
        logger.info("番茄钟进入休息：%d 分钟", self._break_minutes)
        return self.snapshot()

    def tick_break(self, seconds: int = 1) -> PomodoroSnapshot:
        """推进休息时间；走完回到空闲。"""
        if self._state is not PomodoroState.BREAK:
            return self.snapshot()
        if seconds <= 0:
            return self.snapshot()
        self._elapsed_seconds = min(
            self._planned_seconds, self._elapsed_seconds + int(seconds)
        )
        if self._elapsed_seconds >= self._planned_seconds:
            self.reset()
        return self.snapshot()

    def skip_break(self) -> PomodoroSnapshot:
        """跳过休息。"""
        if self._state is not PomodoroState.BREAK:
            raise ValueError("当前不在休息中")
        self.reset()
        return self.snapshot()

    def reset(self) -> PomodoroSnapshot:
        """回到空闲（清空计时，保留已落库的记录）。"""
        self._state = PomodoroState.IDLE
        self._planned_seconds = 0
        self._elapsed_seconds = 0
        self._task_id = None
        self._record = None
        return self.snapshot()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _finish(
        self, *, completed: bool, moment: datetime | None = None
    ) -> FocusRecord | None:
        """结束一段专注：回写记录、切状态、触发回调。"""
        record = self._record
        elapsed = self._elapsed_seconds
        if record is not None:
            record.actual_seconds = elapsed
            record.completed = completed
            record.ended_at = moment or timeutils.now_local()
            self._repo.update(record)
            logger.info(
                "番茄钟结束：记录=%s 实际 %.1f 分钟 完成=%s",
                record.id,
                record.actual_minutes,
                completed,
            )
        self._state = PomodoroState.FINISHED
        if self.on_finished is not None and record is not None:
            try:
                self.on_finished(record)
            except Exception:  # 回调出错不能影响状态机
                logger.exception("番茄钟结束回调抛异常，已忽略")
        return record

    @staticmethod
    def _clamp(minutes: int) -> int:
        """把时长限制在允许范围内。"""
        return max(MIN_PLANNED_MINUTES, min(MAX_PLANNED_MINUTES, int(minutes)))

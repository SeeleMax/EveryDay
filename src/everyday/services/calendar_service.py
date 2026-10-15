"""日历服务（文档 5.6 实时日历）。

职责：把「数据源」变成「事件列表」，并处理缓存与容错。

容错策略（文档 5.6 网络订阅说明）：读取或解析失败时**记日志并保留上一次
成功的数据**，绝不因为订阅地址暂时不可用就清空界面上的日程。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from everyday import timeutils
from everyday.services.ics_parser import CalendarEvent, load_events

logger = logging.getLogger(__name__)

#: 文档 5.6：筛选当天及未来七天的事件
DEFAULT_WINDOW_DAYS = 7

#: 网络订阅超时（秒）
NETWORK_TIMEOUT = 15


def read_source(source: str) -> str:
    """读取 ICS 内容。

    支持三种写法：

    * ``D:\\cal\\work.ics``        本地文件路径
    * ``file:///D:/cal/work.ics``  本地文件 URL
    * ``https://.../basic.ics``   网络订阅地址
    """
    text = (source or "").strip()
    if not text:
        raise ValueError("日历数据源为空")

    parsed = urlparse(text)
    if parsed.scheme in ("http", "https"):
        request = Request(text, headers={"User-Agent": "EveryDay/1.0", "Accept": "text/calendar,*/*"})
        with urlopen(request, timeout=NETWORK_TIMEOUT) as response:  # noqa: S310 - 用户显式填写的订阅地址
            raw = response.read()
        # ICS 可能是 UTF-8，也可能是本地编码，优先 UTF-8 并容错
        return raw.decode("utf-8", errors="replace")

    if parsed.scheme == "file":
        path = Path(parsed.path.lstrip("/")) if parsed.netloc in ("", "localhost") else Path(text[7:])
        return path.read_text(encoding="utf-8", errors="replace")

    return Path(text).expanduser().read_text(encoding="utf-8", errors="replace")


@dataclass
class CalendarSnapshot:
    """一次成功加载的结果。"""

    events: list[CalendarEvent] = field(default_factory=list)
    loaded_at: datetime | None = None
    window_start: date | None = None
    window_end: date | None = None
    source: str = ""

    @property
    def is_empty(self) -> bool:
        """是否没有任何事件。"""
        return not self.events


class CalendarService:
    """日历数据源管理 + 事件查询。"""

    def __init__(self, window_days: int = DEFAULT_WINDOW_DAYS) -> None:
        self._window_days = window_days
        self._snapshot = CalendarSnapshot()
        self._last_error: str | None = None

    # ------------------------------------------------------------------
    @property
    def source(self) -> str:
        """当前已加载的数据源。"""
        return self._snapshot.source

    @property
    def last_error(self) -> str | None:
        """最近一次加载失败的原因；成功后清空。"""
        return self._last_error

    @property
    def last_loaded_at(self) -> datetime | None:
        """最近一次成功加载的时间。"""
        return self._snapshot.loaded_at

    # ------------------------------------------------------------------
    def refresh(self, source: str, day: date | None = None) -> bool:
        """重新加载数据源。返回是否成功（失败时保留旧数据）。"""
        text = (source or "").strip()
        if not text:
            # 数据源被清空：视为「用户主动关闭日历」，清掉缓存是正确行为
            self._snapshot = CalendarSnapshot()
            self._last_error = None
            logger.info("日历数据源为空，已清空日程缓存")
            return True

        today = day or timeutils.today_local()
        window_end = today + timedelta(days=self._window_days - 1)
        try:
            content = read_source(text)
            events = load_events(content, today, window_end)
        except Exception as exc:  # noqa: BLE001 - 网络/文件/解析错误都按同一种方式降级
            self._last_error = str(exc)
            logger.warning("日历加载失败，沿用上一次数据：%s", exc)
            return False

        self._snapshot = CalendarSnapshot(
            events=events,
            loaded_at=timeutils.now_local(),
            window_start=today,
            window_end=window_end,
            source=text,
        )
        self._last_error = None
        logger.info(
            "日历加载成功：%d 个事件（%s ~ %s）", len(events), today, window_end
        )
        return True

    # ------------------------------------------------------------------
    def today_events(self, day: date | None = None) -> list[CalendarEvent]:
        """今日日程（文档 5.6 主窗口侧栏）。"""
        target = day or timeutils.today_local()
        return [e for e in self._snapshot.events if e.overlaps_day(target)]

    def upcoming(self, day: date | None = None) -> list[CalendarEvent]:
        """今日及未来七天的事件（已排序）。"""
        target = day or timeutils.today_local()
        window_end = target + timedelta(days=self._window_days - 1)
        return [
            e
            for e in self._snapshot.events
            if e.last_day >= target and e.start_date <= window_end
        ]

    def next_event(self, moment: datetime | None = None) -> CalendarEvent | None:
        """最近一条尚未结束的日程（文档 5.2 悬浮窗底部可选显示）。"""
        now = moment or timeutils.now_local()
        for event in self._snapshot.events:
            end = event.end or event.start
            if end >= now:
                return event
        return None

    def replace_events(self, events: list[CalendarEvent], source: str = "") -> None:
        """直接注入事件列表（供测试与离线预览使用）。"""
        event_list = sorted(events, key=CalendarEvent.sort_key)
        self._snapshot = CalendarSnapshot(
            events=event_list,
            loaded_at=timeutils.now_local(),
            source=source,
        )
        self._last_error = None

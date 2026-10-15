"""ICS (iCalendar, RFC 5545) 解析器（文档 5.6 实时日历）。

为什么手写而不用第三方库
------------------------
V1 只用到 ICS 的一个子集：单次事件 + 常见循环规则，且需要「只展开给定
7 天窗口」这一按需语义。第三方库为实现完整 RFC 会引入可观依赖，而本项目
的定位是「单机、零配置」。因此这里实现所需的子集，并把不支持的写法
**显式降级为日志告警**，而不是静默丢弃。

已支持的子集
------------
* 行折叠（续行以空格或 TAB 开头）
* 属性参数：``DTSTART;VALUE=DATE:20261005``、``DTSTART;TZID=Asia/Shanghai:...``
* 日期/日期时间取值：``YYYYMMDD``、``YYYYMMDDTHHMMSS``、``...Z``（UTC）
* 文本转义：``\\n``、``\\,``、``\\;``、``\\\\``
* 循环规则：``FREQ=DAILY|WEEKLY|MONTHLY`` 搭配 ``INTERVAL`` / ``COUNT`` / ``UNTIL``
* ``EXDATE`` 排除日期

已知限制（见 docs/known-issues.md）
-----------------------------------
* ``TZID`` 参数被忽略，一律按本地时间解释；跨时区订阅可能出现偏移
* 不支持 ``BYDAY`` / ``BYMONTHDAY`` / ``BYSETPOS`` 等高级递归修饰符
* 不支持 ``RECURRENCE-ID`` 覆盖实例
* 全天事件的 ``DTEND`` 为排他语义，按 RFC 处理
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta

logger = logging.getLogger(__name__)

#: 支持的循环频率
SUPPORTED_FREQUENCIES = ("DAILY", "WEEKLY", "MONTHLY")

#: 一次展开最多生成的实例数，防御畸形 RRULE 造成的死循环
MAX_OCCURRENCES_PER_EVENT = 2000


# ======================================================================
# 数据模型
# ======================================================================
@dataclass(frozen=True)
class CalendarEvent:
    """一条日历事件（已解析为一个具体时间点）。"""

    uid: str
    summary: str
    start: datetime
    end: datetime | None = None
    location: str = ""
    description: str = ""
    all_day: bool = False

    @property
    def start_date(self) -> date:
        """事件开始日期。"""
        return self.start.date()

    @property
    def end_date(self) -> date:
        """事件结束日期；没有结束时间时等于开始日期。"""
        return self.end.date() if self.end else self.start.date()

    @property
    def last_day(self) -> date:
        """事件实际占据的最后一天。

        全天事件的 ``DTEND`` 是**排他**语义（RFC 5545），
        例如 10-05 全天的结束时间是 10-06T00:00，实际只占 10-05。
        """
        if self.end is None:
            return self.start_date
        if self.all_day:
            return max(self.start_date, (self.end - timedelta(days=1)).date())
        return self.end.date()

    @property
    def duration(self) -> timedelta:
        """事件时长；无结束时间时视为零时长。"""
        return (self.end - self.start) if self.end else timedelta(0)

    @property
    def time_label(self) -> str:
        """界面用的时间标签：全天 / ``09:00`` / ``09:00-10:30``。"""
        if self.all_day:
            return "全天"
        if self.end is None or self.end == self.start:
            return self.start.strftime("%H:%M")
        return f"{self.start.strftime('%H:%M')}-{self.end.strftime('%H:%M')}"

    def overlaps_day(self, day: date) -> bool:
        """事件是否与 ``day`` 有交集（含跨天事件）。"""
        return self.start_date <= day <= self.last_day

    def sort_key(self) -> tuple[date, int, str]:
        """排序键：全天事件排在同日定时事件之后（更符合阅读习惯）。"""
        return (self.start_date, 1 if self.all_day else 0, self.summary)


# ======================================================================
# 词法层
# ======================================================================
def unfold_lines(text: str) -> list[str]:
    """展开 ICS 的行折叠，返回逻辑行列表。

    RFC 5545 规定长行通过「CRLF + 单个空格/TAB」续行。
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    logical: list[str] = []
    for raw in normalized.split("\n"):
        if raw.startswith((" ", "\t")) and logical:
            logical[-1] += raw[1:]
        elif raw.strip():
            logical.append(raw)
    return logical


_PROPERTY_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9\-]+)(?P<params>(?:;[^:]*)?):(?P<value>.*)$"
)


def split_property(line: str) -> tuple[str, dict[str, str], str]:
    """把一行拆成 ``(名称, 参数字典, 取值)``。

    参数字典的键统一大写；无参数时返回空字典。
    无法解析的行返回 ``("", {}, "")``。
    """
    match = _PROPERTY_RE.match(line)
    if match is None:
        return ("", {}, "")
    name = match.group("name").upper()
    params: dict[str, str] = {}
    for chunk in match.group("params").split(";"):
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        params[key.strip().upper()] = value.strip().strip('"')
    return (name, params, match.group("value"))


def unescape_text(value: str) -> str:
    """还原 ICS 文本字段的转义序列。"""
    if not value:
        return ""
    result: list[str] = []
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char == "\\" and index + 1 < length:
            nxt = value[index + 1]
            if nxt in ("n", "N"):
                result.append("\n")
            elif nxt in ("\\", ",", ";", ":"):
                result.append(nxt)
            else:
                result.append(nxt)
            index += 2
            continue
        result.append(char)
        index += 1
    return "".join(result)


# ======================================================================
# 日期时间解析
# ======================================================================
_DATE_ONLY_RE = re.compile(r"^\d{8}$")
_DATETIME_RE = re.compile(r"^\d{8}T\d{4}(\d{2})?Z?$")


def parse_ics_datetime(value: str, params: Mapping[str, str]) -> tuple[datetime, bool]:
    """解析 ICS 日期时间取值，返回 ``(本地时间, 是否全天)``。

    解析失败时抛 ``ValueError``，由调用方决定跳过该事件。
    """
    raw = (value or "").strip()
    if params.get("VALUE", "").upper() == "DATE" or _DATE_ONLY_RE.match(raw):
        parsed = datetime.strptime(raw[:8], "%Y%m%d")
        return (parsed, True)

    if not _DATETIME_RE.match(raw):
        raise ValueError(f"无法识别的日期时间取值：{raw!r}")

    is_utc = raw.endswith("Z")
    body = raw[:-1] if is_utc else raw
    fmt = "%Y%m%dT%H%M%S" if len(body) == 15 else "%Y%m%dT%H%M"
    parsed = datetime.strptime(body, fmt)

    if is_utc:
        # 转成本地时间：UTC -> 本地
        parsed = datetime.fromtimestamp(parsed.replace(tzinfo=_utc()).timestamp())
        parsed = parsed.replace(microsecond=0)
    elif "TZID" in params:
        # 已知限制：忽略 TZID，按本地时间解释
        logger.debug("忽略 TZID=%s，按本地时间解释 %s", params["TZID"], raw)
    return (parsed, False)


def _utc():
    """返回 UTC 时区对象（延迟导入，兼容 Python 3.9）。"""
    from datetime import timezone

    return timezone.utc


# ======================================================================
# 循环规则
# ======================================================================
def parse_rrule(value: str) -> dict[str, str]:
    """把 ``FREQ=DAILY;INTERVAL=2`` 解析为字典。"""
    rule: dict[str, str] = {}
    for part in (value or "").split(";"):
        key, _, val = part.partition("=")
        key = key.strip().upper()
        if key:
            rule[key] = val.strip()
    return rule


def _add_months(moment: datetime, months: int) -> datetime:
    """在日期时间上增加自然月，日序号超出目标月时收敛到月末。"""
    total = moment.month - 1 + months
    year = moment.year + total // 12
    month = total % 12 + 1
    # 目标月份的天数
    if month == 12:
        last_day = 31
    else:
        last_day = (date(year, month + 1, 1) - timedelta(days=1)).day
    return moment.replace(year=year, month=month, day=min(moment.day, last_day))


def _iter_occurrence_starts(
    start: datetime,
    rule: Mapping[str, str],
    window_start: datetime,
    window_end: datetime,
) -> Iterator[datetime]:
    """按循环规则生成实例起始时间，直到超出窗口或规则结束。

    当规则没有 ``COUNT`` 时，会先做一次「快速前跳」，直接跳到窗口前一个
    周期附近。否则一个 5 年前开始的每日事件需要先迭代上千次才能到达窗口，
    既慢又会撞上 :data:`MAX_OCCURRENCES_PER_EVENT` 上限而丢失数据。
    """
    frequency = (rule.get("FREQ") or "").upper()
    if frequency not in SUPPORTED_FREQUENCIES:
        if frequency:
            logger.warning("不支持的 RRULE FREQ=%s，事件按单次处理", frequency)
        yield start
        return

    interval = 1
    try:
        interval = max(1, int(rule.get("INTERVAL", "1")))
    except ValueError:
        interval = 1

    count_limit: int | None = None
    if "COUNT" in rule:
        try:
            count_limit = max(1, int(rule["COUNT"]))
        except ValueError:
            count_limit = None

    until: datetime | None = None
    if "UNTIL" in rule:
        try:
            until, _ = parse_ics_datetime(rule["UNTIL"], {})
        except ValueError:
            until = None

    current = start

    # 快速前跳（仅在无 COUNT 时安全：有 COUNT 必须从序列头开始计数）
    if count_limit is None and start < window_start:
        step_days = {"DAILY": interval, "WEEKLY": interval * 7}.get(frequency)
        if step_days:
            gap_days = (window_start - start).days
            skips = max(0, gap_days // step_days - 1)
            current = start + timedelta(days=skips * step_days)
        else:  # MONTHLY
            gap_months = (window_start.year - start.year) * 12 + (
                window_start.month - start.month
            )
            skips = max(0, gap_months // interval - 1)
            current = _add_months(start, skips * interval)

    produced = 0
    while produced < MAX_OCCURRENCES_PER_EVENT:
        if count_limit is not None and produced >= count_limit:
            return
        if until is not None and current > until:
            return
        if current > window_end:
            return
        yield current
        produced += 1
        if frequency == "DAILY":
            current = current + timedelta(days=interval)
        elif frequency == "WEEKLY":
            current = current + timedelta(weeks=interval)
        else:  # MONTHLY
            current = _add_months(current, interval)


def expand_recurrences(
    events: Sequence[CalendarEvent],
    rules: Mapping[str, Mapping[str, str]],
    exdates: Mapping[str, set[date]],
    window_start: date,
    window_end: date,
) -> list[CalendarEvent]:
    """把循环事件展开到 ``[window_start, window_end]`` 窗口内。

    :param events: 已解析的事件（其 ``uid`` 用于查循环规则与排除日期）
    :param rules: ``{uid: {RRULE 键值}}``
    :param exdates: ``{uid: {被排除的日期}}``
    """
    window_start_dt = datetime.combine(window_start, time.min)
    window_end_dt = datetime.combine(window_end, time.max)
    expanded: list[CalendarEvent] = []

    for event in events:
        excluded = exdates.get(event.uid, set())
        rule = rules.get(event.uid)
        occurrences: Iterable[datetime] = (
            _iter_occurrence_starts(event.start, rule, window_start_dt, window_end_dt)
            if rule
            else [event.start]
        )
        for occurrence in occurrences:
            instance = replace(
                event,
                start=occurrence,
                end=(occurrence + event.duration) if event.end else None,
            )
            if instance.start_date in excluded:
                continue
            # 窗口重叠判定：结束日早于窗口起点，或开始日晚于窗口终点，则丢弃
            if instance.last_day < window_start or instance.start_date > window_end:
                continue
            expanded.append(instance)

    expanded.sort(key=CalendarEvent.sort_key)
    return expanded


# ======================================================================
# 主解析入口
# ======================================================================
def parse_ics(text: str) -> tuple[list[CalendarEvent], dict[str, dict[str, str]], dict[str, set[date]]]:
    """解析 ICS 文本。

    返回 ``(事件列表, {uid: RRULE 字典}, {uid: 排除日期集合})``。
    单个事件解析失败只记录日志并跳过，不影响其余事件。
    """
    events: list[CalendarEvent] = []
    rules: dict[str, dict[str, str]] = {}
    exdates: dict[str, set[date]] = {}

    current: dict[str, tuple[dict[str, str], str]] | None = None
    index = 0

    for line in unfold_lines(text):
        if line.upper() == "BEGIN:VEVENT":
            current = {}
            index += 1
            continue
        if line.upper() == "END:VEVENT":
            if current:
                _build_event(current, index, events, rules, exdates)
            current = None
            continue
        if current is None:
            continue
        name, params, value = split_property(line)
        if not name:
            continue
        if name == "EXDATE":
            # 一个事件可以有多行 EXDATE，这里合并成逗号分隔的单一取值
            existing = current.get(name)
            if existing is not None:
                value = f"{existing[1]},{value}"
            current[name] = (params, value)
            continue
        # 同名属性只保留第一次（DTSTART 等不应重复）
        current.setdefault(name, (params, value))

    return (events, rules, exdates)


def _build_event(
    props: Mapping[str, tuple[dict[str, str], str]],
    index: int,
    events: list[CalendarEvent],
    rules: dict[str, dict[str, str]],
    exdates: dict[str, set[date]],
) -> None:
    """把一组 VEVENT 属性构造成 :class:`CalendarEvent`。"""
    dtstart = props.get("DTSTART")
    if dtstart is None:
        logger.warning("跳过缺少 DTSTART 的事件（序号 %d）", index)
        return
    params, raw_start = dtstart
    try:
        start, all_day = parse_ics_datetime(raw_start, params)
    except ValueError as exc:
        logger.warning("跳过时间无法解析的事件：%s", exc)
        return

    end: datetime | None = None
    if "DTEND" in props:
        end_params, raw_end = props["DTEND"]
        try:
            end, _ = parse_ics_datetime(raw_end, end_params)
        except ValueError as exc:
            logger.warning("事件结束时间无法解析，按无结束时间处理：%s", exc)

    summary = unescape_text(props.get("SUMMARY", ({}, ""))[1]) or "(无标题)"
    uid = props.get("UID", ({}, f"generated-{index}"))[1] or f"generated-{index}"

    event = CalendarEvent(
        uid=uid,
        summary=summary,
        start=start,
        end=end,
        location=unescape_text(props.get("LOCATION", ({}, ""))[1]),
        description=unescape_text(props.get("DESCRIPTION", ({}, ""))[1]),
        all_day=all_day,
    )
    events.append(event)

    if "RRULE" in props:
        rules[uid] = parse_rrule(props["RRULE"][1])

    if "EXDATE" in props:
        ex_params, ex_value = props["EXDATE"]
        excluded: set[date] = set()
        for chunk in ex_value.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                moment, _ = parse_ics_datetime(chunk, ex_params)
                excluded.add(moment.date())
            except ValueError:
                logger.debug("忽略无法解析的 EXDATE 取值：%s", chunk)
        if excluded:
            exdates[uid] = excluded


def load_events(
    text: str, window_start: date, window_end: date
) -> list[CalendarEvent]:
    """一步到位：解析 + 展开循环 + 过滤排序（文档 5.6 解析流程）。"""
    events, rules, exdates = parse_ics(text)
    return expand_recurrences(events, rules, exdates, window_start, window_end)

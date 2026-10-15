"""日期/时间的解析与格式化。

数据库里日期与时间都以 TEXT 存储，本模块是唯一的转换出入口，
保证「写入什么格式、读回什么格式」在全局只有一处定义。

设计约定：
    * 日期      ``YYYY-MM-DD``
    * 时间      ``HH:MM``（24 小时制，分钟永远两位）
    * 日期时间  ``YYYY-MM-DDTHH:MM:SS``

所有 ``parse_*`` 函数对 ``None`` 与空字符串都返回 ``None``，不抛异常；
这符合「数据库里可空字段读出来就是可空」的语义。
"""

from __future__ import annotations

from datetime import date, datetime, time

DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M"
DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


def today_local() -> date:
    """返回本地时区的今天。"""
    return datetime.now().date()


def now_local() -> datetime:
    """返回本地时区的当前时间（秒级精度）。"""
    return datetime.now().replace(microsecond=0)


# --------------------------------------------------------------------------
# 日期
# --------------------------------------------------------------------------
def parse_date(value: object) -> date | None:
    """把数据库/JSON 里的值解析为 :class:`datetime.date`。

    支持 ``date`` 实例、``datetime`` 实例，以及 ``YYYY-MM-DD`` 字符串。
    无法解析时返回 ``None``（脏数据不应让整个应用崩溃）。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    # 兼容带时间的 ISO 串（例如 ICS 导入或手工编辑过的数据）
    text = text.replace("/", "-")
    try:
        return datetime.strptime(text[:10], DATE_FORMAT).date()
    except ValueError:
        return None


def format_date(value: date | datetime | None) -> str | None:
    """把日期格式化为数据库存储字符串；``None`` 原样返回。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime(DATE_FORMAT)


# --------------------------------------------------------------------------
# 时间
# --------------------------------------------------------------------------
def parse_time(value: object) -> time | None:
    """把值解析为 :class:`datetime.time`。

    容忍 ``9:5``、``09:05:00`` 这类非规范写法，统一按 24 小时制处理。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0)
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1][:2])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour=hour, minute=minute)


def format_time(value: time | datetime | None) -> str | None:
    """把时间格式化为 ``HH:MM``；``None`` 原样返回。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.time()
    return value.strftime(TIME_FORMAT)


# --------------------------------------------------------------------------
# 日期时间
# --------------------------------------------------------------------------
def parse_datetime(value: object) -> datetime | None:
    """解析 ``YYYY-MM-DDTHH:MM:SS``；失败时返回 ``None``。

    优先走 :meth:`datetime.fromisoformat`（可正确处理带时区偏移、毫秒、
    以及 ``Z`` 后缀的串），退化路径按纯日期解析。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace(" ", "T")
    if normalized.endswith(("Z", "z")):
        # Python 3.11 之前的 fromisoformat 不认 'Z'，统一改写成显式偏移
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized).replace(microsecond=0)
    except ValueError:
        pass
    parsed_date = parse_date(normalized)
    if parsed_date is not None:
        return datetime(parsed_date.year, parsed_date.month, parsed_date.day)
    return None


def format_datetime(value: datetime | date | None) -> str | None:
    """把日期时间格式化为数据库存储字符串；``None`` 原样返回。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(microsecond=0).strftime(DATETIME_FORMAT)
    return datetime(value.year, value.month, value.day).strftime(DATETIME_FORMAT)

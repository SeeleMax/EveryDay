"""屏幕时间记录实体（v1.1 P5）。

存储粒度是「日期 + 应用名」的累计秒数，而不是原始采样点：
P5 要求 2 秒采样一次，一天就是四万多个样本，按样本存表会迅速膨胀。
采样在服务层做会话合并后，用 UPSERT 累加到本表。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from everyday import timeutils

#: 应用名最大长度，防止异常进程名把表撑坏
MAX_APP_NAME_LENGTH = 120


@dataclass(frozen=True)
class ScreenTimeRecord:
    """某一天某个应用的累计使用时长。"""

    day: date
    app_name: str
    seconds: int

    def __post_init__(self) -> None:
        name = (self.app_name or "").strip()
        if not name:
            raise ValueError("应用名不能为空")
        object.__setattr__(self, "app_name", name[:MAX_APP_NAME_LENGTH])
        object.__setattr__(self, "seconds", max(0, int(self.seconds)))

    @property
    def minutes(self) -> float:
        """折算成分钟，便于展示。"""
        return round(self.seconds / 60, 1)

    def to_row(self) -> dict[str, Any]:
        """转换为可直接用于 UPSERT 的列字典。"""
        return {
            "day": timeutils.format_date(self.day),
            "app_name": self.app_name,
            "seconds": self.seconds,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> ScreenTimeRecord:
        """从数据库行构造实体。"""
        parsed = timeutils.parse_date(row["day"]) or timeutils.today_local()
        return cls(
            day=parsed,
            app_name=str(row["app_name"]),
            seconds=int(row["seconds"] or 0),
        )


def format_duration(seconds: int) -> str:
    """把秒数格式化成 ``1 小时 23 分`` / ``45 分`` / ``30 秒`` 这样的文本。"""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分"
    return f"{secs} 秒"

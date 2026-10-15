"""应用设置实体（文档 4.1 AppSettings、5.1 / 5.2 / 5.6）。

存储层是键值表 ``app_settings(key, value)``，本模块负责
「富类型 ↔ 字符串」的双向转换，是设置项类型的唯一定义处。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any

from everyday.models.preset import DEFAULT_CATEGORY  # noqa: F401  (对外暴露同一常量)

#: 默认日历刷新间隔（分钟），文档 5.6 规定为 30
DEFAULT_CALENDAR_REFRESH_MINUTES = 30


def _as_bool(value: object, default: bool = False) -> bool:
    """宽松布尔解析：兼容 JSON 布尔、``"true"``/``"1"``/``"yes"`` 等写法。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off", ""):
        return False
    return default


def _as_float(value: object, default: float) -> float:
    """宽松浮点解析，失败时回落到默认值。"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int) -> int:
    """宽松整数解析，失败时回落到默认值。"""
    if value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


@dataclass
class AppSettings:
    """应用级设置，字段与文档 4.1 的 AppSettings 表格一一对应。"""

    auto_start: bool = False
    float_window_visible: bool = True
    float_window_left: float = 60.0
    float_window_top: float = 60.0
    calendar_ics_path: str = ""

    # ---- 以下几项是文档未列出、但实现必需的运行时配置 ----
    # 它们有安全默认值，缺失时不影响行为。
    calendar_refresh_minutes: int = DEFAULT_CALENDAR_REFRESH_MINUTES
    log_level: str = "INFO"
    startup_backup_enabled: bool = True

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    def to_kv(self) -> dict[str, str]:
        """转换为 ``app_settings`` 表的键值字典。"""
        return {
            "auto_start": "true" if self.auto_start else "false",
            "float_window_visible": "true" if self.float_window_visible else "false",
            "float_window_left": repr(float(self.float_window_left)),
            "float_window_top": repr(float(self.float_window_top)),
            "calendar_ics_path": self.calendar_ics_path or "",
            "calendar_refresh_minutes": str(int(self.calendar_refresh_minutes)),
            "log_level": (self.log_level or "INFO").upper(),
            "startup_backup_enabled": "true" if self.startup_backup_enabled else "false",
        }

    @classmethod
    def from_kv(cls, values: Mapping[str, Any]) -> AppSettings:
        """从键值字典构造设置对象；缺失键使用默认值。"""
        defaults = cls()
        return cls(
            auto_start=_as_bool(values.get("auto_start"), defaults.auto_start),
            float_window_visible=_as_bool(
                values.get("float_window_visible"), defaults.float_window_visible
            ),
            float_window_left=_as_float(
                values.get("float_window_left"), defaults.float_window_left
            ),
            float_window_top=_as_float(
                values.get("float_window_top"), defaults.float_window_top
            ),
            calendar_ics_path=str(values.get("calendar_ics_path") or "").strip(),
            calendar_refresh_minutes=max(
                1,
                _as_int(
                    values.get("calendar_refresh_minutes"),
                    DEFAULT_CALENDAR_REFRESH_MINUTES,
                ),
            ),
            log_level=str(values.get("log_level") or "INFO").upper(),
            startup_backup_enabled=_as_bool(
                values.get("startup_backup_enabled"), defaults.startup_backup_enabled
            ),
        )

    def merged(self, **changes: Any) -> AppSettings:
        """返回替换了部分字段的副本，用于「读-改-写」配置更新。"""
        valid = {f.name for f in fields(self)}
        unknown = set(changes) - valid
        if unknown:
            raise TypeError(f"未知的设置项：{', '.join(sorted(unknown))}")
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data.update(changes)
        return AppSettings(**data)

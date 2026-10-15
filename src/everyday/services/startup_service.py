"""开机自启服务（文档 5.1）。

实现方式：写入 **当前用户** 的注册表启动项

    HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run

因此不需要管理员权限，也不影响其他用户（文档 5.1 权限说明）。

非 Windows 平台（例如 macOS 上跑单元测试）不会报错：
:meth:`StartupService.is_supported` 返回 ``False``，
其余方法安全地变成空操作并记一条日志。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from everyday import APP_NAME

logger = logging.getLogger(__name__)

#: 注册表启动项键路径（当前用户）
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

#: 开机自启时传给应用的参数，对应文档 3.3「判断启动参数」
MINIMIZED_FLAG = "--minimized"

try:  # pragma: no cover - 平台分支
    import winreg  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]


def entry_point_script() -> Path:
    """返回仓库根目录的 ``main.py`` 路径。"""
    from everyday import paths

    return paths.project_root() / "main.py"


def python_launcher() -> str:
    """返回用于自启的 Python 解释器。

    优先使用 ``pythonw.exe``：无控制台窗口，不会在开机时闪出一个黑框。
    在 PyCharm 虚拟环境里 ``sys.executable`` 就是 venv 下的解释器，
    同目录下的 ``pythonw.exe`` 同样存在，因此这里能正确工作。
    """
    executable = Path(sys.executable)
    if os.name == "nt":
        candidate = executable.with_name("pythonw.exe")
        if candidate.is_file():
            return str(candidate)
    return str(executable)


def build_command(script: Path | None = None) -> str:
    """构造写入注册表的启动命令行（文档 5.1：完整路径 + 最小化参数）。

    路径带空格时必须加引号，否则注册表会把路径截断到第一个空格处。
    """
    target = script or entry_point_script()
    return f'"{python_launcher()}" "{target}" {MINIMIZED_FLAG}'


class StartupService:
    """注册表开机自启的读写。"""

    def __init__(self, value_name: str = APP_NAME) -> None:
        self._value_name = value_name

    # ------------------------------------------------------------------
    @staticmethod
    def is_supported() -> bool:
        """当前平台是否支持注册表自启。"""
        return winreg is not None and os.name == "nt"

    @property
    def value_name(self) -> str:
        """注册表中的键名（默认与应用同名）。"""
        return self._value_name

    # ------------------------------------------------------------------
    def is_enabled(self) -> bool:
        """读取注册表，判断自启是否已开启。"""
        if not self.is_supported():
            return False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
                value, _ = winreg.QueryValueEx(key, self._value_name)
            return bool(str(value).strip())
        except FileNotFoundError:
            return False
        except OSError as exc:
            logger.warning("读取开机自启注册表失败：%s", exc)
            return False

    def current_command(self) -> str | None:
        """返回注册表里当前记录的命令行（未开启时为 ``None``）。"""
        if not self.is_supported():
            return None
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
                value, _ = winreg.QueryValueEx(key, self._value_name)
            return str(value)
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("读取开机自启命令行失败：%s", exc)
            return None

    def enable(self, script: Path | None = None) -> bool:
        """写入启动项。返回是否成功。"""
        if not self.is_supported():
            logger.info("当前平台不支持注册表自启，已跳过")
            return False
        command = build_command(script)
        try:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(key, self._value_name, 0, winreg.REG_SZ, command)
            logger.info("已开启开机自启：%s", command)
            return True
        except OSError as exc:
            logger.error("写入开机自启失败：%s", exc)
            return False

    def disable(self) -> bool:
        """删除启动项。返回是否成功（本来就不存在也算成功）。"""
        if not self.is_supported():
            return False
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, self._value_name)
            logger.info("已关闭开机自启")
            return True
        except FileNotFoundError:
            return True
        except OSError as exc:
            logger.error("删除开机自启失败：%s", exc)
            return False

    def apply(self, enabled: bool, script: Path | None = None) -> bool:
        """把 ``enabled`` 落到注册表（设置页勾选框与托盘菜单共用）。"""
        return self.enable(script) if enabled else self.disable()

    def sync_from_registry(self) -> bool:
        """以注册表为准返回实际状态。

        文档 5.1 要求「应用启动时读取注册表，与配置中的状态保持一致」：
        如果用户在「任务管理器 → 启动」里禁用了本项，注册表值可能仍在，
        因此这里只做读取；配置与注册表不一致的处理交给调用方。
        """
        actual = self.is_enabled()
        logger.debug("注册表开机自启实际状态：%s", actual)
        return actual

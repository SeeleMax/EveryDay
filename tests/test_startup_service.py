"""开机自启服务测试（文档 5.1）。

**不触碰真实注册表**：只测试纯函数（命令行拼装）与平台判定，
避免测试污染开发机的自启项。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from everyday import APP_NAME
from everyday.services.startup_service import (
    MINIMIZED_FLAG,
    RUN_KEY_PATH,
    StartupService,
    build_command,
    entry_point_script,
    python_launcher,
)
from tests.base import DatabaseTestCase  # noqa: F401  (确保 src 已入 path)


class BuildCommandTest(unittest.TestCase):
    def test_command_quotes_both_paths(self):
        command = build_command(Path(r"C:\Program Files\EveryDay\main.py"))
        self.assertTrue(command.startswith('"'))
        self.assertIn(r'"C:\Program Files\EveryDay\main.py"', command)

    def test_command_contains_minimized_flag(self):
        command = build_command(Path(r"D:\app\main.py"))
        self.assertTrue(command.endswith(MINIMIZED_FLAG))

    def test_command_uses_launcher_from_sys_executable(self):
        command = build_command(Path(r"D:\app\main.py"))
        self.assertIn(python_launcher(), command)

    def test_default_script_is_repository_main_py(self):
        script = entry_point_script()
        self.assertEqual(script.name, "main.py")
        self.assertTrue(script.parent.is_dir())

    def test_registry_key_path_is_current_user_run(self):
        self.assertEqual(RUN_KEY_PATH, r"Software\Microsoft\Windows\CurrentVersion\Run")

    @unittest.skipUnless(os.name == "nt", "仅 Windows 有 pythonw.exe")
    def test_windows_prefers_pythonw_launcher(self):
        self.assertTrue(python_launcher().lower().endswith("pythonw.exe"))


class StartupServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = StartupService()

    def test_value_name_defaults_to_app_name(self):
        self.assertEqual(self.service.value_name, APP_NAME)

    def test_is_supported_matches_platform(self):
        self.assertEqual(self.service.is_supported(), os.name == "nt")

    @unittest.skipIf(os.name == "nt", "非 Windows 平台断言空操作语义")
    def test_operations_are_noops_off_windows(self):
        self.assertFalse(self.service.is_enabled())
        self.assertIsNone(self.service.current_command())
        self.assertFalse(self.service.enable())
        self.assertFalse(self.service.disable())
        self.assertFalse(self.service.apply(True))
        self.assertFalse(self.service.sync_from_registry())

    @unittest.skipIf(os.name == "nt", "非 Windows 平台没有注册表")
    def test_current_command_is_none_off_windows(self):
        self.assertIsNone(self.service.current_command())


if __name__ == "__main__":
    unittest.main()

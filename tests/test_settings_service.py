"""设置服务测试：默认值、读改写、首启播种与导出。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.base import DatabaseTestCase

from everyday.models.settings import AppSettings
from everyday.services.settings_service import SettingsService


class AppSettingsTest(unittest.TestCase):
    def test_defaults_match_document(self):
        settings = AppSettings()
        self.assertFalse(settings.auto_start)
        self.assertTrue(settings.float_window_visible)
        self.assertEqual(settings.calendar_ics_path, "")
        self.assertEqual(settings.calendar_refresh_minutes, 30)

    def test_kv_roundtrip(self):
        original = AppSettings(
            auto_start=True,
            float_window_visible=False,
            float_window_left=123.5,
            float_window_top=-40.0,
            calendar_ics_path="https://example.com/basic.ics",
            calendar_refresh_minutes=15,
            log_level="DEBUG",
            startup_backup_enabled=False,
        )
        restored = AppSettings.from_kv(original.to_kv())
        self.assertEqual(restored, original)
        self.assertEqual(restored.log_level, "DEBUG")

    def test_from_kv_tolerates_missing_and_bad_values(self):
        restored = AppSettings.from_kv(
            {"auto_start": "yes", "float_window_left": "not-a-number", "calendar_refresh_minutes": "0"}
        )
        self.assertTrue(restored.auto_start)
        self.assertEqual(restored.float_window_left, 60.0)  # 回落到默认值
        self.assertEqual(restored.calendar_refresh_minutes, 1)  # 下限保护

    def test_merged_replaces_only_given_field(self):
        settings = AppSettings(float_window_left=10.0, float_window_top=20.0)
        merged = settings.merged(float_window_left=99.0)
        self.assertEqual(merged.float_window_left, 99.0)
        self.assertEqual(merged.float_window_top, 20.0)

    def test_merged_rejects_unknown_field(self):
        with self.assertRaises(TypeError):
            AppSettings().merged(不存在的设置项=1)


class SettingsServiceTest(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = SettingsService(self.settings)

    def test_empty_database_yields_defaults(self):
        self.assertEqual(self.service.load(), AppSettings())

    def test_save_then_load_roundtrip(self):
        self.service.save(AppSettings(auto_start=True, float_window_left=333.0))
        loaded = self.service.load()
        self.assertTrue(loaded.auto_start)
        self.assertEqual(loaded.float_window_left, 333.0)

    def test_update_changes_only_requested_field(self):
        self.service.save(AppSettings(float_window_left=10.0, float_window_top=20.0))
        updated = self.service.update(auto_start=True)
        self.assertTrue(updated.auto_start)
        self.assertEqual(updated.float_window_left, 10.0)
        self.assertEqual(updated.float_window_top, 20.0)

    def test_update_persists(self):
        self.service.update(calendar_ics_path="D:/cal/work.ics")
        self.assertEqual(self.service.load().calendar_ics_path, "D:/cal/work.ics")

    def test_first_run_detection(self):
        self.assertTrue(self.service.is_first_run())
        self.service.save(AppSettings())
        self.assertFalse(self.service.is_first_run())


class SettingsSeedingTest(DatabaseTestCase):
    def _write_template(self, payload: dict) -> Path:
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(payload, tmp, ensure_ascii=False)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink, missing_ok=True)
        return Path(tmp.name)

    def test_seed_reads_template_ignoring_comment_keys(self):
        template = self._write_template(
            {
                "_comment": "说明文字",
                "auto_start": True,
                "float_window_visible": False,
                "float_window_left": 200,
                "_float_window_left": "悬浮窗横坐标",
                "calendar_ics_path": "D:/cal/work.ics",
            }
        )
        service = SettingsService(self.settings, template_path=template)
        self.assertTrue(service.seed_from_template())

        loaded = service.load()
        self.assertTrue(loaded.auto_start)
        self.assertFalse(loaded.float_window_visible)
        self.assertEqual(loaded.float_window_left, 200.0)
        self.assertEqual(loaded.calendar_ics_path, "D:/cal/work.ics")

    def test_seed_is_skipped_on_second_run(self):
        template = self._write_template({"auto_start": True})
        service = SettingsService(self.settings, template_path=template)
        self.assertTrue(service.seed_from_template())
        self.assertFalse(service.seed_from_template())

    def test_seed_falls_back_to_defaults_when_template_missing(self):
        service = SettingsService(self.settings, template_path=Path("Z:/missing.json"))
        self.assertTrue(service.seed_from_template())
        self.assertEqual(service.load(), AppSettings())

    def test_seed_survives_malformed_template(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        tmp.write("{ 这不是合法 JSON")
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink, missing_ok=True)

        service = SettingsService(self.settings, template_path=Path(tmp.name))
        self.assertTrue(service.seed_from_template())
        self.assertEqual(service.load(), AppSettings())


class SettingsExportTest(DatabaseTestCase):
    def test_export_writes_readable_json(self):
        service = SettingsService(self.settings)
        service.save(AppSettings(auto_start=True, float_window_left=250.0))

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "exported.json"
            written = service.export_to_json(target)
            payload = json.loads(written.read_text(encoding="utf-8"))

        self.assertIs(payload["auto_start"], True)
        self.assertEqual(payload["float_window_left"], 250.0)
        self.assertEqual(payload["calendar_ics_path"], "")

    def test_export_creates_parent_directory(self):
        service = SettingsService(self.settings)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "dir" / "settings.json"
            written = service.export_to_json(target)
            self.assertTrue(written.is_file())


if __name__ == "__main__":
    unittest.main()

"""日历服务测试：数据源读取、缓存容错与查询接口。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from everyday.services.calendar_service import CalendarService, read_source
from everyday.services.ics_parser import CalendarEvent
from tests.base import DatabaseTestCase  # noqa: F401  (确保 src 已入 path)

WEEK_ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:evt-1
SUMMARY:团队周会
DTSTART:20261005T090000
DTEND:20261005T100000
LOCATION:会议室 A
END:VEVENT
BEGIN:VEVENT
UID:evt-2
SUMMARY:项目评审
DTSTART:20261006T140000
DTEND:20261006T153000
END:VEVENT
END:VCALENDAR
"""


class ReadSourceTest(unittest.TestCase):
    def test_reads_plain_file_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "work.ics"
            path.write_text(WEEK_ICS, encoding="utf-8")
            self.assertIn("团队周会", read_source(str(path)))

    def test_reads_file_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "work.ics"
            path.write_text(WEEK_ICS, encoding="utf-8")
            self.assertIn("团队周会", read_source(path.as_uri()))

    def test_empty_source_raises(self):
        with self.assertRaises(ValueError):
            read_source("   ")

    def test_missing_file_raises(self):
        with self.assertRaises(OSError):
            read_source("Z:/definitely/missing.ics")


class CalendarServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = CalendarService()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_ics(self, name: str = "work.ics") -> Path:
        path = self.root / name
        path.write_text(WEEK_ICS, encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    def test_refresh_loads_events(self):
        path = self._write_ics()
        self.assertTrue(self.service.refresh(str(path), day=date(2026, 10, 5)))
        self.assertIsNone(self.service.last_error)
        self.assertIsNotNone(self.service.last_loaded_at)
        self.assertEqual(len(self.service.upcoming(date(2026, 10, 5))), 2)

    def test_today_events_filters_by_day(self):
        path = self._write_ics()
        self.service.refresh(str(path), day=date(2026, 10, 5))

        today = self.service.today_events(date(2026, 10, 5))
        self.assertEqual([e.summary for e in today], ["团队周会"])
        self.assertEqual(
            [e.summary for e in self.service.today_events(date(2026, 10, 6))],
            ["项目评审"],
        )

    def test_empty_source_clears_cache(self):
        path = self._write_ics()
        self.service.refresh(str(path), day=date(2026, 10, 5))
        self.assertTrue(self.service.refresh("", day=date(2026, 10, 5)))
        self.assertEqual(self.service.upcoming(date(2026, 10, 5)), [])

    def test_failed_refresh_keeps_previous_data(self):
        path = self._write_ics()
        self.service.refresh(str(path), day=date(2026, 10, 5))
        before = len(self.service.upcoming(date(2026, 10, 5)))

        self.assertFalse(self.service.refresh("Z:/missing.ics", day=date(2026, 10, 5)))
        self.assertIsNotNone(self.service.last_error)
        self.assertEqual(len(self.service.upcoming(date(2026, 10, 5))), before)

    def test_malformed_ics_does_not_crash(self):
        path = self.root / "bad.ics"
        path.write_text("这不是一个 ICS 文件", encoding="utf-8")
        self.assertTrue(self.service.refresh(str(path), day=date(2026, 10, 5)))
        self.assertEqual(self.service.upcoming(date(2026, 10, 5)), [])

    def test_next_event_returns_first_not_yet_finished(self):
        path = self._write_ics()
        self.service.refresh(str(path), day=date(2026, 10, 5))

        upcoming = self.service.next_event(datetime(2026, 10, 5, 9, 30))
        self.assertEqual(upcoming.summary, "团队周会")

        after_first = self.service.next_event(datetime(2026, 10, 5, 10, 30))
        self.assertEqual(after_first.summary, "项目评审")

        none_left = self.service.next_event(datetime(2026, 11, 1, 0, 0))
        self.assertIsNone(none_left)

    def test_upcoming_is_sorted_by_start(self):
        path = self._write_ics()
        self.service.refresh(str(path), day=date(2026, 10, 5))
        starts = [e.start for e in self.service.upcoming(date(2026, 10, 5))]
        self.assertEqual(starts, sorted(starts))

    def test_recurring_events_are_expanded_into_window(self):
        path = self.root / "daily.ics"
        path.write_text(
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:d1\nSUMMARY:每日站会\n"
            "DTSTART:20261001T093000\nRRULE:FREQ=DAILY\nEND:VEVENT\nEND:VCALENDAR\n",
            encoding="utf-8",
        )
        self.service.refresh(str(path), day=date(2026, 10, 5))
        # 窗口为 10-05 ~ 10-11，共 7 天
        self.assertEqual(len(self.service.upcoming(date(2026, 10, 5))), 7)

    def test_replace_events_injects_and_sorts(self):
        self.service.replace_events(
            [
                CalendarEvent(uid="b", summary="B", start=datetime(2026, 10, 5, 14, 0)),
                CalendarEvent(uid="a", summary="A", start=datetime(2026, 10, 5, 9, 0)),
            ]
        )
        self.assertEqual(
            [e.summary for e in self.service.upcoming(date(2026, 10, 5))], ["A", "B"]
        )

    def test_source_reports_loaded_path(self):
        path = self._write_ics()
        self.service.refresh(str(path), day=date(2026, 10, 5))
        self.assertEqual(self.service.source, str(path))


if __name__ == "__main__":
    unittest.main()

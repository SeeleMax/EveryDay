"""ICS 解析器测试（文档 5.6 实时日历）。

覆盖：行折叠、属性参数、UTC 转换、文本转义、全天事件排他性、
RRULE 展开（含长序列快速前跳）、EXDATE 排除、窗口过滤与容错。
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from tests.base import DatabaseTestCase  # noqa: F401  (确保 src 已入 path)

from everyday.services.ics_parser import (
    MAX_OCCURRENCES_PER_EVENT,
    CalendarEvent,
    expand_recurrences,
    load_events,
    parse_ics,
    parse_ics_datetime,
    split_property,
    unescape_text,
    unfold_lines,
)

WINDOW_START = date(2026, 10, 5)
WINDOW_END = date(2026, 10, 11)


def wrap(*event_lines: str) -> str:
    """把若干 VEVENT 属性行包成一个完整的 VCALENDAR。"""
    body = "\r\n".join(event_lines)
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//EveryDay//Test//CN\r\n"
        "BEGIN:VEVENT\r\n"
        f"{body}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )


class LexicalLayerTest(unittest.TestCase):
    def test_unfold_joins_continuation_lines(self):
        text = "SUMMARY:很长\r\n 的标题\r\nUID:1\r\n"
        self.assertEqual(unfold_lines(text), ["SUMMARY:很长的标题", "UID:1"])

    def test_unfold_accepts_tab_continuation(self):
        self.assertEqual(unfold_lines("A:1\r\n\t2\r\n"), ["A:12"])

    def test_split_property_without_params(self):
        name, params, value = split_property("SUMMARY:团队周会")
        self.assertEqual(name, "SUMMARY")
        self.assertEqual(params, {})
        self.assertEqual(value, "团队周会")

    def test_split_property_with_params(self):
        name, params, value = split_property("DTSTART;TZID=Asia/Shanghai:20261005T090000")
        self.assertEqual(name, "DTSTART")
        self.assertEqual(params["TZID"], "Asia/Shanghai")
        self.assertEqual(value, "20261005T090000")

    def test_split_property_keeps_colons_inside_value(self):
        _, _, value = split_property("URL:https://example.com/a:b")
        self.assertEqual(value, "https://example.com/a:b")

    def test_split_property_rejects_garbage(self):
        self.assertEqual(split_property("这不是一行 ICS"), ("", {}, ""))

    def test_unescape_text(self):
        self.assertEqual(unescape_text(r"第一行\n第二行"), "第一行\n第二行")
        self.assertEqual(unescape_text(r"逗号\,分号\;反斜杠\\"), "逗号,分号;反斜杠\\")

    def test_unescape_text_handles_empty(self):
        self.assertEqual(unescape_text(""), "")


class DateTimeParsingTest(unittest.TestCase):
    def test_all_day_value(self):
        moment, all_day = parse_ics_datetime("20261005", {"VALUE": "DATE"})
        self.assertTrue(all_day)
        self.assertEqual(moment, datetime(2026, 10, 5, 0, 0))

    def test_all_day_value_without_explicit_param(self):
        _, all_day = parse_ics_datetime("20261005", {})
        self.assertTrue(all_day)

    def test_local_datetime(self):
        moment, all_day = parse_ics_datetime("20261005T090000", {})
        self.assertFalse(all_day)
        self.assertEqual(moment, datetime(2026, 10, 5, 9, 0))

    def test_datetime_without_seconds(self):
        moment, _ = parse_ics_datetime("20261005T0930", {})
        self.assertEqual(moment, datetime(2026, 10, 5, 9, 30))

    def test_utc_value_is_converted_to_local(self):
        moment, all_day = parse_ics_datetime("20261005T010000Z", {})
        expected = datetime.fromtimestamp(
            datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc).timestamp()
        )
        self.assertFalse(all_day)
        self.assertEqual(moment, expected.replace(microsecond=0))

    def test_invalid_value_raises(self):
        with self.assertRaises(ValueError):
            parse_ics_datetime("不是日期", {})


class ParseIcsTest(unittest.TestCase):
    def test_parses_single_event(self):
        text = wrap(
            "UID:evt-1",
            "SUMMARY:团队周会",
            "DTSTART:20261005T090000",
            "DTEND:20261005T100000",
            "LOCATION:会议室 A",
        )
        events, rules, exdates = parse_ics(text)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.uid, "evt-1")
        self.assertEqual(event.summary, "团队周会")
        self.assertEqual(event.start, datetime(2026, 10, 5, 9, 0))
        self.assertEqual(event.end, datetime(2026, 10, 5, 10, 0))
        self.assertEqual(event.location, "会议室 A")
        self.assertEqual(event.time_label, "09:00-10:00")
        self.assertEqual(rules, {})
        self.assertEqual(exdates, {})

    def test_event_without_uid_gets_generated_one(self):
        events, _, _ = parse_ics(wrap("SUMMARY:无 UID", "DTSTART:20261005T090000"))
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].uid.startswith("generated-"))

    def test_event_without_summary_gets_placeholder(self):
        events, _, _ = parse_ics(wrap("UID:1", "DTSTART:20261005T090000"))
        self.assertEqual(events[0].summary, "(无标题)")

    def test_event_without_dtstart_is_skipped(self):
        events, _, _ = parse_ics(wrap("UID:1", "SUMMARY:没有开始时间"))
        self.assertEqual(events, [])

    def test_event_with_unparsable_dtstart_is_skipped(self):
        events, _, _ = parse_ics(wrap("UID:1", "SUMMARY:坏时间", "DTSTART:坏掉的取值"))
        self.assertEqual(events, [])

    def test_non_event_lines_are_ignored(self):
        events, _, _ = parse_ics(
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nX-WR-CALNAME:工作日历\r\nEND:VCALENDAR\r\n"
        )
        self.assertEqual(events, [])

    def test_all_day_event_end_is_exclusive(self):
        events, _, _ = parse_ics(
            wrap(
                "UID:1",
                "SUMMARY:国庆假期",
                "DTSTART;VALUE=DATE:20261005",
                "DTEND;VALUE=DATE:20261006",
            )
        )
        event = events[0]
        self.assertTrue(event.all_day)
        self.assertEqual(event.time_label, "全天")
        self.assertEqual(event.last_day, date(2026, 10, 5))
        self.assertTrue(event.overlaps_day(date(2026, 10, 5)))
        self.assertFalse(event.overlaps_day(date(2026, 10, 6)))

    def test_multiple_events_all_parsed(self):
        text = (
            "BEGIN:VCALENDAR\r\n"
            "BEGIN:VEVENT\r\nUID:1\r\nSUMMARY:A\r\nDTSTART:20261005T090000\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:2\r\nSUMMARY:B\r\nDTSTART:20261005T110000\r\nEND:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        events, _, _ = parse_ics(text)
        self.assertEqual([e.summary for e in events], ["A", "B"])


class RecurrenceTest(unittest.TestCase):
    def _expand(self, *lines: str, start=WINDOW_START, end=WINDOW_END):
        events, rules, exdates = parse_ics(wrap(*lines))
        return expand_recurrences(events, rules, exdates, start, end)

    def test_daily_rule_fills_window(self):
        instances = self._expand(
            "UID:1",
            "SUMMARY:每日站会",
            "DTSTART:20261005T093000",
            "RRULE:FREQ=DAILY",
        )
        self.assertEqual(len(instances), 7)
        self.assertEqual(instances[0].start.date(), WINDOW_START)
        self.assertEqual(instances[-1].start.date(), WINDOW_END)

    def test_daily_rule_with_interval(self):
        instances = self._expand(
            "UID:1", "SUMMARY:隔天", "DTSTART:20261005T090000", "RRULE:FREQ=DAILY;INTERVAL=2"
        )
        self.assertEqual(
            [i.start.date() for i in instances],
            [date(2026, 10, 5), date(2026, 10, 7), date(2026, 10, 9), date(2026, 10, 11)],
        )

    def test_weekly_rule_with_interval(self):
        instances = self._expand(
            "UID:1",
            "SUMMARY:双周会",
            "DTSTART:20261005T100000",
            "RRULE:FREQ=WEEKLY;INTERVAL=2",
            start=date(2026, 10, 5),
            end=date(2026, 11, 2),
        )
        self.assertEqual(
            [i.start.date() for i in instances],
            [date(2026, 10, 5), date(2026, 10, 19), date(2026, 11, 2)],
        )

    def test_monthly_rule(self):
        instances = self._expand(
            "UID:1",
            "SUMMARY:月度对账",
            "DTSTART:20261005T200000",
            "RRULE:FREQ=MONTHLY",
            start=date(2026, 10, 1),
            end=date(2026, 12, 31),
        )
        self.assertEqual(
            [i.start.date() for i in instances],
            [date(2026, 10, 5), date(2026, 11, 5), date(2026, 12, 5)],
        )

    def test_until_stops_expansion(self):
        instances = self._expand(
            "UID:1",
            "SUMMARY:限期",
            "DTSTART:20261005T090000",
            "RRULE:FREQ=DAILY;UNTIL=20261007T090000",
        )
        self.assertEqual(len(instances), 3)

    def test_count_limits_expansion(self):
        instances = self._expand(
            "UID:1",
            "SUMMARY:三次",
            "DTSTART:20261005T090000",
            "RRULE:FREQ=DAILY;COUNT=3",
        )
        self.assertEqual(len(instances), 3)
        self.assertEqual(instances[-1].start.date(), date(2026, 10, 7))

    def test_long_running_series_fast_forwards_into_window(self):
        """11 年前开始的每日事件仍必须在窗口内正确展开。

        这是「快速前跳」优化的回归测试：若从序列头逐条迭代，
        会先撞上展开上限而返回 0 条。
        """
        instances = self._expand(
            "UID:1",
            "SUMMARY:老事件",
            "DTSTART:20151005T090000",
            "RRULE:FREQ=DAILY",
        )
        self.assertEqual(len(instances), 7)
        self.assertEqual(instances[0].start.date(), WINDOW_START)

    def test_occurrence_count_never_exceeds_guard(self):
        instances = self._expand(
            "UID:1",
            "SUMMARY:密集",
            "DTSTART:20261005T000000",
            "RRULE:FREQ=DAILY",
        )
        self.assertLessEqual(len(instances), MAX_OCCURRENCES_PER_EVENT)

    def test_unsupported_frequency_falls_back_to_single_occurrence(self):
        instances = self._expand(
            "UID:1", "SUMMARY:每年", "DTSTART:20261005T090000", "RRULE:FREQ=YEARLY"
        )
        self.assertEqual(len(instances), 1)

    def test_exdate_excludes_occurrence(self):
        instances = self._expand(
            "UID:1",
            "SUMMARY:每日",
            "DTSTART:20261005T090000",
            "RRULE:FREQ=DAILY",
            "EXDATE:20261007T090000",
        )
        dates = [i.start.date() for i in instances]
        self.assertEqual(len(instances), 6)
        self.assertNotIn(date(2026, 10, 7), dates)

    def test_multiple_exdate_lines_are_merged(self):
        instances = self._expand(
            "UID:1",
            "SUMMARY:每日",
            "DTSTART:20261005T090000",
            "RRULE:FREQ=DAILY",
            "EXDATE:20261006T090000",
            "EXDATE:20261008T090000",
        )
        dates = [i.start.date() for i in instances]
        self.assertEqual(len(instances), 5)
        self.assertNotIn(date(2026, 10, 6), dates)
        self.assertNotIn(date(2026, 10, 8), dates)

    def test_recurrence_preserves_duration(self):
        instances = self._expand(
            "UID:1",
            "SUMMARY:一小时",
            "DTSTART:20261005T090000",
            "DTEND:20261005T100000",
            "RRULE:FREQ=DAILY",
        )
        for instance in instances:
            self.assertEqual(instance.duration, timedelta(hours=1))
            self.assertEqual(instance.time_label, "09:00-10:00")


class WindowFilterTest(unittest.TestCase):
    def test_events_outside_window_are_dropped(self):
        events = load_events(
            wrap("UID:1", "SUMMARY:下个月", "DTSTART:20261105T090000"),
            WINDOW_START,
            WINDOW_END,
        )
        self.assertEqual(events, [])

    def test_event_starting_before_window_but_overlapping_is_kept(self):
        events = load_events(
            wrap(
                "UID:1",
                "SUMMARY:跨周项目",
                "DTSTART:20261001T090000",
                "DTEND:20261006T180000",
            ),
            WINDOW_START,
            WINDOW_END,
        )
        self.assertEqual(len(events), 1)

    def test_event_ending_before_window_is_dropped(self):
        events = load_events(
            wrap("UID:1", "SUMMARY:已结束", "DTSTART:20261001T090000", "DTEND:20261002T090000"),
            WINDOW_START,
            WINDOW_END,
        )
        self.assertEqual(events, [])

    def test_load_events_sorts_all_day_after_timed_within_same_day(self):
        text = (
            "BEGIN:VCALENDAR\r\n"
            "BEGIN:VEVENT\r\nUID:1\r\nSUMMARY:全天活动\r\n"
            "DTSTART;VALUE=DATE:20261005\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:2\r\nSUMMARY:早上会议\r\n"
            "DTSTART:20261005T090000\r\nEND:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        events = load_events(text, WINDOW_START, WINDOW_END)
        self.assertEqual([e.summary for e in events], ["早上会议", "全天活动"])


class CalendarEventTest(unittest.TestCase):
    def test_time_label_without_end(self):
        event = CalendarEvent(uid="1", summary="A", start=datetime(2026, 10, 5, 9, 0))
        self.assertEqual(event.time_label, "09:00")

    def test_overlaps_day_for_multi_day_event(self):
        event = CalendarEvent(
            uid="1",
            summary="出差",
            start=datetime(2026, 10, 3, 8, 0),
            end=datetime(2026, 10, 8, 18, 0),
        )
        self.assertTrue(event.overlaps_day(date(2026, 10, 5)))
        self.assertTrue(event.overlaps_day(date(2026, 10, 8)))
        self.assertFalse(event.overlaps_day(date(2026, 10, 9)))


if __name__ == "__main__":
    unittest.main()

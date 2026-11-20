import json
import unittest
from datetime import date, timedelta

from icalendar import Calendar

from test_course_import import importer, plugin


def weekly(summary="每周课", *, weekday=1, weeks=None, **kwargs):
    return {
        "summary": summary,
        "weekday": weekday,
        "weeks": [1, 4] if weeks is None else weeks,
        "first_period": 1,
        "last_period": 2,
        "location": "第一教室",
        **kwargs,
    }


class ImportWeeksTest(unittest.TestCase):
    def setUp(self):
        self.monday = date(2026, 8, 31)
        self.profile = {1: ("08:25", "09:10"), 2: ("09:15", "10:00")}

    def events(self, rows, *, model_monday=None, user_monday=None):
        payload = json.dumps({"first_week_monday": model_monday, "courses": rows})
        raw = importer.parse_model_result(payload, plugin.SHANGHAI_TZ, self.profile, user_monday)
        return Calendar.from_ical(raw).walk("VEVENT")

    def local_start(self, course):
        return course.decoded("dtstart").astimezone(plugin.SHANGHAI_TZ)

    def test_semester_expands_151_occurrences_and_preserves_periods(self):
        rows = [weekly(f"课程 {i}", weekday=(i % 7) + 1, weeks=list(range(1, 8 if i == 24 else 7)))
                for i in range(25)]
        events = self.events(rows, user_monday=self.monday)
        self.assertEqual(len(events), 151)
        self.assertEqual(len({str(event.get("uid")) for event in events}), 151)
        start = self.local_start(events[-1])
        self.assertEqual(start.date(), self.monday + timedelta(weeks=6, days=24 % 7))
        self.assertEqual(start.strftime("%H:%M"), "08:25")
        self.assertEqual(str(events[-1]["X-COURSE-LAST-PERIOD"]), "2")

    def test_mixed_dated_and_recurring_rows_preserve_explicit_weeks_and_anchor(self):
        weekly_row = weekly(weekday=7, weeks=[2, 12, 12])
        dated = {
            "summary": "单次课",
            "date": "2026-11-14",
            "start": "14:00",
            "end": "15:00",
            "weekday": None,
            "weeks": None,
        }
        events = self.events([weekly_row, dated], model_monday="2026-09-07", user_monday=self.monday)
        self.assertEqual([self.local_start(event).date() for event in events],
                         [date(2026, 9, 13), date(2026, 11, 22), date(2026, 11, 14)])

    def test_requires_valid_monday_and_rejects_bad_weekday_or_weeks(self):
        cases = (
            (weekly(), None),
            (weekly(), "2026-09-01"),
            (weekly(weekday=True), "2026-08-31"),
            (weekly(weekday=8), "2026-08-31"),
            (weekly(weeks=[0]), "2026-08-31"),
            (weekly(weeks=[61]), "2026-08-31"),
            (weekly(weeks=[True]), "2026-08-31"),
            (weekly(weeks=[3]), "9999-12-27"),
        )
        for row, monday in cases:
            with self.subTest(row=row, monday=monday), self.assertRaises(importer.ImportProblem):
                self.events([row], model_monday=monday)

    def test_caps_expanded_event_count_before_serialization(self):
        rows = [weekly(f"课程 {i}", weeks=list(range(1, 61))) for i in range(21)]
        with self.assertRaises(importer.ImportProblem):
            self.events(rows, user_monday=self.monday)

    def test_recognizes_only_an_explicit_valid_week_hint(self):
        self.assertEqual(importer.explicit_week_anchor("课表补充 第一周周一：2026-08-31"), self.monday)
        self.assertIsNone(importer.explicit_week_anchor("学期 2026-08-31"))
        with self.assertRaises(importer.ImportProblem):
            importer.explicit_week_anchor("课表补充 第一周周一 2026-09-01")


if __name__ == "__main__":
    unittest.main()

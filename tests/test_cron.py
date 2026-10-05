"""aio/cron.py: the BACKUP_SCHEDULE dialect.

    python3 -m unittest tests.test_cron
"""
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aio"))
import cron  # noqa: E402

D = datetime


class NextRunTest(unittest.TestCase):
    def check(self, expr, after, expected):
        self.assertEqual(cron.next_run(expr, after), expected, expr)

    def test_default_schedule(self):
        self.check("0 3 * * *", D(2026, 10, 5, 2, 59), D(2026, 10, 5, 3, 0))
        self.check("0 3 * * *", D(2026, 10, 5, 3, 0), D(2026, 10, 6, 3, 0))  # strictly after
        self.check("0 3 * * *", D(2026, 10, 5, 3, 0, 30), D(2026, 10, 6, 3, 0))

    def test_every_minute_and_steps(self):
        self.check("* * * * *", D(2026, 1, 1, 0, 0, 59), D(2026, 1, 1, 0, 1))
        self.check("*/15 * * * *", D(2026, 1, 1, 10, 7), D(2026, 1, 1, 10, 15))
        self.check("*/15 * * * *", D(2026, 1, 1, 10, 45), D(2026, 1, 1, 11, 0))
        self.check("10-20/5 * * * *", D(2026, 1, 1, 10, 11), D(2026, 1, 1, 10, 15))
        self.check("5/20 * * * *", D(2026, 1, 1, 10, 6), D(2026, 1, 1, 10, 25))  # a/n = a-max/n

    def test_lists_and_ranges(self):
        self.check("0 6,18 * * *", D(2026, 1, 1, 7, 0), D(2026, 1, 1, 18, 0))
        self.check("30 9-11 * * *", D(2026, 1, 1, 11, 30), D(2026, 1, 2, 9, 30))

    def test_rollovers(self):
        self.check("0 0 * * *", D(2026, 12, 31, 23, 59), D(2027, 1, 1, 0, 0))
        self.check("0 0 1 * *", D(2026, 1, 31, 12, 0), D(2026, 2, 1, 0, 0))
        self.check("0 12 1 1 *", D(2026, 1, 1, 12, 0), D(2027, 1, 1, 12, 0))
        self.check("0 0 31 * *", D(2026, 4, 1), D(2026, 5, 31))  # April has no 31st

    def test_leap_day(self):
        self.check("0 0 29 2 *", D(2026, 3, 1), D(2028, 2, 29))
        self.check("0 0 29 2 *", D(2096, 3, 1), D(2104, 2, 29))  # 2100 is not a leap year

    def test_sunday_is_zero_and_seven(self):
        sunday = D(2026, 10, 11, 3, 0)
        self.assertEqual(sunday.weekday(), 6)
        self.check("0 3 * * 0", D(2026, 10, 5), sunday)
        self.check("0 3 * * 7", D(2026, 10, 5), sunday)
        self.check("0 3 * * 1", D(2026, 10, 5, 3, 0), D(2026, 10, 12, 3, 0))  # Monday
        self.check("0 3 * * 5-7", D(2026, 10, 9, 3, 1), D(2026, 10, 10, 3, 0))  # Fri-Sun: Saturday

    def test_day_of_month_or_day_of_week_when_both_restricted(self):
        self.check("0 0 15 * 1", D(2026, 10, 5, 0, 0), D(2026, 10, 12, 0, 0))  # the 15th or any Monday
        self.check("0 0 15 * 1", D(2026, 10, 12, 0, 0), D(2026, 10, 15, 0, 0))
        self.check("0 0 15 * *", D(2026, 10, 5), D(2026, 10, 15))  # only one restricted: it alone decides
        # "*/2" counts as unrestricted, so both must match: an odd day that is also a Monday (the 12th is even)
        self.check("0 0 */2 * 1", D(2026, 10, 5, 0, 0), D(2026, 10, 19, 0, 0))

    def test_aliases(self):
        self.check("@daily", D(2026, 1, 1, 5), D(2026, 1, 2, 0, 0))
        self.check("@hourly", D(2026, 1, 1, 5, 30), D(2026, 1, 1, 6, 0))
        self.check("@weekly", D(2026, 10, 5), D(2026, 10, 11))
        self.check("@monthly", D(2026, 10, 5), D(2026, 11, 1))
        self.check("@yearly", D(2026, 10, 5), D(2027, 1, 1))

    def test_wall_clock_across_dst_is_plain_arithmetic(self):
        # naive times: a 02:30 that a DST jump skips is still returned (the caller fires when the wall clock passes it)
        self.check("30 2 * * *", D(2026, 3, 28, 12, 0), D(2026, 3, 29, 2, 30))


class ParseTest(unittest.TestCase):
    def test_off(self):
        for value in (None, "", "  ", "off", "OFF", " Off "):
            self.assertTrue(cron.is_off(value), repr(value))
        for value in ("0 3 * * *", "@daily", "never"):
            self.assertFalse(cron.is_off(value), repr(value))

    def test_off_is_not_parseable(self):
        for value in ("", "off"):
            with self.assertRaises(ValueError):
                cron.parse(value)

    def test_invalid_expressions(self):
        for bad in ("0 3 * *", "0 3 * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * 32 * *", "* * * 13 *",
                    "* * * * 8", "a * * * *", "*/0 * * * *", "*/ * * * *", "5-1 * * * *", "1,,2 * * * *",
                    "-1 * * * *", "* * * * mon", "@sometimes", "1.5 * * * *"):
            with self.assertRaises(ValueError, msg=bad) as ctx:
                cron.parse(bad)
            self.assertIn("Invalid schedule", str(ctx.exception))

    def test_never_runs(self):
        with self.assertRaises(ValueError):
            cron.next_run("0 0 30 2 *", D(2026, 1, 1))

    def test_parse_normalises_text(self):
        self.assertEqual(cron.parse("  0   3  * * *  ").text, "0 3 * * *")
        self.assertEqual(cron.parse("@daily").text, "0 0 * * *")


if __name__ == "__main__":
    unittest.main()

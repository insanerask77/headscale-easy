"""Five-field cron expressions (stand-in for Block 2's aio/cron.py; same signatures).

Fields: minute hour day-of-month month day-of-week. Each accepts ``*``, ``a``,
``a,b``, ``a-b``, ``*/n`` and ``a-b/n``; day-of-week is 0-7 (0 and 7 are Sunday).
``off`` (or an empty string) disables the schedule. When both day-of-month and
day-of-week are restricted, either one matching is enough. Standard library only.
"""
from __future__ import annotations

import datetime as dt
from typing import NamedTuple

_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_NAMES = ("minute", "hour", "day of month", "month", "day of week")


class Cron(NamedTuple):
    minutes: frozenset
    hours: frozenset
    days: frozenset
    months: frozenset
    weekdays: frozenset  # 0 = Sunday .. 6 = Saturday
    any_day: bool
    any_weekday: bool


def is_off(expr: str) -> bool:
    return (expr or "").strip().lower() in ("", "off")


def _field(text: str, lo: int, hi: int, name: str) -> frozenset:
    out = set()
    for part in text.split(","):
        base, sep, step_text = part.partition("/")
        if sep and not step_text.isdigit():
            raise ValueError("invalid step in the %s field: %r" % (name, part))
        step = int(step_text) if sep else 1
        if step < 1:
            raise ValueError("invalid step in the %s field: %r" % (name, part))
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, _dash, b = base.partition("-")
            if not (a.isdigit() and b.isdigit()):
                raise ValueError("invalid range in the %s field: %r" % (name, part))
            start, end = int(a), int(b)
        elif base.isdigit():
            start = int(base)
            end = hi if sep else start
        else:
            raise ValueError("invalid value in the %s field: %r" % (name, part))
        if not (lo <= start <= hi and lo <= end <= hi) or start > end:
            raise ValueError("the %s field must be between %d and %d: %r" % (name, lo, hi, part))
        out.update(range(start, end + 1, step))
    return frozenset(out)


def parse(expr: str) -> Cron:
    fields = (expr or "").split()
    if len(fields) != 5:
        raise ValueError("a schedule needs five fields (minute hour day month weekday), e.g. 0 3 * * *")
    sets = [_field(f, lo, hi, name) for f, (lo, hi), name in zip(fields, _RANGES, _NAMES)]
    weekdays = frozenset(d % 7 for d in sets[4])
    return Cron(sets[0], sets[1], sets[2], sets[3], weekdays, fields[2] == "*", fields[4] == "*")


def _day_matches(c: Cron, day: dt.date) -> bool:
    dom = day.day in c.days
    dow = (day.weekday() + 1) % 7 in c.weekdays
    if c.any_day and c.any_weekday:
        return True
    if c.any_day:
        return dow
    if c.any_weekday:
        return dom
    return dom or dow


def next_run(expr: str, after: dt.datetime) -> dt.datetime:
    """First run strictly after ``after`` (naive local time)."""
    c = parse(expr)
    start = after.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    day = start.date()
    for _ in range(366 * 5):
        if day.month in c.months and _day_matches(c, day):
            for hour in sorted(c.hours):
                for minute in sorted(c.minutes):
                    cand = dt.datetime.combine(day, dt.time(hour, minute))
                    if cand >= start:
                        return cand
        day += dt.timedelta(days=1)
    raise ValueError("the schedule never runs: %r" % expr)

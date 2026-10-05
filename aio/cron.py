"""aio/cron.py: the small cron dialect used by BACKUP_SCHEDULE. Standard library only.

Five fields (minute hour day-of-month month day-of-week), each ``*``, ``a``,
``a,b``, ``a-b``, ``*/n``, ``a-b/n`` or ``a/n`` (= ``a-max/n``); day-of-week
0-7 (0 and 7 are Sunday). The usual rule for the two day fields: when both are
restricted, either one matching is enough. Aliases: @hourly @daily @midnight
@weekly @monthly @yearly @annually. ``off`` (or an empty value) disables it.

Times are naive local times; the caller decides what "local" means (TZ).
``next_run`` is wall-clock arithmetic: a time skipped by a DST jump is simply
reached when the wall clock passes it, and a repeated hour fires once.
"""
from __future__ import annotations

from datetime import datetime, timedelta

ALIASES = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}

# name, lowest, highest
FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("day of month", 1, 31), ("month", 1, 12), ("day of week", 0, 7))

# Feb 29 can be 8 years away (1896 -> 1904, 2096 -> 2104)
_MAX_DAYS = 366 * 9


class Cron:
    def __init__(self, minutes, hours, days, months, weekdays, dom_star, dow_star, text):
        self.minutes, self.hours = sorted(minutes), sorted(hours)
        self.days, self.months, self.weekdays = days, months, weekdays
        self.dom_star, self.dow_star = dom_star, dow_star
        self.text = text

    def day_matches(self, day) -> bool:
        if day.month not in self.months:
            return False
        dom = day.day in self.days
        dow = (day.weekday() + 1) % 7 in self.weekdays  # Python: Monday = 0; cron: Sunday = 0
        if not self.dom_star and not self.dow_star:
            return dom or dow
        return dom and dow


def is_off(expr) -> bool:
    return expr is None or str(expr).strip().lower() in ("", "off")


def _number(text, name, lo, hi, expr):
    if not text.isdigit():
        raise ValueError("Invalid schedule %r: %r is not a number in the %s field." % (expr, text, name))
    value = int(text)
    if not lo <= value <= hi:
        raise ValueError("Invalid schedule %r: %s must be between %d and %d, not %d." % (expr, name, lo, hi, value))
    return value


def _field(text, name, lo, hi, expr) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        if not part:
            raise ValueError("Invalid schedule %r: empty value in the %s field." % (expr, name))
        base, slash, step_text = part.partition("/")
        step = 1
        if slash:
            step = _number(step_text, name, 1, hi, expr) if step_text else 0
            if step < 1:
                raise ValueError("Invalid schedule %r: the step in the %s field must be at least 1." % (expr, name))
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, _, b = base.partition("-")
            start, end = _number(a, name, lo, hi, expr), _number(b, name, lo, hi, expr)
            if start > end:
                raise ValueError("Invalid schedule %r: the range %s is backwards in the %s field." % (expr, base, name))
        else:
            start = _number(base, name, lo, hi, expr)
            end = hi if slash else start
        values.update(range(start, end + 1, step))
    return values


def parse(expr: str) -> Cron:
    text = (expr or "").strip()
    if is_off(text):
        raise ValueError("Invalid schedule: it is empty or 'off', there is nothing to run.")
    text = ALIASES.get(text.lower(), text)
    parts = text.split()
    if len(parts) != 5:
        raise ValueError("Invalid schedule %r: expected 5 fields (minute hour day-of-month month day-of-week), "
                         "got %d." % (expr, len(parts)))
    sets = [_field(p, name, lo, hi, expr) for p, (name, lo, hi) in zip(parts, FIELDS)]
    weekdays = {0 if d == 7 else d for d in sets[4]}
    return Cron(sets[0], sets[1], sets[2], sets[3], weekdays,
                parts[2].startswith("*"), parts[4].startswith("*"), " ".join(parts))


def next_run(expr: str, after: datetime) -> datetime:
    """The first matching minute strictly after ``after`` (naive local time)."""
    cron = parse(expr)
    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = start.replace(hour=0, minute=0)
    for _ in range(_MAX_DAYS):
        if cron.day_matches(day):
            for hour in cron.hours:
                for minute in cron.minutes:
                    candidate = day.replace(hour=hour, minute=minute)
                    if candidate >= start:
                        return candidate
        day += timedelta(days=1)
    raise ValueError("Invalid schedule %r: it never runs (for example February 30)." % expr)

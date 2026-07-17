from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

_FIELD_RANGES = (
    (0, 59),
    (0, 23),
    (1, 31),
    (1, 12),
    (0, 7),
)


@dataclass(frozen=True)
class CronSchedule:
    expression: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]
    dom_any: bool
    dow_any: bool


def parse_interval_seconds(text: str) -> tuple[float | None, str]:
    value = (text or "").strip().lower()
    if not value:
        return None, ""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd])", value)
    if not match:
        return None, ""
    amount = float(match.group(1))
    unit = match.group(2)
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    seconds = amount * multiplier
    if seconds < 60:
        seconds = 60
    return seconds, value


def split_loop_interval_and_prompt(text: str) -> tuple[float | None, str, str | None]:
    raw = (text or "").strip()
    if not raw:
        return None, "", None
    parts = raw.split(maxsplit=1)
    leading, token = parse_interval_seconds(parts[0])
    if leading is not None:
        return leading, parts[1].strip() if len(parts) > 1 else "", token
    trailing = re.search(
        r"(?:^|\s)every\s+(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d)\s*$",
        raw,
        re.I,
    )
    if trailing:
        amount = float(trailing.group(1))
        unit = trailing.group(2).lower()
        if unit.startswith(("second", "sec")) or unit == "s":
            multiplier = 1
        elif unit.startswith(("minute", "min")) or unit == "m":
            multiplier = 60
        elif unit.startswith(("hour", "hr")) or unit == "h":
            multiplier = 3600
        else:
            multiplier = 86400
        seconds = max(60.0, amount * multiplier)
        prompt = raw[: trailing.start()].strip()
        return seconds, prompt, trailing.group(0).strip()
    return None, raw, None


def interval_to_cron(seconds: float) -> str | None:
    minutes = int((seconds + 59) // 60)
    if minutes <= 0:
        return None
    if minutes < 60 and 60 % minutes == 0:
        return f"*/{minutes} * * * *"
    if minutes == 60:
        return "0 * * * *"
    if minutes % 60 == 0:
        hours = minutes // 60
        if 24 % hours == 0:
            return f"0 */{hours} * * *"
    return None


def parse_cron(expression: str) -> CronSchedule:
    fields = (expression or "").split()
    if len(fields) != 5:
        raise ValueError("cron expression must have 5 fields")
    parsed = []
    any_flags = []
    for field, bounds in zip(fields, _FIELD_RANGES):
        values, is_any = _parse_field(field, bounds)
        parsed.append(values)
        any_flags.append(is_any)
    dow = frozenset(0 if value == 7 else value for value in parsed[4])
    return CronSchedule(
        expression=" ".join(fields),
        minutes=frozenset(parsed[0]),
        hours=frozenset(parsed[1]),
        days_of_month=frozenset(parsed[2]),
        months=frozenset(parsed[3]),
        days_of_week=dow,
        dom_any=any_flags[2],
        dow_any=any_flags[4],
    )


def next_cron_fire(expression: str, after: float | None = None) -> float:
    schedule = parse_cron(expression)
    current = datetime.fromtimestamp(after or time.time()).replace(
        second=0, microsecond=0
    ) + timedelta(minutes=1)
    deadline = current + timedelta(days=366)
    while current <= deadline:
        if _matches(schedule, current):
            return current.timestamp()
        current += timedelta(minutes=1)
    raise ValueError("cron expression did not match within one year")


def _parse_field(field: str, bounds: tuple[int, int]) -> tuple[frozenset[int], bool]:
    if re.search(r"[A-Za-z?LW#]", field):
        raise ValueError(f"unsupported cron field syntax: {field}")
    values: set[int] = set()
    for part in field.split(","):
        if not part:
            raise ValueError(f"invalid empty cron field part in {field}")
        values.update(_expand_part(part, bounds))
    if not values:
        raise ValueError(f"cron field produced no values: {field}")
    return frozenset(values), field == "*"


def _expand_part(part: str, bounds: tuple[int, int]) -> Iterable[int]:
    low, high = bounds
    if "/" in part:
        base, step_text = part.split("/", 1)
        step = int(step_text)
        if step <= 0:
            raise ValueError("cron step must be positive")
    else:
        base, step = part, 1
    if base == "*":
        start, end = low, high
    elif "-" in base:
        start_text, end_text = base.split("-", 1)
        start, end = int(start_text), int(end_text)
    else:
        start = end = int(base)
    if start < low or end > high or start > end:
        raise ValueError(f"cron value out of range: {part}")
    return range(start, end + 1, step)


def _matches(schedule: CronSchedule, value: datetime) -> bool:
    if value.minute not in schedule.minutes:
        return False
    if value.hour not in schedule.hours:
        return False
    if value.month not in schedule.months:
        return False
    dom_match = value.day in schedule.days_of_month
    dow_match = ((value.weekday() + 1) % 7) in schedule.days_of_week
    if schedule.dom_any and schedule.dow_any:
        day_match = True
    elif schedule.dom_any:
        day_match = dow_match
    elif schedule.dow_any:
        day_match = dom_match
    else:
        day_match = dom_match or dow_match
    return day_match

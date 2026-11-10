"""Deterministic natural-language date handling.

These helpers never guess: anything they cannot pin down is reported in `ambiguities`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
_MONTH_RE = "|".join(m[:3] + m[3:].replace(m[3:], f"(?:{m[3:]})?") if len(m) > 3 else m for m in MONTHS)
PERIODS = {"morning": (9, 12), "afternoon": (13, 17), "evening": (17, 20)}


def get_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


@dataclass
class ParsedWhen:
    date: date | None = None
    start: time | None = None
    end: time | None = None
    period: str | None = None
    duration_minutes: int | None = None
    date_text: str | None = None
    ambiguities: list[str] = field(default_factory=list)


def _next_weekday(base: date, weekday: int, modifier: str | None) -> date:
    delta = (weekday - base.weekday()) % 7
    if delta == 0:
        delta = 7 if modifier != "this" else 0
    return base + timedelta(days=delta)


def parse_date_phrase(text: str, base: date) -> tuple[date | None, str | None]:
    t = text.lower()
    if m := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t):
        try:
            return date(int(m[1]), int(m[2]), int(m[3])), m[0]
        except ValueError:
            return None, None
    if m := re.search(r"\b(?:the\s+)?day after tomorrow\b", t):
        return base + timedelta(days=2), m[0]
    if m := re.search(r"\btomorrow\b", t):
        return base + timedelta(days=1), m[0]
    if m := re.search(r"\b(today|tonight)\b", t):
        return base, m[0]
    if m := re.search(r"\bin (\d{1,3}) days?\b", t):
        return base + timedelta(days=int(m[1])), m[0]
    if m := re.search(r"\bnext week\b", t):
        return None, m[0]
    if m := re.search(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", t):
        return _month_day(m[1], int(m[2]), m[3], base), m[0]
    if m := re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_RE})\.?(?:,?\s+(\d{{4}}))?\b", t):
        return _month_day(m[2], int(m[1]), m[3], base), m[0]
    if m := re.search(r"\b(?:(next|this|on|by|before|until)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t):
        mod = m[1] if m[1] in ("next", "this") else None
        return _next_weekday(base, WEEKDAYS.index(m[2]), mod), m[0]
    return None, None


def _month_day(month_text: str, day: int, year_text: str | None, base: date) -> date | None:
    month = next(i for i, name in enumerate(MONTHS, 1) if name.startswith(month_text[:3]))
    try:
        candidate = date(int(year_text) if year_text else base.year, month, day)
        if not year_text and candidate < base:
            candidate = date(base.year + 1, month, day)
        return candidate
    except ValueError:
        return None


def _to_time(hour: int, minute: int, meridiem: str | None) -> time | None:
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    return time(hour, minute) if 0 <= hour < 24 and 0 <= minute < 60 else None


def parse_when(text: str, base: date) -> ParsedWhen:
    t = text.lower().replace("a.m.", "am").replace("p.m.", "pm")
    parsed = ParsedWhen()
    parsed.date, parsed.date_text = parse_date_phrase(t, base)
    if parsed.date is None:
        if parsed.date_text == "next week":
            parsed.ambiguities.append("Which day next week?")
        else:
            parsed.ambiguities.append("What date should this be on?")

    if m := re.search(r"\bfor\s+(\d+(?:\.\d+)?|an?|one|two|half an?)\s*(hours?|hrs?|h|minutes?|mins?|m)\b", t):
        words = {"a": 1, "an": 1, "one": 1, "two": 2}
        qty = words.get(m[1])
        if qty is None:
            qty = 0.5 if m[1].startswith("half") else float(m[1])
        parsed.duration_minutes = int(qty * (60 if m[2].startswith("h") else 1))
    elif re.search(r"\bhalf an hour\b", t):
        parsed.duration_minutes = 30

    if m := re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|to|until)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t):
        first_meridiem = m[3] or m[6]
        parsed.start = _to_time(int(m[1]), int(m[2] or 0), first_meridiem)
        parsed.end = _to_time(int(m[4]), int(m[5] or 0), m[6])
    elif m := re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t):
        parsed.start = _to_time(int(m[1]), int(m[2] or 0), m[3])
    elif m := re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t):
        parsed.start = time(int(m[1]), int(m[2]))
    elif "noon" in t or "midday" in t:
        parsed.start = time(12, 0)
    elif m := re.search(r"\bat\s+(\d{1,2})\b(?!\s*(?:/|-|\.))", t):
        hour = int(m[1])
        if 1 <= hour <= 12:
            parsed.ambiguities.append(f"Did you mean {hour} AM or {hour} PM?")
        elif hour < 24:
            parsed.start = time(hour, 0)
    for period in PERIODS:
        if period in t:
            parsed.period = period
    return parsed


DEADLINE_RE = re.compile(
    r"(?:\b(?:by|due(?:\s+(?:on|by))?|deadline(?:\s+(?:is|of|on))?:?|before|no later than|submit(?:ted)?\s+by)\s+)"
    r"((?:end of day\s+|eod\s+|the\s+)?(?:(?:next|this)\s+)?"
    rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|today|tonight|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    rf"(?:{_MONTH_RE})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH_RE})))",
    re.IGNORECASE,
)


def extract_deadlines(text: str, base: date) -> list[dict]:
    found: dict[str, dict] = {}
    for match in DEADLINE_RE.finditer(text or ""):
        when, _ = parse_date_phrase(match[1], base)
        if when:
            found.setdefault(when.isoformat(), {"text": match[0].strip(), "date": when.isoformat()})
    return sorted(found.values(), key=lambda d: d["date"])


def extract_all_dates(text: str, base: date) -> list[dict]:
    results = extract_deadlines(text, base)
    seen = {r["date"] for r in results}
    for m in re.finditer(rf"\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b(?:{_MONTH_RE})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b", text or "", re.I):
        when, _ = parse_date_phrase(m[0], base)
        if when and when.isoformat() not in seen:
            seen.add(when.isoformat())
            results.append({"text": m[0], "date": when.isoformat()})
    return sorted(results, key=lambda d: d["date"])


def local_end_of_day(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time(17, 0), zone).astimezone(UTC)


def local_day_bounds(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, zone)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def to_utc(value: datetime, zone_name: str | None) -> datetime:
    """Interpret naive datetimes in the user's zone."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=get_zone(zone_name))
    return value.astimezone(UTC)

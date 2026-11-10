"""Calendar reads, availability, conflict detection and event proposals. Times are stored in UTC."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound, ValidationFailed
from app.integrations import factory
from app.models import ApprovalRequest, CalendarEvent, UserPreference
from app.services.approvals import propose
from app.utils.dates import PERIODS, get_zone, local_day_bounds


def _prefs(db: Session, user_id: uuid.UUID) -> UserPreference:
    return db.scalar(select(UserPreference).where(UserPreference.user_id == user_id))


def get_owned(db: Session, user_id: uuid.UUID, event_id: uuid.UUID) -> CalendarEvent:
    event = db.scalar(select(CalendarEvent).where(CalendarEvent.id == event_id, CalendarEvent.user_id == user_id))
    if event is None:
        raise NotFound("Event not found.")
    return event


def sync_range(db: Session, user_id: uuid.UUID, start: datetime, end: datetime) -> dict:
    """Mirror live events into the local cache. Demo events already live there."""
    provider = factory.calendar_provider(db, user_id)
    if provider.simulated:
        return {"synced": 0, "simulated": True}
    remote = provider.list_events(start, end)
    existing = {
        e.external_id: e
        for e in db.scalars(select(CalendarEvent).where(CalendarEvent.user_id == user_id, CalendarEvent.is_demo.is_(False), CalendarEvent.external_id.in_([r.external_id for r in remote]))).all()
    }
    for item in remote:
        if item.end_at <= item.start_at:
            continue
        row = existing.get(item.external_id)
        if row is None:
            row = CalendarEvent(user_id=user_id, external_id=item.external_id, source="google")
            db.add(row)
        row.title, row.description, row.location = item.title[:255], item.description, item.location
        row.start_at, row.end_at, row.timezone, row.attendees, row.status = item.start_at, item.end_at, item.timezone, item.attendees, item.status
    db.commit()
    return {"synced": len(remote), "simulated": False}


def events_between(db: Session, user_id: uuid.UUID, start: datetime, end: datetime, include_proposed: bool = False) -> list[CalendarEvent]:
    statuses = ("confirmed", "proposed") if include_proposed else ("confirmed",)
    return list(db.scalars(
        select(CalendarEvent)
        .where(CalendarEvent.user_id == user_id, CalendarEvent.is_demo.is_(factory.is_demo(db, user_id)), CalendarEvent.status.in_(statuses), CalendarEvent.end_at > start, CalendarEvent.start_at < end)
        .order_by(CalendarEvent.start_at)
    ).all())


def _sync_best_effort(db: Session, user_id: uuid.UUID, start: datetime, end: datetime) -> str | None:
    try:
        sync_range(db, user_id, start, end)
        return None
    except AppError as exc:
        return exc.message


def conflicts(db: Session, user_id: uuid.UUID, start: datetime, end: datetime, exclude: uuid.UUID | None = None) -> list[CalendarEvent]:
    _sync_best_effort(db, user_id, start, end)
    return [e for e in events_between(db, user_id, start, end) if e.id != exclude]


def free_slots(db: Session, user_id: uuid.UUID, day: date, duration_minutes: int, period: str | None = None, limit: int = 6) -> dict:
    prefs = _prefs(db, user_id)
    zone = get_zone(prefs.timezone)
    start_hour, end_hour = PERIODS.get(period or "", (prefs.work_start_hour, prefs.work_end_hour))
    window_start = datetime.combine(day, time(start_hour), zone)
    window_end = datetime.combine(day, time(end_hour), zone)
    day_start, day_end = local_day_bounds(day, zone)
    warning = _sync_best_effort(db, user_id, day_start, day_end)
    busy = [(e.start_at.astimezone(zone), e.end_at.astimezone(zone)) for e in events_between(db, user_id, day_start, day_end, include_proposed=True)]
    now = datetime.now(zone)
    cursor = max(window_start, now.replace(second=0, microsecond=0) + timedelta(minutes=(15 - now.minute % 15) % 15)) if day == now.date() else window_start
    step = timedelta(minutes=30)
    length = timedelta(minutes=duration_minutes)
    slots = []
    while cursor + length <= window_end and len(slots) < limit:
        if not any(cursor < b_end and cursor + length > b_start for b_start, b_end in busy):
            slots.append({
                "start": cursor.isoformat(), "end": (cursor + length).isoformat(),
                "start_local": cursor.strftime("%H:%M"), "end_local": (cursor + length).strftime("%H:%M"),
                "label": f"{cursor:%H:%M}–{(cursor + length):%H:%M}",
            })
        cursor += step
    return {"date": day.isoformat(), "timezone": prefs.timezone, "slots": slots, "warning": warning}


def propose_update(
    db: Session, user_id: uuid.UUID, event: CalendarEvent, *, title: str, start: datetime, end: datetime, description: str = "", location: str | None = None,
    attendees: list[str] | None = None, requested_by: str = "user", origin: str = "calendar", conversation_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None, tainted: bool = False,
) -> ApprovalRequest:
    if event.status != "confirmed":
        raise ValidationFailed("Only confirmed events can be updated.")
    if end <= start:
        raise ValidationFailed("The event must end after it starts.")
    tz = event.timezone or _prefs(db, user_id).timezone
    zone = get_zone(tz)
    payload = {
        "event_id": str(event.id), "title": title, "description": description, "location": location, "start_at": start.astimezone(zone).isoformat(),
        "end_at": end.astimezone(zone).isoformat(), "timezone": tz, "attendees": attendees or [],
    }
    return propose(
        db, user_id, "update_calendar_event", payload, title=f"Update event: {title}", description=f"{start.astimezone(zone):%a %d %b %Y, %H:%M} ({tz})",
        requested_by=requested_by, origin=origin, conversation_id=conversation_id, run_id=run_id, derived_from_untrusted=tainted,
    )


def propose_event(
    db: Session, user_id: uuid.UUID, *, title: str, start: datetime, end: datetime, description: str = "", location: str | None = None,
    attendees: list[str] | None = None, timezone: str | None = None, requested_by: str = "user", origin: str = "calendar",
    conversation_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None, tainted: bool = False, source: str = "ai",
) -> tuple[CalendarEvent, ApprovalRequest]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValidationFailed("Event times must include a timezone.")
    if end <= start:
        raise ValidationFailed("The event must end after it starts.")
    tz = timezone or _prefs(db, user_id).timezone
    event = CalendarEvent(
        user_id=user_id, title=title[:255], description=description, location=location, start_at=start, end_at=end, timezone=tz,
        attendees=attendees or [], status="proposed", source=source, is_demo=factory.is_demo(db, user_id),
    )
    db.add(event)
    db.flush()
    zone = get_zone(tz)
    payload = {
        "title": title, "description": description, "location": location, "start_at": start.astimezone(zone).isoformat(), "end_at": end.astimezone(zone).isoformat(),
        "timezone": tz, "attendees": attendees or [], "event_id": str(event.id),
    }
    approval = propose(
        db, user_id, "create_calendar_event", payload, title=f"Create event: {title}", description=f"{start.astimezone(zone):%a %d %b %Y, %H:%M} ({tz})",
        requested_by=requested_by, origin=origin, conversation_id=conversation_id, run_id=run_id, derived_from_untrusted=tainted,
    )
    return event, approval

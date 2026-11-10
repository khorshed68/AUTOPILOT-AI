"""Chooses simulated or live providers per user, based on their mode and connection state."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import IntegrationNotConnected
from app.integrations.demo import DemoCalendarProvider, DemoDriveProvider, DemoEmailProvider
from app.integrations.google import GmailProvider, GoogleCalendarProvider, GoogleClient, GoogleDriveProvider
from app.models import OAuthAccount, UserPreference


def is_demo(db: Session, user_id: uuid.UUID) -> bool:
    mode = db.scalar(select(UserPreference.integration_mode).where(UserPreference.user_id == user_id))
    return mode != "live"


def get_google_account(db: Session, user_id: uuid.UUID) -> OAuthAccount | None:
    return db.scalar(select(OAuthAccount).where(OAuthAccount.user_id == user_id, OAuthAccount.provider == "google"))


def _google(db: Session, user_id: uuid.UUID, service: str) -> GoogleClient:
    account = get_google_account(db, user_id)
    if account is None:
        raise IntegrationNotConnected(f"{service} is not connected. Connect Google on the Integrations page, or switch to demo mode.")
    return GoogleClient(db, account)


def email_provider(db: Session, user_id: uuid.UUID):
    return DemoEmailProvider(db, user_id) if is_demo(db, user_id) else GmailProvider(_google(db, user_id, "Gmail"))


def calendar_provider(db: Session, user_id: uuid.UUID):
    return DemoCalendarProvider(db, user_id) if is_demo(db, user_id) else GoogleCalendarProvider(_google(db, user_id, "Google Calendar"))


def drive_provider(db: Session, user_id: uuid.UUID):
    return DemoDriveProvider(db, user_id) if is_demo(db, user_id) else GoogleDriveProvider(_google(db, user_id, "Google Drive"))

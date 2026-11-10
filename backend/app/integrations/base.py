"""Provider contracts. Every mutation returns an ExternalResult built from the provider's confirmation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass
class ExternalResult:
    external_id: str | None
    simulated: bool
    provider: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"external_id": self.external_id, "simulated": self.simulated, "provider": self.provider, **self.details}


@dataclass
class RemoteEmail:
    external_id: str
    thread_id: str | None
    sender_name: str | None
    sender_email: str
    recipients: list[str]
    subject: str
    snippet: str
    body: str
    received_at: datetime
    labels: list[str]
    message_id: str | None = None


@dataclass
class EmailPage:
    emails: list[RemoteEmail]
    next_page_token: str | None


@dataclass
class RemoteEvent:
    external_id: str
    title: str
    description: str
    location: str | None
    start_at: datetime
    end_at: datetime
    timezone: str
    attendees: list[str]
    status: str = "confirmed"


@dataclass
class RemoteFile:
    file_id: str
    name: str
    mime_type: str
    modified_at: str | None = None


class EmailProvider(Protocol):
    name: str
    simulated: bool

    def fetch_page(self, page_token: str | None, max_results: int) -> EmailPage: ...

    def send(self, to: list[str], cc: list[str], subject: str, body: str, thread_id: str | None, in_reply_to: str | None) -> ExternalResult: ...


class CalendarProvider(Protocol):
    name: str
    simulated: bool

    def list_events(self, start: datetime, end: datetime) -> list[RemoteEvent]: ...

    def create_event(self, payload: dict, idempotency_key: str) -> ExternalResult: ...

    def update_event(self, external_id: str, payload: dict) -> ExternalResult: ...

    def delete_event(self, external_id: str) -> ExternalResult: ...


class DriveProvider(Protocol):
    name: str
    simulated: bool

    def list_files(self, query: str | None, page_size: int) -> list[RemoteFile]: ...

    def download(self, file_id: str) -> tuple[bytes, str, str]: ...

    def share(self, file_id: str, email: str, role: str) -> ExternalResult: ...

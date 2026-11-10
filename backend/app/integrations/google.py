"""Live Google integrations (OAuth 2.0 + PKCE, Gmail, Calendar, Drive) over the REST APIs."""
from __future__ import annotations

import base64
import hashlib
import logging
import re
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ExternalServiceError, IntegrationNotConnected
from app.core.security import decrypt_secret, encrypt_secret
from app.database.base import utcnow
from app.integrations.base import EmailPage, ExternalResult, RemoteEmail, RemoteEvent, RemoteFile
from app.integrations.http import raise_for_status, request_with_retry
from app.models import OAuthAccount
from app.utils.text import truncate

logger = logging.getLogger("autopilot.google")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR = "https://www.googleapis.com/calendar/v3/calendars/primary"
DRIVE = "https://www.googleapis.com/drive/v3/files"
SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.readonly",
]
# Sharing needs write access to file metadata, which drive.readonly does not grant.
OPTIONAL_DRIVE_SHARE_SCOPE = "https://www.googleapis.com/auth/drive.file"


def build_auth_url(state: str, code_challenge: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join([*SCOPES, OPTIONAL_DRIVE_SHARE_SCOPE]),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str, verifier: str) -> dict:
    response = request_with_retry(
        "POST", TOKEN_URL, service="Google OAuth",
        data={
            "code": code, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier,
        },
    )
    raise_for_status(response, "Google OAuth", "the authorization code")
    return response.json()


def fetch_account_email(access_token: str) -> str | None:
    response = request_with_retry("GET", USERINFO_URL, service="Google OAuth", headers={"Authorization": f"Bearer {access_token}"})
    return response.json().get("email") if response.is_success else None


def revoke_token(token: str) -> None:
    try:
        request_with_retry("POST", "https://oauth2.googleapis.com/revoke", service="Google OAuth", retries=0, data={"token": token})
    except ExternalServiceError:
        logger.warning("google_revoke_failed")


class GoogleClient:
    def __init__(self, db: Session, account: OAuthAccount):
        self.db, self.account = db, account

    def _token(self) -> str:
        account = self.account
        if account.status != "connected":
            raise IntegrationNotConnected("Google access has expired. Reconnect it on the Integrations page.")
        if account.expires_at and account.expires_at > utcnow() + timedelta(seconds=60):
            return decrypt_secret(account.access_token_enc)
        refresh = decrypt_secret(account.refresh_token_enc)
        if not refresh:
            raise IntegrationNotConnected("Google access has expired. Reconnect it on the Integrations page.")
        response = request_with_retry(
            "POST", TOKEN_URL, service="Google OAuth", idempotent=True,
            data={"client_id": settings.google_client_id, "client_secret": settings.google_client_secret, "refresh_token": refresh, "grant_type": "refresh_token"},
        )
        if response.status_code in (400, 401):
            account.status = "needs_reauth"
            self.db.commit()
            raise IntegrationNotConnected("Google revoked this connection. Reconnect it on the Integrations page.")
        raise_for_status(response, "Google OAuth", "the token refresh")
        data = response.json()
        account.access_token_enc = encrypt_secret(data["access_token"])
        account.expires_at = utcnow() + timedelta(seconds=int(data.get("expires_in", 3600)))
        self.db.commit()
        return data["access_token"]

    def request(self, method: str, url: str, *, service: str, idempotent: bool | None = None, **kwargs):
        headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {self._token()}"}
        response = request_with_retry(method, url, service=service, idempotent=idempotent, headers=headers, **kwargs)
        if response.status_code == 401:
            self.account.status = "needs_reauth"
            self.db.commit()
            raise IntegrationNotConnected("Google rejected the stored credentials. Reconnect on the Integrations page.")
        return response


def _decode_part(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _extract_body(payload: dict) -> str:
    html = None
    stack = [payload]
    while stack:
        part = stack.pop(0)
        stack.extend(part.get("parts", []) or [])
        data = (part.get("body") or {}).get("data")
        if not data:
            continue
        if part.get("mimeType") == "text/plain":
            return _decode_part(data)
        if part.get("mimeType") == "text/html" and html is None:
            html = _decode_part(data)
    if html:
        without_blocks = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", without_blocks)).strip()
    return ""


class GmailProvider:
    name = "gmail"
    simulated = False

    def __init__(self, client: GoogleClient):
        self.client = client

    def fetch_page(self, page_token: str | None, max_results: int) -> EmailPage:
        params: dict = {"maxResults": max_results, "labelIds": "INBOX"}
        if page_token:
            params["pageToken"] = page_token
        response = self.client.request("GET", f"{GMAIL}/messages", service="Gmail", params=params)
        raise_for_status(response, "Gmail", "the message list")
        data = response.json()
        emails = [self._get(m["id"]) for m in data.get("messages", [])]
        return EmailPage(emails, data.get("nextPageToken"))

    def _get(self, message_id: str) -> RemoteEmail:
        response = self.client.request("GET", f"{GMAIL}/messages/{message_id}", service="Gmail", params={"format": "full"})
        raise_for_status(response, "Gmail", "the message fetch")
        msg = response.json()
        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
        name, address = parseaddr(headers.get("from", ""))
        try:
            received = parsedate_to_datetime(headers["date"])
            if received.tzinfo is None:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            received = datetime.fromtimestamp(int(msg.get("internalDate", 0)) / 1000, tz=utcnow().tzinfo)
        return RemoteEmail(
            msg["id"], msg.get("threadId"), name or None, address or "unknown@unknown", [parseaddr(headers.get("to", ""))[1]],
            headers.get("subject", "(no subject)"), truncate(msg.get("snippet", ""), 400),
            truncate(_extract_body(msg["payload"]), 60_000), received, msg.get("labelIds", []), headers.get("message-id"),
        )

    def send(self, to, cc, subject, body, thread_id, in_reply_to) -> ExternalResult:
        message = EmailMessage()
        message["To"] = ", ".join(to)
        if cc:
            message["Cc"] = ", ".join(cc)
        message["Subject"] = subject
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        payload = {"raw": raw, **({"threadId": thread_id} if thread_id else {})}
        response = self.client.request("POST", f"{GMAIL}/messages/send", service="Gmail", json=payload)
        raise_for_status(response, "Gmail", "the message")
        data = response.json()
        return ExternalResult(data["id"], False, "gmail", {"thread_id": data.get("threadId"), "label_ids": data.get("labelIds", [])})


def _google_event_body(payload: dict) -> dict:
    return {
        "summary": payload["title"],
        "description": payload.get("description") or "",
        **({"location": payload["location"]} if payload.get("location") else {}),
        "start": {"dateTime": payload["start_at"], "timeZone": payload["timezone"]},
        "end": {"dateTime": payload["end_at"], "timeZone": payload["timezone"]},
        "attendees": [{"email": a} for a in payload.get("attendees", [])],
    }


class GoogleCalendarProvider:
    name = "google_calendar"
    simulated = False

    def __init__(self, client: GoogleClient):
        self.client = client

    def list_events(self, start: datetime, end: datetime) -> list[RemoteEvent]:
        results: list[RemoteEvent] = []
        token = None
        for _ in range(5):  # bounded pagination
            params = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true", "orderBy": "startTime", "maxResults": 250}
            if token:
                params["pageToken"] = token
            response = self.client.request("GET", f"{CALENDAR}/events", service="Google Calendar", params=params)
            raise_for_status(response, "Google Calendar", "the event list")
            data = response.json()
            for item in data.get("items", []):
                if event := self._parse(item):
                    results.append(event)
            token = data.get("nextPageToken")
            if not token:
                break
        return results

    @staticmethod
    def _parse(item: dict) -> RemoteEvent | None:
        try:
            start, end = item["start"], item["end"]
            start_at = datetime.fromisoformat(start.get("dateTime") or f"{start['date']}T00:00:00+00:00")
            end_at = datetime.fromisoformat(end.get("dateTime") or f"{end['date']}T00:00:00+00:00")
        except (KeyError, ValueError):
            return None
        return RemoteEvent(
            item["id"], item.get("summary", "(no title)"), item.get("description", ""), item.get("location"),
            start_at, end_at, start.get("timeZone", "UTC"), [a["email"] for a in item.get("attendees", []) if "email" in a],
            "cancelled" if item.get("status") == "cancelled" else "confirmed",
        )

    def create_event(self, payload: dict, idempotency_key: str) -> ExternalResult:
        # A client-chosen event id makes re-submission safe: Google answers 409 instead of duplicating.
        event_id = "ap" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:40]
        body = {**_google_event_body(payload), "id": event_id}
        response = self.client.request("POST", f"{CALENDAR}/events", service="Google Calendar", idempotent=True, json=body, params={"sendUpdates": "all"})
        if response.status_code == 409:
            existing = self.client.request("GET", f"{CALENDAR}/events/{event_id}", service="Google Calendar")
            raise_for_status(existing, "Google Calendar", "the existing event lookup")
            return ExternalResult(event_id, False, "google_calendar", {"html_link": existing.json().get("htmlLink"), "already_existed": True})
        raise_for_status(response, "Google Calendar", "the event")
        data = response.json()
        return ExternalResult(data["id"], False, "google_calendar", {"html_link": data.get("htmlLink"), "status": data.get("status")})

    def update_event(self, external_id: str, payload: dict) -> ExternalResult:
        response = self.client.request("PATCH", f"{CALENDAR}/events/{external_id}", service="Google Calendar", idempotent=True, json=_google_event_body(payload), params={"sendUpdates": "all"})
        raise_for_status(response, "Google Calendar", "the event update")
        data = response.json()
        return ExternalResult(data["id"], False, "google_calendar", {"html_link": data.get("htmlLink"), "updated": data.get("updated")})

    def delete_event(self, external_id: str) -> ExternalResult:
        response = self.client.request("DELETE", f"{CALENDAR}/events/{external_id}", service="Google Calendar", params={"sendUpdates": "all"})
        if response.status_code not in (204, 410):
            raise_for_status(response, "Google Calendar", "the event deletion")
        return ExternalResult(external_id, False, "google_calendar", {"deleted": True})


class GoogleDriveProvider:
    name = "google_drive"
    simulated = False
    EXPORTS = {
        "application/vnd.google-apps.document": ("text/plain", ".txt"),
        "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    }
    SUPPORTED = ("application/pdf", "text/plain", "text/csv", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", *EXPORTS)

    def __init__(self, client: GoogleClient):
        self.client = client

    def list_files(self, query: str | None, page_size: int) -> list[RemoteFile]:
        clauses = ["trashed=false", "(" + " or ".join(f"mimeType='{m}'" for m in self.SUPPORTED) + ")"]
        if query:
            escaped = query.replace("\\", "\\\\").replace("'", "\\'")
            clauses.append(f"name contains '{escaped}'")
        response = self.client.request(
            "GET", DRIVE, service="Google Drive",
            params={"q": " and ".join(clauses), "pageSize": page_size, "fields": "files(id,name,mimeType,modifiedTime)", "orderBy": "modifiedTime desc"},
        )
        raise_for_status(response, "Google Drive", "the file list")
        return [RemoteFile(f["id"], f["name"], f["mimeType"], f.get("modifiedTime")) for f in response.json().get("files", [])]

    def download(self, file_id: str) -> tuple[bytes, str, str]:
        meta = self.client.request("GET", f"{DRIVE}/{file_id}", service="Google Drive", params={"fields": "name,mimeType,size"})
        raise_for_status(meta, "Google Drive", "the file lookup")
        info = meta.json()
        name, mime = info["name"], info["mimeType"]
        if int(info.get("size") or 0) > settings.max_upload_mb * 1024 * 1024:
            raise ExternalServiceError(f"{name} exceeds the {settings.max_upload_mb} MB limit.")
        if mime in self.EXPORTS:
            export_mime, ext = self.EXPORTS[mime]
            response = self.client.request("GET", f"{DRIVE}/{file_id}/export", service="Google Drive", params={"mimeType": export_mime})
            mime, name = export_mime, name + ext
        else:
            response = self.client.request("GET", f"{DRIVE}/{file_id}", service="Google Drive", params={"alt": "media"})
        raise_for_status(response, "Google Drive", "the file download")
        return response.content, name, mime

    def share(self, file_id: str, email: str, role: str) -> ExternalResult:
        response = self.client.request(
            "POST", f"{DRIVE}/{file_id}/permissions", service="Google Drive", json={"type": "user", "role": role, "emailAddress": email}
        )
        raise_for_status(response, "Google Drive", "the share request")
        return ExternalResult(response.json().get("id"), False, "google_drive", {"file_id": file_id, "shared_with": email, "role": role})

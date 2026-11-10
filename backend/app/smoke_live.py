"""Read-only proof that live credentials work. Sends, creates and deletes nothing.

    docker compose exec backend python -m app.smoke_live --user you@example.com

Exit codes: 0 all checks passed, 1 a check failed, 2 something was not configured.
Only counts are printed, never message content.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select

from app.agents.llm.base import structured_call
from app.agents.llm.factory import get_llm
from app.core.errors import AppError
from app.core.security import decrypt_secret
from app.database.base import utcnow
from app.database.session import SessionLocal
from app.integrations import github
from app.integrations.google import GmailProvider, GoogleCalendarProvider, GoogleClient, GoogleDriveProvider
from app.models import OAuthAccount, User

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
Check = tuple[str, str, str]


def _attempt(name: str, fn: Callable[[], str]) -> Check:
    try:
        return name, PASS, fn()
    except AppError as exc:
        return name, FAIL, exc.message
    except Exception as exc:  # noqa: BLE001 - a diagnostic must report every failure and keep going
        return name, FAIL, exc.__class__.__name__


def check_ai() -> list[Check]:
    try:
        llm = get_llm()
    except Exception as exc:  # noqa: BLE001
        return [("AI provider", FAIL, exc.__class__.__name__)]
    if llm.simulated:
        return [("AI provider", SKIP, "LLM_PROVIDER is mock; a real model is not configured")]

    def structured() -> str:
        schema = {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"]}
        result = structured_call(llm, purpose="smoke.structured", system="Answer using the tool.", user="What is 2 + 2?", schema=schema, max_tokens=2000)
        if result.get("answer") != 4:
            raise ValueError("Unexpected answer")
        return f"{llm.name}/{llm.model}: tool call and structured output work"

    return [_attempt("AI structured tool call", structured)]


def check_integrations(user_email: str | None) -> list[Check]:
    if not user_email:
        return [("Integrations", SKIP, "pass --user to check a connected account")]
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == user_email.lower()))
        if user is None:
            return [("Integrations", FAIL, "no such user")]
        accounts = {a.provider: a for a in db.scalars(select(OAuthAccount).where(OAuthAccount.user_id == user.id)).all()}
        results: list[Check] = []
        if "google" not in accounts:
            results.append(("Google", SKIP, "not connected for this user"))
        else:
            client = GoogleClient(db, accounts["google"])
            now = utcnow()
            results += [
                _attempt("Gmail read", lambda: f"{len(GmailProvider(client).fetch_page(None, 1).emails)} message(s) fetched"),
                _attempt("Calendar read", lambda: f"{len(GoogleCalendarProvider(client).list_events(now, now + timedelta(days=7)))} event(s) in 7 days"),
                _attempt("Drive read", lambda: f"{len(GoogleDriveProvider(client).list_files(None, 1))} file(s) listed"),
            ]
        if "github" not in accounts:
            results.append(("GitHub", SKIP, "not connected for this user"))
        else:
            results.append(_attempt("GitHub read", lambda: f"signed in as {github.get_login(decrypt_secret(accounts['github'].access_token_enc))}"))
        return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", help="Email of the AutoPilot user whose connected accounts to check")
    args = parser.parse_args(argv)
    results = [*check_ai(), *check_integrations(args.user)]
    for name, status, detail in results:
        print(f"[{status}] {name}: {detail}")
    statuses = {status for _, status, _ in results}
    return 1 if FAIL in statuses else 2 if SKIP in statuses else 0


if __name__ == "__main__":
    sys.exit(main())

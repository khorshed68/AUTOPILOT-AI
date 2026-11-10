"""Optional, read-only GitHub integration using a personal access token stored encrypted."""
from __future__ import annotations

from app.integrations.http import raise_for_status, request_with_retry

API = "https://api.github.com"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def get_login(token: str) -> str:
    response = request_with_retry("GET", f"{API}/user", service="GitHub", headers=_headers(token))
    raise_for_status(response, "GitHub", "the access token")
    return response.json()["login"]


def list_assigned_issues(token: str, limit: int = 30) -> list[dict]:
    response = request_with_retry("GET", f"{API}/issues", service="GitHub", headers=_headers(token), params={"filter": "assigned", "state": "open", "per_page": limit})
    raise_for_status(response, "GitHub", "the issue list")
    return [
        {"id": i["id"], "title": i["title"], "url": i["html_url"], "repository": i.get("repository", {}).get("full_name"), "updated_at": i.get("updated_at"), "is_pull_request": "pull_request" in i}
        for i in response.json()
    ]

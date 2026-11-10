# Development status

Last updated 2026-11-09, after the completion and security pass.

## Where it stands

**Release state: demo release.** No real account or AI provider has been connected, and none is needed to run it. Everything that can be built and proven without external credentials is done. **Two acceptance criteria stay open until real credentials are supplied:**

| Criterion | Status | What closes it |
|---|---|---|
| 3. AI understands natural-language instructions | Works through the demo AI's keyword rules; never run on a real model | An AI provider key, then the check below |
| 16. External integrations work when configured | Implemented and tested against mocked HTTP only | A Google OAuth client, then the check below |

All 18 other criteria are met and verified in demo mode.

## Closing the two open criteria

1. Add the credentials to `.env` (see README), then run `docker compose up -d`.
2. Connect Google on the Integrations page.
3. Run the read-only check. It sends, creates and deletes nothing, and prints only counts:

```bash
docker compose exec backend python -m app.smoke_live --user you@example.com
```

It exits 0 only if every check passed, and reports SKIP rather than success for anything not configured.

4. Turn on Live integrations and approve one real action of each kind on a test account: send an email to yourself, then create and delete a calendar event.

Expect some adjustment here: agent prompts have never been tuned on a real model, and the Google request shapes come from the published APIs.

## Verification

| What | Result |
|---|---|
| Backend tests, SQLite | 189 passed |
| Backend tests, PostgreSQL 17 | 189 passed |
| Frontend tests, type-check, production build | 20 passed; clean |
| Lint (ruff) | Clean |
| Dependency audit | `pip-audit`: none. `npm audit`: 0 vulnerabilities |
| Migrations | Two applied to PostgreSQL; `alembic check` reports no drift |
| Docker stack | All services running; migrations apply on start |
| Clean install | A separate copy on empty volumes reached the latest migration; demo sign-in, the worker, a 6-step workflow and chat all worked |
| Browser | Sign-in, dashboard, 4-step report scenario, approving a card, and all routes |
| Worker | Simulated email -> Redis -> worker -> 6-step workflow; beat ticks every minute |
| Visual review | Overview, chat, inbox, calendar, automations, tasks and analytics in light or dark; overview and chat at phone width. No horizontal overflow on any page at 390px |

## Security audit

Reviewed authentication, OAuth, authorization, prompt injection, tool permissions, data isolation and approval enforcement.

| Finding | Severity | Resolution |
|---|---|---|
| A card shown as simulated would really execute if the user switched to live before approving | High | Approval now refuses and expires if the mode changed |
| Remote images in AI-written markdown could leak data through their URL | High | Images are never rendered; CSP also blocks them |
| Security headers missing on HTML pages | High | Sent on every page |
| Editing an approval could retarget it to a different record | Medium | Linked record ids are immutable |
| Conversation history and some tool output reached agents unmarked | Medium | Now delimited as untrusted, and it taints the run |
| Memory could be written from text quoted out of earlier turns | Medium | A memory must come from the user's current message |
| Crafted DOCX or PDF files could exhaust memory or crash an upload | Medium | Size and page limits; parser errors are contained |
| Line breaks in a subject could inject mail headers | Medium | Rejected at validation |
| Auth rate limits shared by all users behind the proxy | Medium | Keyed on the real client IP, which clients can't spoof |
| API docs public in production | Low | Disabled in production |
| React Router and test tooling advisories | Mixed | Upgraded |

## Known limitations

These are accepted and do not affect the criteria above.

- The demo AI handles the brief's examples and close variations only.
- Sharing a Drive file uses `drive.file`, which covers files the app created or the user opened with it. Sharing a file imported read-only may be refused by Google, and is reported as a failure. Full Drive access was deliberately not requested.
- Archiving is local to AutoPilot, because Gmail access is read and send only.
- A chat run interrupted by a backend restart is marked failed and retryable within about 15 minutes.
- The run time limit is checked between model and tool calls; an in-flight call is bounded by its own timeout.
- Document search is BM25 with no embeddings.
- There are no admin features; authorization is per-owner (see docs/IMPLEMENTATION_PLAN.md).
- Production deployment needs your domain, HTTPS and secrets. It has only been run locally.

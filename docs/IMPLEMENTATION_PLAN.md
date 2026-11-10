# Implementation plan

How the brief was broken down, and where each part lives.

| Phase | Scope | Main code | Verified by |
|---|---|---|---|
| 1. Foundation | Project layout, FastAPI, React, PostgreSQL, Docker, auth, dashboard | `backend/app/core`, `app/api/auth.py`, `frontend/src/layouts`, `docker-compose.yml` | `tests/test_auth.py` |
| 2. Core productivity | Tasks, calendar, documents, activity, notifications | `app/services`, `frontend/src/pages` | `tests/test_features.py` |
| 3. AI employee | Provider layer, Manager and specialists, tool calling, chat | `app/agents` | `tests/test_agents.py`, `tests/test_smoke_chat.py` |
| 4. Integrations | Google OAuth with PKCE, Gmail, Calendar, Drive, GitHub | `app/integrations`, `app/smoke_live.py` | `tests/test_integrations.py` with mocked HTTP |
| 5. Automation | Triggers, conditions, actions, Celery workers, builder, history | `app/workflows`, `app/workers` | `tests/test_workflows.py` |
| 6. Control | Approval system, memory | `app/services/approvals.py`, `app/services/memory.py` | `tests/test_approvals.py`, `tests/test_isolation.py` |
| 7. Quality | Security review, error handling, demo mode, deployment, docs | across the project | `tests/test_audit.py`, frontend tests, browser checks |

Order of work followed the phases: the data model and approval rules came before the agents, so no agent could be written with a path around them.

## Decisions

See "Design decisions" in [ARCHITECTURE.md](ARCHITECTURE.md). Two that shaped the scope:

- **Authorization is per-owner.** This is a personal product, so every record belongs to one user and access is checked on each query. The `role` column exists, but there are no admin features to protect.
- **Least-privilege Google scopes.** Gmail is read and send only, Drive is read-only plus `drive.file`. Broader access would be needed for archiving in Gmail or sharing arbitrary Drive files, and was deliberately not requested.

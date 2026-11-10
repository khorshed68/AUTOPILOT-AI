# AutoPilot AI

**Your Work. Your AI Employee. Fully in Control.**

AutoPilot AI is a personal AI employee. You describe an outcome in plain language. A Manager agent plans the work, specialist agents carry it out through a validated set of tools, and anything sensitive waits for your approval of the exact action.

It runs fully without external credentials in **demo mode**: sample data and a rule-based AI, with every simulated item labelled.

> **Current release: demo.** Gmail, Calendar, Drive, GitHub and real AI models are implemented and tested against mocks, but have not yet been run against the real services. See [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md).

## Quick start

Requires Docker and Python 3 (only to generate secrets).

```bash
python scripts/generate_env.py
```

```bash
docker compose up -d --build
```

Open <http://localhost:5190> and sign in with the local demo account:

| Email | Password |
|---|---|
| `demo@example.com` | `DemoPass123!` |

Or register a new account, which starts with its own demo data. The demo account is only created when `DEMO_USER_PASSWORD` is set and `ENVIRONMENT` is not `production`.

API docs are at <http://localhost:5190/api/docs>.

### Try these

In **AI Employee**:

- `Someone emailed me asking for a project report by Thursday. Find the relevant documents, prepare a report, create a task, and remind me one day before the deadline.`
- `Schedule a meeting with the development team next Monday afternoon for one hour.` (then approve, reject or edit the card)
- `Summarize my important emails.` (one sample email contains a prompt-injection attempt, which is flagged and ignored)

In **Inbox**, draft a reply to "Can you confirm the on-call rota?" and approve it to see how a failed delivery is reported. In demo mode, **Simulate incoming email** triggers the *Important Email Assistant* workflow through the background worker.

## Using real services

### AI provider

Set these in `.env`, then run `docker compose up -d`:

```ini
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
# LLM_MODEL defaults to claude-opus-5-5
```

For any OpenAI-compatible endpoint, use `LLM_PROVIDER=openai` with `OPENAI_API_KEY`, `LLM_MODEL`, and `OPENAI_BASE_URL`.

### Google (Gmail, Calendar, Drive)

1. In Google Cloud Console, create a project and enable the Gmail, Google Calendar and Google Drive APIs.
2. Configure the OAuth consent screen and add yourself as a test user.
3. Create an OAuth client of type **Web application** with this redirect URI: `http://localhost:5190/api/integrations/google/callback`
4. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env`, then run `docker compose up -d`.
5. Go to **Integrations**, choose **Connect Google**, then turn on **Live integrations**.

### GitHub (optional)

On **Integrations**, paste a personal access token. Access is read-only: it lists issues assigned to you.

### Verify your credentials

This read-only check confirms the AI provider and connected accounts work. It sends, creates and deletes nothing:

```bash
docker compose exec backend python -m app.smoke_live --user you@example.com
```

## Development without Docker

Start the infrastructure, then the backend and frontend:

```bash
docker compose up -d postgres redis
```

```bash
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
```

Set `DATABASE_URL` to `postgresql+psycopg://autopilot:<POSTGRES_PASSWORD>@localhost:55440/autopilot`, then:

```bash
cd backend && .venv/Scripts/alembic upgrade head && .venv/Scripts/uvicorn app.main:app --port 8100 --reload
```

```bash
cd frontend && npm install && npm run dev
```

The dev frontend is on <http://localhost:5191>.

## Tests

```bash
cd backend && .venv/Scripts/python -m pytest
```

```bash
cd frontend && npm test
```

Backend tests use SQLite by default. To run them on PostgreSQL, set `TEST_DATABASE_URL`. External services are mocked in automated tests.

## Project layout

```
backend/app/
  agents/        orchestrator, agent registry, tool registry, safety, LLM providers
  api/           FastAPI routers
  core/          config, security, errors, logging, rate limits
  database/      engine, base types
  integrations/  provider contracts; demo and Google/GitHub implementations
  models/        SQLAlchemy models
  schemas/       request and response models
  services/      approvals, email, calendar, documents, memory, analytics, seed
  workflows/     automation catalog and engine
  workers/       Celery app and jobs
backend/alembic/ migrations
backend/tests/
frontend/src/    components, contexts, layouts, pages, services, types, utils
docs/            architecture
scripts/         environment generator
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how agents, approvals and injection defences work, and [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md) for what has and hasn't been verified.

## Production notes

Set `ENVIRONMENT=production`, `COOKIE_SECURE=true`, strong `SECRET_KEY` and `ENCRYPTION_KEY` (the app refuses to start otherwise), and serve over HTTPS. Set `FRONTEND_URL`, `CORS_ORIGINS` and `GOOGLE_REDIRECT_URI` to your domain. Consider `DEMO_MODE_ENABLED=false`.

Changing `ENCRYPTION_KEY` makes stored integration tokens unreadable, and users will need to reconnect.

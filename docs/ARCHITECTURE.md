# Architecture

## Request flow

```
Browser (React)  ->  nginx  ->  FastAPI  ->  Orchestrator  ->  Specialist agents  ->  Tool registry  ->  Services  ->  PostgreSQL
                                   |                                                       |
                                   |                                                       +-> Providers (demo or Google)
                                   +-> Celery (Redis)  ->  Workflow engine
```

## Agents

`app/agents/orchestrator.py` is the only dispatcher. Agents cannot call each other, so agent-to-agent loops are impossible by construction.

| Stage | What happens | Enforced by |
|---|---|---|
| Plan | The Manager returns an `ExecutionPlan`: intent, objective, steps with dependencies | Pydantic schema, known agents only, DAG check, step limit; one repair attempt |
| Execute | Steps run in topological order. A step whose dependency did not complete is skipped | `AGENT_MAX_PLAN_STEPS`, `AGENT_MAX_ITERATIONS`, `AGENT_MAX_TOOL_CALLS_PER_RUN`, `AGENT_STEP_RETRIES`, `AGENT_RUN_TIMEOUT_SECONDS` |
| Tool call | Allowlist check, then argument validation, then a handler scoped to `user_id` | `execute_tool` in `app/agents/tools.py` |
| Report | The Manager writes the message from structured results only. The UI shows the ledger of confirmed actions separately | Ledger entries are created by tools, not by the model |

Each agent has its own instructions and tool allowlist in `app/agents/registry.py`. Every run is stored as `agent_runs` plus one `agent_executions` row per step and attempt (agent, task id, input, result, status, errors, duration, tool log).

## LLM providers

All providers implement `LLMProvider` in `app/agents/llm/base.py`: `complete` and `stream` over provider-neutral messages and tool specs. Select one with `LLM_PROVIDER`:

- `mock`: deterministic keyword rules. It emits real plans and tool calls, so the same validators, allowlists and approvals run. It cannot invent facts because its text is extracted from tool results.
- `anthropic`: official SDK. Defaults to `claude-opus-5-5`, with server-side refusal fallback enabled.
- `openai`: any OpenAI-compatible Chat Completions endpoint.

Structured output is requested as a `submit_result` tool call, which behaves the same across providers.

## Approval system

Sensitive actions (`send_email`, `create/update/delete_calendar_event`, `share_document`, and `create_task` when it originates from an automation) are created by `services/approvals.py::propose`.

- `payload_hash` is SHA-256 over action type, canonical payload and version. `approve` requires the hash the client displayed.
- An edit increments `version`, so even reverting to the original text produces a new hash and needs a new approval.
- `pending -> executing` is a single conditional `UPDATE`, so concurrent approvals execute once.
- The status is set to `executed` only from the provider's response. A rejection becomes `failed`. A timeout on a non-idempotent call becomes `outcome_unknown` and is never retried automatically.
- A crash mid-execution is swept to `outcome_unknown` after 10 minutes.
- An approval is refused and expired if the user switched between demo and live after it was proposed, so a card labelled simulated can never reach a real service.
- Linked record ids in a payload cannot be changed by an edit.

## Prompt-injection defences

Detection is advisory. These are the hard guarantees:

1. Tool allowlists are enforced by the backend, per agent.
2. Reply recipients are derived on the server from the original email. No tool lets a model choose a recipient for an email.
3. No tool can send, delete or share. They can only create proposals for a human.
4. Untrusted content is wrapped in delimiters carrying a per-run random nonce, so it cannot forge the end of the block.
5. Taint tracking: once a run reads email or documents, its proposals are flagged `derived_from_untrusted` and the approval card shows a warning.
6. AI-written markdown never renders images or raw HTML, which closes the common data-leak channel through image URLs.
7. Memory is only written from the Manager's plan, and only when its content comes from the user's current message. Secret-shaped values are refused.

## Workflow engine

`app/workflows/engine.py`. Triggers: manual, daily, weekly, new email. `enabled` only matters because Celery beat's `tick` task evaluates `next_run_at` and unchecked emails. Executions have a `dedupe_key` unique per automation, so a schedule slot or email cannot run twice, and `run_execution` claims a job with a conditional `UPDATE`, so a redelivered job is ignored. Automations can only propose: their `create_task` goes through approval.

## Data separation

`user_preferences.integration_mode` selects providers per user. `is_demo` marks simulated rows. Email and calendar queries are strictly separated by mode. Anything simulated is labelled in the UI.

## Design decisions

- Sync SQLAlchemy and FastAPI's thread pool: one code path for API and Celery.
- Chat streams over SSE from a background thread, so a dropped connection cannot leave a half-recorded run.
- Search uses BM25 in Python over the user's rows: simple, portable, and adequate at personal scale.
- Sessions are JWT cookies backed by an `auth_sessions` row, which makes logout real revocation.
- Tests run on SQLite for speed and can run on PostgreSQL with `TEST_DATABASE_URL`.

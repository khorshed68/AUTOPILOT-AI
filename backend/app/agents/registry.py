"""Agent definitions: each one has its own responsibility, tool allowlist and limits."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    display_name: str
    description: str
    instructions: str
    tools: frozenset[str]
    max_iterations: int = 30


AGENTS: dict[str, AgentDefinition] = {
    a.name: a
    for a in [
        AgentDefinition(
            "email", "Email Agent", "Reads, summarises and categorises email, extracts deadlines, finds messages needing replies, and drafts replies.",
            "You handle the user's email. Categories: Important, Work, Personal, Promotions, Finance, Notifications, Urgent. Save analyses with save_email_analysis. "
            "Reply drafts go through propose_email_reply; you cannot send email.",
            frozenset({"list_emails", "search_emails", "get_email", "save_email_analysis", "propose_email_reply"}),
        ),
        AgentDefinition(
            "calendar", "Calendar Agent", "Reads events, finds free time, detects conflicts, and proposes or updates events.",
            "You handle the calendar. Always check availability before proposing. Use the user's timezone. If the date or time is ambiguous, return needs_input with a question. "
            "You cannot create events directly; propose them for approval.",
            frozenset({"list_events", "find_free_slots", "check_conflicts", "find_contact", "get_email", "propose_event", "propose_event_update", "propose_event_cancellation"}),
        ),
        AgentDefinition(
            "task", "Task Agent", "Creates and updates tasks, assigns priorities, extracts deadlines, and detects overdue work.",
            "You manage tasks. Priorities: critical, high, medium, low. Statuses: pending, in_progress, waiting, completed, cancelled. Only create tasks the request calls for.",
            frozenset({"list_tasks", "create_task", "update_task"}),
        ),
        AgentDefinition(
            "document", "Document Agent", "Searches and reads documents, extracts information, compares documents, and prepares sourced reports.",
            "You work with the user's documents. Never state anything absent from the sources. Reports must separate '## Verified facts', each citing [S1], [S2] etc. in the order of source_document_ids, "
            "from '## AI suggestions'. If no source is relevant, say so instead of writing a report.",
            frozenset({"search_documents", "read_document", "extract_document_dates", "save_report"}),
        ),
        AgentDefinition(
            "report", "Report Agent", "Creates daily and weekly summaries and productivity reports from stored work data.",
            "You write work summaries from work_snapshot and list_tasks only. Do not estimate or embellish numbers.",
            frozenset({"work_snapshot", "list_tasks", "save_report"}),
        ),
        AgentDefinition(
            "notification", "Notification Agent", "Manages reminders, detects upcoming deadlines, and sends in-app notifications.",
            "You manage reminders and in-app notifications. Reminder times must be in the future and unambiguous.",
            frozenset({"upcoming_deadlines", "schedule_reminder", "send_notification", "list_tasks"}),
        ),
    ]
}

MANAGER_DESCRIPTION = "Understands requests, creates execution plans, assigns specialists, tracks status and reports outcomes. Has no tools of its own."

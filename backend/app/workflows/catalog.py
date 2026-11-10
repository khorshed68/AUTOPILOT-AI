"""What the workflow builder can offer. Both the API validation and the UI read from this."""
from __future__ import annotations

TRIGGERS = [
    {"type": "manual", "label": "Manual", "description": "Runs only when you start it."},
    {"type": "daily", "label": "Every day", "description": "Runs daily at a set time in your timezone.", "config": ["time"]},
    {"type": "weekly", "label": "Every week", "description": "Runs weekly on a set day and time.", "config": ["weekday", "time"]},
    {"type": "new_email", "label": "New email received", "description": "Runs for each new email that matches the conditions."},
]

CONDITION_FIELDS = [
    {"field": "email.subject", "label": "Subject", "kind": "text"},
    {"field": "email.body", "label": "Body", "kind": "text"},
    {"field": "email.sender", "label": "Sender", "kind": "text"},
    {"field": "email.has_deadline", "label": "Contains a deadline", "kind": "boolean"},
    {"field": "email.is_urgent", "label": "Is an urgent request", "kind": "boolean"},
]
TEXT_OPERATORS = ("contains", "not_contains", "equals")
BOOL_OPERATORS = ("is_true", "is_false")

EMAIL = "email"
ACTIONS = [
    {"type": "analyze_email", "label": "Analyze email", "requires": EMAIL, "agent": "email", "description": "Categorize the email and extract key details."},
    {"type": "generate_summary", "label": "Generate summary", "requires": EMAIL, "agent": "email", "description": "Add the email's summary to the run results."},
    {"type": "extract_deadline", "label": "Extract deadline", "requires": EMAIL, "agent": "email", "description": "Find the deadline stated in the email."},
    {"type": "propose_task", "label": "Propose task", "requires": EMAIL, "agent": "task", "description": "Create a task proposal for your approval."},
    {"type": "prepare_reply_draft", "label": "Prepare reply draft", "requires": EMAIL, "agent": "email", "description": "If a reply is needed, draft one for your approval."},
    {"type": "notify_user", "label": "Notify me", "requires": None, "agent": "notification", "description": "Send an in-app notification.", "config": ["title"]},
    {"type": "get_todays_tasks", "label": "Get today's tasks", "requires": None, "agent": "task", "description": "Collect open tasks and those due today."},
    {"type": "get_completed_activities", "label": "Get completed activities", "requires": None, "agent": "report", "description": "Collect what was completed today."},
    {"type": "analyze_pending", "label": "Analyze pending work", "requires": None, "agent": "report", "description": "Count overdue work, replies and approvals."},
    {"type": "generate_daily_summary", "label": "Generate daily summary", "requires": None, "agent": "report", "description": "Write a work summary and notify you."},
]
ACTION_TYPES = {a["type"]: a for a in ACTIONS}
CONDITION_FIELD_KIND = {c["field"]: c["kind"] for c in CONDITION_FIELDS}

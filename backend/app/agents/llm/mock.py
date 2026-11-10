"""Deterministic stand-in for an LLM, used in demo mode and tests.

It implements the same provider contract as a real model. Plans and tool calls it emits go through the
same schema validation, tool allowlists, approvals and audit trail. Its "understanding" is keyword rules,
and everything it writes is extracted from tool results, so it cannot invent facts.
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Generator, Iterator
from datetime import date, datetime, time, timedelta
from typing import Any

from app.agents.llm.base import SUBMIT_TOOL, LLMProvider, LLMRequest, LLMResponse, ToolCall
from app.utils.dates import extract_deadlines, parse_when
from app.utils.text import query_terms, top_sentences, truncate

Script = Generator[tuple[str, dict], dict, dict]
_INSTRUCTION_WORDS = frozenset("report document documents summary summarize compare file files needed".split())


def _terms(text: str) -> list[str]:
    """Search terms, minus words that describe the request rather than its subject."""
    return [t for t in query_terms(text) if t not in _INSTRUCTION_WORDS]


def _tag(text: str, tag: str) -> Any:
    match = re.search(rf"<{tag}\b[^>]*>\s*(.*?)\s*</{tag}\b[^>]*>", text, re.DOTALL)
    return json.loads(match[1]) if match else None


def _tool_payload(text: str) -> dict:
    try:
        tagged = _tag(text, "untrusted_data")
        return tagged if tagged is not None else json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


# Email heuristics --------------------------------------------------------------------------------------------------

def analyze_email_rules(email: dict, today: date) -> dict:
    text = f"{email.get('subject', '')}\n{email.get('body') or email.get('snippet', '')}"
    lower = text.lower()
    sender = (email.get("sender_email") or "").lower()
    deadlines = extract_deadlines(text, today)
    urgent = bool(re.search(r"\b(urgent|asap|immediately|critical)\b", lower))
    if re.search(r"\b(unsubscribe|% off|sale ends|newsletter|promo)\b", lower):
        category = "Promotions"
    elif re.search(r"\b(invoice|payment|receipt|bank|refund|expense)\b", lower):
        category = "Finance"
    elif re.search(r"(no-?reply|notifications?@|alerts?@)", sender):
        category = "Notifications"
    elif urgent:
        category = "Urgent"
    elif re.search(r"\b(project|report|meeting|client|deadline|sprint|review|proposal|contract)\b", lower):
        category = "Work"
    elif re.search(r"\b(mom|dad|birthday|dinner|family|weekend)\b", lower):
        category = "Personal"
    else:
        category = "Work" if deadlines else "Notifications"
    promotional = category in ("Promotions", "Notifications")
    asks = bool(re.search(r"\?|\bplease (send|share|confirm|review|let me know|reply|prepare|provide)\b|\bcould you\b|\bcan you\b", lower))
    needs_reply = asks and not promotional
    importance = "high" if (urgent or (deadlines and not promotional)) else ("low" if promotional else "normal")
    if importance == "high" and category == "Work" and not urgent:
        category = "Important"
    excerpt = top_sentences(email.get("body") or email.get("snippet", ""), email.get("subject", ""), 2)
    items = [s for s in top_sentences(email.get("body", ""), "please deadline send review confirm prepare", 4) if re.search(r"please|need|must|could you|can you", s, re.I)]
    action = "Reply to the sender" if needs_reply else ("No action needed" if promotional else "Read when convenient")
    if deadlines:
        action = f"Plan work before {deadlines[0]['date']}" + (" and reply" if needs_reply else "")
    return {
        "category": category, "importance": importance, "summary": " ".join(excerpt) or truncate(email.get("snippet", ""), 200),
        "needs_reply": needs_reply, "suggested_action": action, "action_items": items[:3],
        "deadline_date": deadlines[0]["date"] if deadlines else None, "deadline_text": deadlines[0]["text"] if deadlines else None,
    }


def draft_reply_rules(email: dict, tone: str, instructions: str | None) -> dict:
    first = (email.get("sender_name") or "there").split(" ")[0]
    deadlines = extract_deadlines(f"{email.get('subject', '')} {email.get('body', '')}", date.today())
    timing = f" I have noted the timing ({deadlines[0]['text']}) and will keep you updated." if deadlines else ""
    closing = "Thanks" if tone == "friendly" else "Best regards"
    extra = f"\n\n{instructions.strip()}" if instructions else ""
    body = f"Hi {first},\n\nThank you for your message about \"{email.get('subject', 'this')}\". I have received it and am looking into it.{timing}{extra}\n\n{closing}"
    return {"subject": _re(email.get("subject", "")), "body": body}


def _re(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


# Planning ----------------------------------------------------------------------------------------------------------

def _step(sid: str, agent: str, goal: str, action: str, depends: list[str] | None = None, **inputs: Any) -> dict:
    return {"id": sid, "agent": agent, "goal": goal, "inputs": {"action": action, **inputs}, "depends_on": depends or []}


def _plan(text: str) -> dict:
    t = text.lower().strip()
    respond = lambda msg, **kw: {"intent": "respond", "objective": "Answer directly", "response": msg, "steps": [], "memories": [], **kw}  # noqa: E731
    run = lambda objective, *steps: {"intent": "execute", "objective": objective, "steps": list(steps), "memories": []}  # noqa: E731

    if m := re.match(r"^(?:please\s+)?remember(?: that)?\s+(.+)$", text.strip(), re.I | re.S):
        fact = m[1].strip().rstrip(".")
        kind = "tone" if re.search(r"\btone\b", fact, re.I) else "project" if re.search(r"\bproject\b", fact, re.I) else "preference"
        return respond("Noted. You can review or remove this under Settings → Memory.", memories=[{"kind": kind, "content": fact}])
    if re.search(r"^(hi|hello|hey|help|what can you do)\b", t):
        return respond(
            "I can triage your inbox, draft replies, manage tasks, check your calendar and propose meetings, search documents and "
            "build sourced reports, and summarise your work. Sensitive actions always wait for your approval."
        )

    mentions_report = "report" in t
    if mentions_report and re.search(r"\bemail|emailed\b", t) and re.search(r"\btask\b", t):
        reminder = re.search(r"\bremind\b", t)
        steps = [
            _step("s1", "email", "Identify the email requesting the deliverable and extract its deadline", "find_request", request=text),
            _step("s2", "document", "Find relevant documents and prepare a sourced report draft", "report", ["s1"], request=text),
            _step("s3", "task", "Create a task for the deliverable with the extracted deadline", "create_from_request", ["s1"]),
        ]
        if reminder:
            steps.append(_step("s4", "notification", "Schedule a reminder one day before the deadline", "remind_before", ["s1", "s3"], days_before=1))
        return run("Handle the emailed report request end to end", *steps)

    if re.search(r"\b(schedule|book|set up|arrange)\b.*\b(meeting|call|sync|event|appointment|discussion)\b", t) or re.search(r"\bmeeting with\b", t):
        return run("Propose a calendar event", _step("s1", "calendar", "Find a suitable time and propose the event", "schedule", request=text))
    if re.search(r"\b(calendar|upcoming (events|meetings)|what meetings|my schedule|free time|availability)\b", t):
        return run("Review the calendar", _step("s1", "calendar", "List upcoming events", "list", request=text))

    if re.search(r"\b(draft|write|prepare)\b.*\b(reply|response)\b|\breply to\b", t):
        return run("Draft an email reply", _step("s1", "email", "Draft a professional reply for approval", "draft_reply", request=text))
    if "email" in t or "inbox" in t or "mail" in t:
        if "deadline" in t:
            return run("Find a deadline in email", _step("s1", "email", "Find the deadline mentioned in the relevant email", "find_deadline", request=text))
        if re.search(r"\b(reply|replies|respond|response)\b", t):
            return run("Find emails awaiting a reply", _step("s1", "email", "Identify emails requiring a reply", "needs_reply"))
        if re.search(r"categor|classif|organi[sz]e", t):
            return run("Categorise the inbox", _step("s1", "email", "Categorise recent emails", "categorize"))
        return run("Summarise important email", _step("s1", "email", "Analyse and summarise important emails", "summarize_important"))

    if re.search(r"\b(daily|today'?s work|today'?s summary|summary of today|end of day|what did i do today)\b", t):
        return run("Summarise today's work", _step("s1", "report", "Create today's work summary", "daily"))
    if re.search(r"\b(weekly|this week|productivity report)\b", t) and re.search(r"\b(report|summary|review|productivity)\b", t):
        return run("Create a weekly report", _step("s1", "report", "Create the weekly report", "weekly"))

    if "compare" in t:
        return run("Compare documents", _step("s1", "document", "Compare the relevant documents", "compare", request=text))
    if mentions_report or re.search(r"\b(document|documents|file|files|pdf)\b", t):
        if re.search(r"\b(report|draft)\b", t):
            return run("Prepare a report from documents", _step("s1", "document", "Search documents and write a sourced report", "report", request=text))
        return run("Summarise documents", _step("s1", "document", "Find and summarise the relevant documents", "summarize", request=text))

    if "overdue" in t:
        return run("Find overdue tasks", _step("s1", "task", "List overdue tasks", "overdue"))
    if re.search(r"\b(mark|complete|finish(ed)?|done)\b", t) and re.search(r"\b(as done|as complete|completed?|done)\b", t) and "task" in t:
        return run("Update a task", _step("s1", "task", "Mark the matching task completed", "complete", request=text))
    if re.search(r"\b(create|add|new)\b.*\btask\b|\btask to\b", t):
        return run("Create a task", _step("s1", "task", "Create the requested task", "create", request=text))
    if re.search(r"\bremind\b", t):
        return run("Schedule a reminder", _step("s1", "notification", "Schedule the requested reminder", "remind", request=text))
    if re.search(r"\b(deadlines?|due soon)\b", t):
        return run("Check upcoming deadlines", _step("s1", "notification", "List upcoming deadlines", "deadlines"))
    if re.search(r"\btasks?\b|\bto-?do\b", t):
        return run("Review tasks", _step("s1", "task", "List open tasks", "list"))
    return {
        "intent": "clarify", "objective": "Clarify the request", "steps": [], "memories": [],
        "question": "I wasn't sure what you'd like done. Is this about your email, tasks, calendar, or documents?",
    }


# Agent scripts -----------------------------------------------------------------------------------------------------

def _today(task: dict) -> date:
    return datetime.fromisoformat(task["now_local"]).date()


def _ensure_analyzed(task: dict, limit: int = 12) -> Script:
    listing = yield ("list_emails", {"view": "inbox", "limit": limit})
    emails = listing["emails"]
    for item in emails:
        if item.get("analyzed"):
            continue
        full = yield ("get_email", {"email_id": item["id"]})
        analysis = analyze_email_rules(full, _today(task))
        yield ("save_email_analysis", {"email_id": item["id"], **analysis})
        item.update(analysis, analyzed=True)
    return emails


def _summarize_important(task, up) -> Script:
    emails = yield from _ensure_analyzed(task)
    important = [e for e in emails if e.get("importance") == "high" or e.get("category") in ("Important", "Urgent")]
    if not important:
        return {"status": "completed", "summary": "No important emails need your attention right now.", "data": {"emails": []}}
    lines = [f"- **{e['subject']}** from {e['sender']}: {truncate(e.get('summary') or '', 170)}" for e in important]
    return {"status": "completed", "summary": f"{len(important)} important email(s):\n" + "\n".join(lines), "data": {"email_ids": [e["id"] for e in important]}}


def _needs_reply(task, up) -> Script:
    emails = yield from _ensure_analyzed(task)
    pending = [e for e in emails if e.get("needs_reply")]
    if not pending:
        return {"status": "completed", "summary": "No recent emails are waiting on a reply.", "data": {"email_ids": []}}
    lines = [f"- **{e['subject']}** from {e['sender']}: {e.get('suggested_action') or 'Reply'}" for e in pending]
    return {"status": "completed", "summary": f"{len(pending)} email(s) need a reply:\n" + "\n".join(lines), "data": {"email_ids": [e["id"] for e in pending]}}


def _categorize(task, up) -> Script:
    emails = yield from _ensure_analyzed(task)
    counts: dict[str, int] = {}
    for e in emails:
        counts[e.get("category") or "Uncategorized"] = counts.get(e.get("category") or "Uncategorized", 0) + 1
    body = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    return {"status": "completed", "summary": f"Categorised {len(emails)} recent emails. {body}.", "data": {"counts": counts}}


def _search_query(request: str) -> str:
    return " ".join(_terms(re.sub(r"\b(deadline|mentioned|latest|email|emails|mail|reply|draft|write|someone|asking|by|thursday)\b", " ", request.lower()))) or "project"


def _find_deadline(task, up) -> Script:
    found = yield ("search_emails", {"query": _search_query(task["inputs"]["request"]), "limit": 5})
    for item in found["emails"]:
        full = yield ("get_email", {"email_id": item["id"]})
        deadlines = extract_deadlines(f"{full['subject']}\n{full['body']}", _today(task))
        if deadlines:
            d = deadlines[0]
            return {
                "status": "completed", "summary": f"**{full['subject']}** from {full['sender']} says \"{d['text']}\", which is {d['date']}.",
                "data": {"email_id": item["id"], "deadline": d["date"], "quoted": d["text"]},
            }
    return {"status": "needs_input", "summary": "I couldn't find a stated deadline in matching emails.", "question": "Which email or project should I look at?", "data": {}}


def _find_request(task, up) -> Script:
    found = yield ("search_emails", {"query": "report", "limit": 8})
    for item in found["emails"]:
        full = yield ("get_email", {"email_id": item["id"]})
        deadlines = extract_deadlines(f"{full['subject']}\n{full['body']}", _today(task))
        if deadlines and "report" in f"{full['subject']} {full['body']}".lower():
            d = deadlines[0]
            terms = " ".join(_terms(full["subject"])) or "project"
            return {
                "status": "completed", "summary": f"Found the request: **{full['subject']}** from {full['sender']}, due {d['date']} (\"{d['text']}\").",
                "data": {"email_id": item["id"], "subject": full["subject"], "sender": full["sender"], "deadline": d["date"], "topic": terms},
            }
    return {"status": "needs_input", "summary": "I could not find an email asking for a report with a deadline.", "question": "Who sent the request, or what is the project called?", "data": {}}


def _draft_reply(task, up) -> Script:
    request = task["inputs"]["request"]
    target = None
    query = re.sub(r"\b(draft|write|prepare|a|an|the|reply|response|to|email|from|please|professional)\b", " ", request.lower()).strip()
    if _terms(query):
        found = yield ("search_emails", {"query": query, "limit": 1})
        target = found["emails"][0] if found["emails"] else None
    if target is None:
        listing = yield ("list_emails", {"view": "needs_reply", "limit": 1})
        target = listing["emails"][0] if listing["emails"] else None
    if target is None:
        return {"status": "needs_input", "summary": "I couldn't tell which email to reply to.", "question": "Which email should I reply to?", "data": {}}
    full = yield ("get_email", {"email_id": target["id"]})
    draft = draft_reply_rules(full, task.get("tone", "professional"), None)
    proposal = yield ("propose_email_reply", {"email_id": target["id"], "subject": draft["subject"], "body": draft["body"]})
    return {"status": "completed", "summary": f"Drafted a reply to **{full['sender']}** about \"{full['subject']}\". It will not be sent unless you approve it.", "data": {"approval_id": proposal["approval_id"]}}


def _schedule(task, up) -> Script:
    request = task["inputs"]["request"]
    today = _today(task)
    when = parse_when(request, today)
    if when.date is None:
        return {"status": "needs_input", "summary": "I need a date before I can check your calendar.", "question": when.ambiguities[0], "data": {}}
    title_match = re.search(r"\b(?:meeting|call|sync|discussion|appointment)\s+(with\s+.+?)(?=\s+(?:next|this|on|tomorrow|today|at|for|in the)\b|[.?!]?$)", request, re.I)
    subject = title_match[1].strip() if title_match else None
    title = f"Meeting {subject}" if subject else "Meeting"
    duration = when.duration_minutes or 60
    attendees: list[str] = []
    notes = ""
    if subject:
        name = re.sub(r"^with\s+(the\s+)?", "", subject, flags=re.I)
        contact = yield ("find_contact", {"name": name})
        if len(contact["matches"]) == 1:
            attendees = [contact["matches"][0]["email"]]
        else:
            notes = f" I didn't add \"{name}\" as an attendee because I couldn't match one email address; you can add it before approving."
    if when.ambiguities and when.start is None:
        return {"status": "needs_input", "summary": "The time is ambiguous.", "question": when.ambiguities[0], "data": {}}
    if when.start is None and when.period is None:
        slots = yield ("find_free_slots", {"date": when.date.isoformat(), "duration_minutes": duration})
        options = ", ".join(s["label"] for s in slots["slots"][:3]) or "none during working hours"
        return {
            "status": "needs_input", "summary": f"You didn't give a time for {when.date:%A, %B} {when.date.day}. Open slots: {options}.",
            "question": "Which time works for you?", "data": {"date": when.date.isoformat(), "slots": slots["slots"][:3]},
        }
    if when.start is not None:
        start = when.start
        end_value = when.end or (datetime.combine(when.date, start) + timedelta(minutes=duration)).time()
        conflict = yield ("check_conflicts", {"start": f"{when.date}T{start:%H:%M}:00", "end": f"{when.date}T{end_value:%H:%M}:00"})
        if conflict["conflicts"]:
            names = ", ".join(c["title"] for c in conflict["conflicts"])
            return {"status": "needs_input", "summary": f"That time conflicts with {names}.", "question": "Should I look for another time?", "data": {"conflicts": conflict["conflicts"]}}
    else:
        slots = yield ("find_free_slots", {"date": when.date.isoformat(), "duration_minutes": duration, "period": when.period})
        if not slots["slots"]:
            return {"status": "needs_input", "summary": f"You have no {duration}-minute opening that {when.period} on {when.date}.", "question": "Would another day work?", "data": {}}
        start = time.fromisoformat(slots["slots"][0]["start_local"])
        end_value = time.fromisoformat(slots["slots"][0]["end_local"])
    proposal = yield (
        "propose_event",
        {"title": title, "start": f"{when.date}T{start:%H:%M}:00", "end": f"{when.date}T{end_value:%H:%M}:00", "attendees": attendees, "description": f"Requested: {truncate(request, 400)}"},
    )
    return {
        "status": "completed", "summary": f"Proposed **{title}** on {when.date:%A, %B} {when.date.day}, {start:%H:%M}–{end_value:%H:%M} ({task['timezone']}). It is not on your calendar until you approve it.{notes}",
        "data": {"approval_id": proposal["approval_id"], "event_id": proposal.get("event_id")},
    }


def _list_events(task, up) -> Script:
    res = yield ("list_events", {"days": 7})
    if not res["events"]:
        return {"status": "completed", "summary": "Nothing on your calendar for the next 7 days.", "data": {"count": 0}}
    lines = [f"- {e['local_start']}: **{e['title']}**" for e in res["events"][:10]]
    return {"status": "completed", "summary": f"{len(res['events'])} event(s) in the next 7 days:\n" + "\n".join(lines), "data": {"count": len(res["events"])}}


def _overdue(task, up) -> Script:
    res = yield ("list_tasks", {"overdue": True, "limit": 25})
    if not res["tasks"]:
        return {"status": "completed", "summary": "You have no overdue tasks.", "data": {"count": 0}}
    lines = [f"- **{t['title']}** ({t['priority']}), due {t['due_local']}" for t in res["tasks"]]
    return {"status": "completed", "summary": f"{len(res['tasks'])} overdue task(s):\n" + "\n".join(lines), "data": {"task_ids": [t["id"] for t in res["tasks"]]}}


def _list_tasks(task, up) -> Script:
    res = yield ("list_tasks", {"status": "open", "limit": 15})
    lines = [f"- **{t['title']}** ({t['priority']}, {t['status']})" + (f", due {t['due_local']}" if t.get("due_local") else "") for t in res["tasks"]]
    return {"status": "completed", "summary": f"{len(res['tasks'])} open task(s):\n" + "\n".join(lines) if lines else "You have no open tasks.", "data": {"count": len(lines)}}


def _create_task(task, up) -> Script:
    request = task["inputs"]["request"]
    match = re.search(r"\btask\s+(?:to|for|called|named|:)\s*(.+)$", request, re.I | re.S) or re.search(r"\badd\s+(.+?)\s+to my tasks", request, re.I)
    title = (match[1] if match else request).strip().rstrip(".")
    when = parse_when(request, _today(task))
    if when.date_text:
        title = re.sub(rf"\s*(by|on|due|before)?\s*{re.escape(when.date_text)}\b", "", title, flags=re.I).strip()
    title = title[:1].upper() + title[1:]
    priority = "critical" if re.search(r"\b(critical)\b", request, re.I) else "high" if re.search(r"\b(urgent|important|high priority)\b", request, re.I) else "medium"
    args = {"title": title, "priority": priority}
    if when.date:
        args["due_date"] = when.date.isoformat()
    created = yield ("create_task", args)
    due = f", due {when.date:%A, %B} {when.date.day}" if when.date else ""
    return {"status": "completed", "summary": f"Created task **{title}** ({priority} priority{due}).", "data": {"task_id": created["task_id"]}}


def _create_from_request(task, up) -> Script:
    source = up["s1"]["data"]
    created = yield (
        "create_task",
        {"title": f"Deliver report: {truncate(source['subject'], 150)}", "description": f"Requested by {source['sender']}.", "priority": "high", "due_date": source["deadline"], "related_email_id": source["email_id"], "category": "Reports"},
    )
    return {"status": "completed", "summary": f"Created a high-priority task due {source['deadline']}.", "data": {"task_id": created["task_id"], "deadline": source["deadline"]}}


def _complete_task(task, up) -> Script:
    res = yield ("list_tasks", {"status": "open", "query": " ".join(_terms(re.sub(r"\b(mark|task|done|complete|completed|as)\b", " ", task["inputs"]["request"].lower()))), "limit": 3})
    if len(res["tasks"]) != 1:
        return {"status": "needs_input", "summary": f"I found {len(res['tasks'])} matching tasks.", "question": "Which task did you mean?", "data": {"matches": [t["title"] for t in res["tasks"]]}}
    target = res["tasks"][0]
    yield ("update_task", {"task_id": target["id"], "status": "completed"})
    return {"status": "completed", "summary": f"Marked **{target['title']}** as completed.", "data": {"task_id": target["id"]}}


def _report(task, up) -> Script:
    topic = (up.get("s1") or {}).get("data", {}).get("topic") if up else None
    query = topic or " ".join(_terms(task["inputs"]["request"])) or "project"
    found = yield ("search_documents", {"query": query, "limit": 3})
    if not found["documents"]:
        return {"status": "needs_input", "summary": f"No documents match \"{query}\", so I did not write a report; I won't invent content.", "question": "Which documents should I use? You can upload them on the Documents page.", "data": {}}
    labels, facts, source_ids = [], [], []
    for index, doc in enumerate(found["documents"], 1):
        full = yield ("read_document", {"document_id": doc["id"]})
        labels.append(f"- [S{index}] {full['title']}")
        source_ids.append(doc["id"])
        for sentence in top_sentences(full["content"], query, 4):
            facts.append(f"- {sentence} [S{index}]")
    title = f"Report: {truncate(query.title(), 80)}"
    content = (
        f"# {title}\n\n## Verified facts\n_Quoted from the source documents._\n\n" + "\n".join(facts)
        + "\n\n## AI suggestions\n_Not stated in the sources. Review before relying on these._\n\n- Confirm that the figures above are still current before sending.\n\n## Sources\n" + "\n".join(labels)
    )
    saved = yield ("save_report", {"title": title, "content_markdown": content, "source_document_ids": source_ids})
    return {"status": "completed", "summary": f"Drafted **{title}** from {len(source_ids)} source document(s). Review and edit it on the Documents page.", "data": {"document_id": saved["document_id"], "sources": source_ids}}


def _summarize_docs(task, up) -> Script:
    found = yield ("search_documents", {"query": " ".join(_terms(task["inputs"]["request"])) or "project", "limit": 3})
    if not found["documents"]:
        return {"status": "needs_input", "summary": "No matching documents found.", "question": "Which document do you mean?", "data": {}}
    parts = []
    for doc in found["documents"]:
        full = yield ("read_document", {"document_id": doc["id"]})
        parts.append(f"**{full['title']}**: " + " ".join(top_sentences(full["content"], "", 2)))
    return {"status": "completed", "summary": "\n\n".join(parts), "data": {"document_ids": [d["id"] for d in found["documents"]]}}


def _compare(task, up) -> Script:
    listing = yield ("search_documents", {"query": " ".join(_terms(task["inputs"]["request"])) or "project", "limit": 2})
    if len(listing["documents"]) < 2:
        return {"status": "needs_input", "summary": "I need at least two matching documents to compare.", "question": "Which two documents?", "data": {}}
    a = yield ("read_document", {"document_id": listing["documents"][0]["id"]})
    b = yield ("read_document", {"document_id": listing["documents"][1]["id"]})
    set_a, set_b = set(_terms(a["content"])), set(_terms(b["content"]))
    only_a, only_b = sorted(set_a - set_b)[:10], sorted(set_b - set_a)[:10]
    return {
        "status": "completed",
        "summary": f"**{a['title']}** vs **{b['title']}**: {len(set_a & set_b)} shared terms.\n- Only in {a['title']}: {', '.join(only_a) or 'none'}\n- Only in {b['title']}: {', '.join(only_b) or 'none'}",
        "data": {"documents": [a["id"], b["id"]]},
    }


def _daily(task, up) -> Script:
    snap = yield ("work_snapshot", {"period": "today"})
    return _snapshot_result("Today", snap)


def _snapshot_result(label: str, snap: dict) -> dict:
    lines = [
        f"**{label}:** {snap['tasks_completed']} task(s) completed, {snap['tasks_open']} open, {snap['tasks_overdue']} overdue.",
        f"**Inbox:** {snap['emails_needing_reply']} email(s) need a reply.",
        f"**Calendar:** {len(snap['events'])} event(s).",
        f"**Awaiting your approval:** {snap['pending_approvals']}.",
    ]
    if snap["top_open_tasks"]:
        lines.append("**Next up:** " + "; ".join(t["title"] for t in snap["top_open_tasks"][:3]) + ".")
    return {"status": "completed", "summary": "\n".join(lines), "data": {k: snap[k] for k in ("tasks_completed", "tasks_open", "tasks_overdue")}}


def _weekly(task, up) -> Script:
    snap = yield ("work_snapshot", {"period": "week"})
    result = _snapshot_result("This week", snap)
    today = _today(task)
    saved = yield ("save_report", {"title": f"Weekly report, {today:%b} {today.day}", "content_markdown": f"# Weekly report\n\n## Verified facts\n_Computed from your stored records._\n\n{result['summary']}", "source_document_ids": []})
    result["data"]["document_id"] = saved["document_id"]
    return result


def _remind_before(task, up) -> Script:
    source = up["s1"]["data"]
    due = date.fromisoformat(source["deadline"])
    at = due - timedelta(days=task["inputs"].get("days_before", 1))
    res = yield ("schedule_reminder", {"title": f"Due tomorrow: {truncate(source['subject'], 180)}", "remind_at": f"{at}T09:00:00", "task_id": up["s3"]["data"]["task_id"]})
    return {"status": "completed", "summary": f"Reminder set for {at:%A, %B} {at.day} at 09:00.", "data": {"notification_scheduled": res["scheduled"], "remind_at": at.isoformat()}}


def _remind(task, up) -> Script:
    request = task["inputs"]["request"]
    when = parse_when(request, _today(task))
    if when.date is None or (when.start is None and when.ambiguities):
        return {"status": "needs_input", "summary": "I need a specific time for the reminder.", "question": "When should I remind you?", "data": {}}
    match = re.search(r"\bremind me (?:to|about)\s+(.+)$", request, re.I | re.S)
    text = match[1].strip().rstrip(".") if match else request
    if when.date_text:
        text = re.sub(rf"\s*(on|by|at)?\s*{re.escape(when.date_text)}\b", "", text, flags=re.I).strip()
    moment = when.start or time(9, 0)
    yield ("schedule_reminder", {"title": text[:1].upper() + text[1:], "remind_at": f"{when.date}T{moment:%H:%M}:00"})
    return {"status": "completed", "summary": f"Reminder set for {when.date:%A, %B} {when.date.day} at {moment:%H:%M}.", "data": {}}


def _deadlines(task, up) -> Script:
    res = yield ("upcoming_deadlines", {"days": 7})
    lines = [f"- **{i['title']}** ({i['type']}), {i['due_local']}" for i in res["items"]]
    return {"status": "completed", "summary": f"{len(lines)} deadline(s) in the next 7 days:\n" + "\n".join(lines) if lines else "No deadlines in the next 7 days.", "data": {"count": len(lines)}}


SCRIPTS = {
    "summarize_important": _summarize_important, "needs_reply": _needs_reply, "categorize": _categorize, "find_deadline": _find_deadline,
    "find_request": _find_request, "draft_reply": _draft_reply, "schedule": _schedule, "list": None, "overdue": _overdue, "create": _create_task,
    "create_from_request": _create_from_request, "complete": _complete_task, "report": _report, "summarize": _summarize_docs, "compare": _compare,
    "daily": _daily, "weekly": _weekly, "remind_before": _remind_before, "remind": _remind, "deadlines": _deadlines,
}


class MockProvider(LLMProvider):
    name = "mock"
    model = "mock-employee-1"
    simulated = True

    def complete(self, request: LLMRequest) -> LLMResponse:
        if request.purpose.startswith("agent."):
            return self._agent(request)
        return self._submit(self._structured(request))

    @staticmethod
    def _submit(args: dict) -> LLMResponse:
        return LLMResponse(tool_calls=[ToolCall(uuid.uuid4().hex, SUBMIT_TOOL, args)], stop_reason="tool_use")

    def _structured(self, request: LLMRequest) -> dict:
        user = request.messages[0].content
        if request.purpose == "manager.plan":
            return _plan(_tag(user, "request")["text"])
        today = date.today()
        data = _tool_payload(user)
        if request.purpose == "email.analyze":
            return analyze_email_rules(data, today)
        if request.purpose == "email.draft_reply":
            task = _tag(user, "task") or {}
            return draft_reply_rules(data, task.get("tone", "professional"), task.get("instructions"))
        if request.purpose == "document.analyze":
            return {"summary": " ".join(top_sentences(data["content"], "", 3)), "key_points": top_sentences(data["content"], "deadline budget risk total milestone", 5), "topics": sorted(set(_terms(data["content"])), key=data["content"].lower().count, reverse=True)[:6]}
        if request.purpose == "document.compare":
            a, b = data["documents"][:2]
            set_a, set_b = set(_terms(a["content"])), set(_terms(b["content"]))
            return {
                "summary": f"{a['title']} and {b['title']} share {len(set_a & set_b)} key terms.",
                "similarities": sorted(set_a & set_b)[:12], "differences": [f"Only in {a['title']}: {', '.join(sorted(set_a - set_b)[:12]) or 'none'}", f"Only in {b['title']}: {', '.join(sorted(set_b - set_a)[:12]) or 'none'}"],
            }
        if request.purpose == "report.generate":
            terms = (_tag(user, "task") or {}).get("topic", "")
            facts = []
            for index, doc in enumerate(data["documents"], 1):
                facts.extend({"statement": s, "source": f"S{index}"} for s in top_sentences(doc["content"], terms, 4))
            return {"title": f"Report: {truncate(terms.title() or 'Selected documents', 80)}", "executive_summary": facts[0]["statement"] if facts else "", "facts": facts, "suggestions": ["Confirm that the figures above are still current before sending."]}
        if request.purpose == "workflow.summary":
            return {"summary": "\n".join(f"- {k.replace('_', ' ').title()}: {v}" for k, v in data.items() if isinstance(v, (int, str)))}
        raise ValueError(f"MockProvider has no handler for {request.purpose}")

    def _agent(self, request: LLMRequest) -> LLMResponse:
        first = request.messages[0].content
        task = _tag(first, "task")
        upstream = _tag(first, "untrusted_data") or {}
        action = task["inputs"].get("action")
        factory = _list_events if (action == "list" and request.purpose == "agent.calendar") else _list_tasks if action == "list" else SCRIPTS.get(action)
        if factory is None:
            return self._submit({"status": "failed", "summary": f"Mock AI has no script for {action}.", "data": {}})
        results = [_tool_payload(m.content) for m in request.messages if m.role == "tool"]
        script = factory(task, upstream)
        try:
            call = next(script)
            for result in results:
                if "error" in result:
                    return self._submit({"status": "failed", "summary": f"A tool failed: {result['error']}", "data": {}})
                call = script.send(result)
        except StopIteration as stop:
            return self._submit(stop.value)
        return LLMResponse(tool_calls=[ToolCall(uuid.uuid4().hex, call[0], call[1])], stop_reason="tool_use")

    def stream(self, request: LLMRequest) -> Iterator[str]:
        payload = _tag(request.messages[0].content, "results") or {}
        parts = [s["summary"] for s in payload.get("steps", []) if s.get("summary")]
        questions = [s["question"] for s in payload.get("steps", []) if s.get("question")]
        if questions:
            parts.append(questions[0])
        text = "\n\n".join(parts) or "I wasn't able to complete that."
        yield from re.findall(r"\S+\s*", text)

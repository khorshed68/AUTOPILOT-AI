from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from collections.abc import Iterator
from datetime import timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.llm.base import LLMError, LLMProvider
from app.agents.llm.factory import get_llm
from app.agents.orchestrator import Orchestrator
from app.api.deps import DB, CurrentUser, limit_ai
from app.core.errors import Conflict, NotFound, ServiceUnavailable
from app.database.base import utcnow
from app.database.session import SessionLocal
from app.models import AgentExecution, AgentRun, Conversation, Message, UserPreference
from app.schemas.api import ChatIn, ConversationDetail, ConversationOut, MessageIn, MessageOut
from app.utils.text import truncate

router = APIRouter(prefix="/ai", tags=["ai"])
logger = logging.getLogger("autopilot.chat")


def _conversation(db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation:
    row = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id))
    if row is None:
        raise NotFound("Conversation not found.")
    return row


def _provider() -> LLMProvider:
    try:
        return get_llm()
    except LLMError as exc:
        raise ServiceUnavailable(exc.user_message, code="ai_unavailable") from exc


def _guard(db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    prefs = db.scalar(select(UserPreference).where(UserPreference.user_id == user_id))
    if prefs.employee_paused:
        raise Conflict("Your AI employee is paused. Resume it to send new requests.", code="employee_paused")
    busy = db.scalar(select(AgentRun.id).where(AgentRun.conversation_id == conversation_id, AgentRun.status == "running", AgentRun.started_at > utcnow() - timedelta(minutes=10)))
    if busy:
        raise Conflict("This conversation is still working on your previous request.", code="run_in_progress")


def _history(db: Session, conversation_id: uuid.UUID, exclude: uuid.UUID) -> list[Message]:
    rows = db.scalars(select(Message).where(Message.conversation_id == conversation_id, Message.id != exclude, Message.status == "complete").order_by(Message.created_at.desc()).limit(8)).all()
    return list(reversed(rows))


def _respond(db: Session, user_id: uuid.UUID, conversation: Conversation, message: Message, stream: bool, llm: LLMProvider):
    history = _history(db, conversation.id, message.id)
    if not stream:
        return Orchestrator(db, user_id, llm=llm).run(conversation.id, message, history)
    conversation_id, message_id = conversation.id, message.id
    events: queue.Queue = queue.Queue()

    def work() -> None:
        # Runs independently of the HTTP connection, so a dropped client can't leave a half-recorded run.
        with SessionLocal() as session:
            try:
                msg = session.get(Message, message_id)
                hist = _history(session, conversation_id, message_id)
                Orchestrator(session, user_id, emit=events.put, llm=llm).run(conversation_id, msg, hist)
            except Exception:
                logger.exception("chat_worker_crashed")
                events.put({"type": "error", "message": "Something went wrong while handling that request.", "retryable": True})
            finally:
                events.put(None)

    db.close()  # release the request's connection while the worker thread runs
    threading.Thread(target=work, daemon=True, name="chat-run").start()

    def generate() -> Iterator[str]:
        yield f"event: accepted\ndata: {json.dumps({'type': 'accepted', 'conversation_id': str(conversation_id), 'user_message_id': str(message_id)})}\n\n"
        while True:
            try:
                event = events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if event is None:
                return
            yield f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _start(db: Session, user_id: uuid.UUID, conversation: Conversation, text: str, stream: bool):
    llm = _provider()
    _guard(db, user_id, conversation.id)
    message = Message(conversation_id=conversation.id, user_id=user_id, role="user", content=text)
    db.add(message)
    conversation.updated_at = utcnow()
    db.commit()
    return _respond(db, user_id, conversation, message, stream, llm)


@router.post("/chat", dependencies=[Depends(limit_ai)], response_model=None)
def chat(body: ChatIn, db: DB, user: CurrentUser):
    if body.conversation_id:
        conversation = _conversation(db, user.id, body.conversation_id)
    else:
        conversation = Conversation(user_id=user.id, title=truncate(body.message.strip().replace("\n", " "), 70))
        db.add(conversation)
        db.commit()
    result = _start(db, user.id, conversation, body.message, body.stream)
    return MessageOut.model_validate(result) if isinstance(result, Message) else result


@router.get("/conversations", response_model=list[ConversationOut])
def conversations(db: DB, user: CurrentUser):
    return db.scalars(select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.updated_at.desc()).limit(100)).all()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def conversation_detail(conversation_id: uuid.UUID, db: DB, user: CurrentUser):
    return _conversation(db, user.id, conversation_id)


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: uuid.UUID, db: DB, user: CurrentUser):
    db.delete(_conversation(db, user.id, conversation_id))
    db.commit()


@router.post("/conversations/{conversation_id}/messages", dependencies=[Depends(limit_ai)], response_model=None)
def post_message(conversation_id: uuid.UUID, body: MessageIn, db: DB, user: CurrentUser):
    result = _start(db, user.id, _conversation(db, user.id, conversation_id), body.message, body.stream)
    return MessageOut.model_validate(result) if isinstance(result, Message) else result


@router.post("/conversations/{conversation_id}/messages/{message_id}/retry", dependencies=[Depends(limit_ai)], response_model=None)
def retry(conversation_id: uuid.UUID, message_id: uuid.UUID, db: DB, user: CurrentUser, stream: bool = True):
    conversation = _conversation(db, user.id, conversation_id)
    failed = db.scalar(select(Message).where(Message.id == message_id, Message.conversation_id == conversation.id, Message.role == "assistant", Message.status == "error"))
    if failed is None:
        raise NotFound("Only failed responses can be retried.")
    original = db.scalar(select(Message).where(Message.conversation_id == conversation.id, Message.role == "user", Message.created_at < failed.created_at).order_by(Message.created_at.desc()).limit(1))
    if original is None:
        raise NotFound("The original request could not be found.")
    llm = _provider()
    _guard(db, user.id, conversation.id)
    db.delete(failed)
    db.commit()
    result = _respond(db, user.id, conversation, original, stream, llm)
    return MessageOut.model_validate(result) if isinstance(result, Message) else result


@router.get("/runs/{run_id}/executions")
def run_executions(run_id: uuid.UUID, db: DB, user: CurrentUser):
    run = db.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user.id))
    if run is None:
        raise NotFound("Run not found.")
    rows = db.scalars(select(AgentExecution).where(AgentExecution.run_id == run.id).order_by(AgentExecution.started_at)).all()
    return [{"id": str(r.id), "agent_name": r.agent_name, "task_id": r.task_id, "attempt": r.attempt, "status": r.status, "tool_calls": r.tool_calls, "errors": r.errors, "duration_ms": r.duration_ms, "result": r.result} for r in rows]

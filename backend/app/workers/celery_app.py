from __future__ import annotations

from celery import Celery

from app.core.config import settings

celery = Celery("autopilot", broker=settings.redis_url, backend=None, include=["app.workers.tasks"])
celery.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=270,
    task_time_limit=300,
    task_ignore_result=True,
    timezone="UTC",
    broker_connection_retry_on_startup=True,
    broker_transport_options={"socket_connect_timeout": 3, "socket_timeout": 5},
    beat_schedule={
        "tick": {"task": "app.workers.tasks.tick", "schedule": 60.0},
        "scan-deadlines": {"task": "app.workers.tasks.scan_deadlines", "schedule": 900.0},
        "recover": {"task": "app.workers.tasks.recover", "schedule": 300.0},
    },
)

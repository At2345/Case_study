"""Temporal worker for playbook runs.

Run next to the API, with the same STORE_DIR / MONGO_URL so both share run state and notifications:

    TEMPORAL_ADDRESS=localhost:7233 python -m app.workflows.temporal_worker

Environment: TEMPORAL_ADDRESS (default localhost:7233), TEMPORAL_NAMESPACE (default "default"),
TEMPORAL_TASK_QUEUE (default "playbooks"), WORKFLOW_FOUR_EYES (default false).
"""
from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.config import settings
from app.workflows.service import WorkflowService

log = logging.getLogger("sop.workflows.worker")


def build_service() -> WorkflowService:
    """Same stores as create_default_app: Mongo notifications when MONGO_URL is set, else JSON files."""
    notes: Any
    if settings.mongo_uri:
        from app.store.ops import NotificationStore
        from app.store.runs import mongo_db
        notes = NotificationStore(mongo_db(settings.mongo_uri, settings.mongo_db))
    else:
        from app.store.local import JsonNotificationStore
        notes = JsonNotificationStore(settings.store_dir / "notifications.json")
    four_eyes = os.environ.get("WORKFLOW_FOUR_EYES", "false").lower() == "true"
    # Channel actions (stock, prices, EDI POs) run inside activities, so the worker needs the same hub.
    from app.channels import connector_types
    from app.channels.hub import ChannelHub, set_current
    from app.channels.store import ChannelStore
    from app.channels.vault import Vault
    set_current(ChannelHub(ChannelStore(settings.store_dir / "channels" / "channels.db"),
                           Vault.from_env_or_file(settings.store_dir / "channels"), connector_types()))
    return WorkflowService(settings.store_dir / "workflows", notes=notes, four_eyes=four_eyes)


def make_worker(client, service: WorkflowService, task_queue: str, executor: ThreadPoolExecutor):
    from temporalio.worker import Worker

    from app.workflows.temporal_activities import PlaybookActivities
    from app.workflows.temporal_workflow import PlaybookRun
    return Worker(client, task_queue=task_queue, workflows=[PlaybookRun],
                  activities=PlaybookActivities(service).all(), activity_executor=executor)


async def main() -> None:
    from temporalio.client import Client
    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    task_queue = os.environ.get("TEMPORAL_TASK_QUEUE", "playbooks")
    client = await Client.connect(address, namespace=namespace)
    with ThreadPoolExecutor(max_workers=8) as executor:
        worker = make_worker(client, build_service(), task_queue, executor)
        log.info("Playbook worker on %s (namespace=%s, queue=%s)", address, namespace, task_queue)
        await worker.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

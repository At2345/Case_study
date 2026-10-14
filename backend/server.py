"""Preview adapter: loads .env, seeds the demo users and exposes the FastAPI app as `server:app`."""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
logging.basicConfig(level=logging.INFO)

from app.api.auth import UserStore  # noqa: E402
from app.api.main import create_default_app  # noqa: E402
from app.config import settings  # noqa: E402

SEED = (("danny", "planner", "SEED_PLANNER_PASSWORD"), ("admin", "admin", "SEED_ADMIN_PASSWORD"),
        ("auditor", "auditor", "SEED_AUDITOR_PASSWORD"))


def seed_users() -> None:
    store = UserStore(settings.store_dir / "users.json")
    for name, role, var in SEED:
        pw = os.environ.get(var)
        if pw and store.authenticate(name, pw) is None:
            store.add_user(name, role, pw)


seed_users()
app = create_default_app()


@app.on_event("startup")
def start_scheduler() -> None:
    if app.state.jobs is not None and os.environ.get("SCHEDULER_ENABLED", "true").lower() == "true":
        from app.orchestration.scheduler import Scheduler
        app.state.scheduler = Scheduler(app.state.schedules, app.state.jobs)
        app.state.scheduler.start()


@app.on_event("shutdown")
def stop_scheduler() -> None:
    if getattr(app.state, "scheduler", None) is not None:
        app.state.scheduler.stop()

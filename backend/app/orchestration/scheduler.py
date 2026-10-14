"""Run schedules (cron-like, UK time) and the in-process scheduler loop."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("sop.scheduler")

TZ = "Europe/London"
BUSY = "waiting: another run in progress"
DEFAULTS = {
    "monthly_cycle": {"frequency": "monthly", "day": 2, "hour": 6, "minute": 0},
    "amazon_daily": {"frequency": "daily", "day": None, "hour": 5, "minute": 0},
}


def next_run(spec: Dict[str, Any], after: datetime) -> datetime:
    """Next fire time strictly after `after` (UTC-aware), evaluated in UK local time (handles BST)."""
    tz = ZoneInfo(spec.get("tz") or TZ)
    local = after.astimezone(tz)
    h, m = int(spec["hour"]), int(spec["minute"])
    if spec["frequency"] == "daily":
        cand = local.replace(hour=h, minute=m, second=0, microsecond=0)
        if cand <= local:
            cand = (cand + timedelta(days=1)).replace(hour=h, minute=m)
        return cand.astimezone(timezone.utc)
    day = int(spec["day"])
    y, mo = local.year, local.month
    for _ in range(3):
        cand = datetime(y, mo, day, h, m, tzinfo=tz)
        if cand > local:
            return cand.astimezone(timezone.utc)
        y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    raise ValueError("no next run")


def validate(spec: Dict[str, Any]) -> None:
    if spec["frequency"] not in ("daily", "monthly"):
        raise ValueError("Frequency must be daily or monthly.")
    if not 0 <= int(spec["hour"]) <= 23 or not 0 <= int(spec["minute"]) <= 59:
        raise ValueError("Time must be between 00:00 and 23:59.")
    if spec["frequency"] == "monthly" and not 1 <= int(spec.get("day") or 0) <= 28:
        raise ValueError("Day must be between 1 and 28 so the run happens every month.")


class Scheduler:
    """Polls the schedule store and fires due jobs through the JobRunner (claim is atomic in Mongo)."""

    def __init__(self, schedules, jobs, poll_seconds: float = 30.0) -> None:
        self.schedules, self.jobs, self.poll = schedules, jobs, poll_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def tick(self, now: Optional[datetime] = None) -> list:
        now = now or datetime.now(timezone.utc)
        fired = []
        for s in self.schedules.due(now):
            if self.jobs.busy():
                if s.get("last_outcome") != BUSY:  # wait for the other run; log the delay once
                    self.schedules.record(s["job"], now, BUSY)
                continue
            if not self.schedules.claim(s["job"], s["next_run_at"], next_run(s, now).isoformat()):
                continue
            try:
                self.jobs.start(s["job"], "scheduler")
                self.schedules.record(s["job"], now, "started")
                fired.append(s["job"])
            except RuntimeError as exc:
                self.schedules.record(s["job"], now, f"skipped: {exc}")
        return fired

    def _loop(self) -> None:
        while not self._stop.wait(self.poll):
            try:
                self.tick()
            except Exception:
                log.exception("scheduler tick failed")

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="sop-scheduler")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

"""In-process job queue.

Notes are processed in the background so uploads return immediately and clients follow
progress (queued -> transcribing -> analyzing -> done). Each engine gets its own lane:
the cloud lane runs a few jobs concurrently (they mostly wait on network I/O), the private
lane runs one at a time (inference already saturates the CPU).

The database is the source of truth for status; this module only holds jobs that are
queued or recently finished. That keeps the design to a single process with no extra
infrastructure, which is what a free-tier instance can run. Scaling out would mean moving
the queue to Postgres (SELECT ... FOR UPDATE SKIP LOCKED) or Redis and running workers
separately; the JobManager interface would stay the same.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .db import repo
from .db.models import NoteStatus
from .db.session import Database
from .errors import VaaniError
from .pipeline import NotePipeline, NoteRequest, NoteResult

log = logging.getLogger(__name__)

JOB_TIMEOUT_SECONDS = 15 * 60
FINISHED_JOB_TTL_SECONDS = 15 * 60
GENERIC_FAILURE = "Vaani is getting a lot of traffic right now. Please try again in a minute."

# Called with the job and the stage it was in when the event fired ("done"/"failed" at the end).
Listener = Callable[["Job", str], Awaitable[None]]


@dataclass
class Job:
    request: NoteRequest
    status: str = NoteStatus.QUEUED
    stage: str = "queued"
    result: NoteResult | None = None
    error: str | None = None
    error_code: str | None = None
    finished_at: float | None = None
    listeners: list[Listener] = field(default_factory=list)
    _changed: asyncio.Event = field(default_factory=asyncio.Event)
    _done: asyncio.Event = field(default_factory=asyncio.Event)
    _deliver: asyncio.Lock = field(default_factory=asyncio.Lock)  # listeners see events in order

    @property
    def id(self) -> str:
        return self.request.note_id

    @property
    def finished(self) -> bool:
        return self.status in (NoteStatus.DONE, NoteStatus.FAILED)


class Lane:
    def __init__(self, name: str, concurrency: int, typical_seconds: float):
        self.name = name
        self.concurrency = max(1, concurrency)
        self.avg_seconds = typical_seconds
        self.pending: deque[Job] = deque()
        self.running = 0
        self.cond = asyncio.Condition()

    def position(self, job: Job) -> int | None:
        for index, queued in enumerate(self.pending):
            if queued is job:
                return index + 1
        return None

    def eta_seconds(self, position: int | None) -> int:
        ahead = (position - 1 if position else 0) + self.running
        rounds = math.floor(ahead / self.concurrency) + 1
        return int(rounds * self.avg_seconds)

    def record(self, seconds: float) -> None:
        self.avg_seconds = 0.7 * self.avg_seconds + 0.3 * seconds


class JobManager:
    def __init__(self, db: Database, pipeline: NotePipeline):
        self.db = db
        self.pipeline = pipeline
        self.jobs: dict[str, Job] = {}
        self.lanes: dict[str, Lane] = {
            engine.name: Lane(engine.name, engine.concurrency, engine.typical_seconds)
            for engine in pipeline.registry.available()
        }
        self._tasks: set[asyncio.Task] = set()
        self._workers: list[asyncio.Task] = []

    async def start(self) -> None:
        interrupted = await self.db.run(repo.mark_interrupted)
        if interrupted:
            log.warning("Marked %d note(s) interrupted by the previous shutdown as failed", interrupted)
        for lane in self.lanes.values():
            for i in range(lane.concurrency):
                self._workers.append(asyncio.create_task(self._worker(lane), name=f"worker-{lane.name}-{i}"))
        self._workers.append(asyncio.create_task(self._janitor(), name="job-janitor"))

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._workers.clear()

    async def submit(self, request: NoteRequest, listener: Listener | None = None) -> Job:
        lane = self.lanes[request.engine]
        job = Job(request=request)
        if listener:
            job.listeners.append(listener)
        self.jobs[job.id] = job
        async with lane.cond:
            lane.pending.append(job)
            lane.cond.notify()
        return job

    def get(self, note_id: str) -> Job | None:
        return self.jobs.get(note_id)

    def progress(self, note_id: str) -> dict | None:
        """Live progress for a queued or running job (None if it isn't in memory)."""
        job = self.jobs.get(note_id)
        if job is None or job.finished:
            return None
        lane = self.lanes[job.request.engine]
        position = lane.position(job)
        return {"stage": job.stage, "queue_position": position, "eta_seconds": lane.eta_seconds(position)}

    async def wait_for_change(self, note_id: str, timeout: float) -> None:
        job = self.jobs.get(note_id)
        if job is None or job.finished:
            return
        event = job._changed
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(event.wait(), timeout)

    async def wait_until_done(self, job: Job, timeout: float = JOB_TIMEOUT_SECONDS + 60) -> Job:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(job._done.wait(), timeout)
        return job

    async def _worker(self, lane: Lane) -> None:
        while True:
            async with lane.cond:
                await lane.cond.wait_for(lambda: bool(lane.pending))
                job = lane.pending.popleft()
                lane.running += 1
            try:
                await self._process(job, lane)
            finally:
                lane.running -= 1

    async def _process(self, job: Job, lane: Lane) -> None:
        started = time.perf_counter()

        async def on_stage(stage: str) -> None:
            job.status, job.stage = NoteStatus.PROCESSING, stage
            try:
                await self.db.run(repo.set_stage, job.id, stage)
            except Exception:  # progress is best-effort; never fail a note over it
                log.exception("could not persist stage for note %s", job.id)
            self._emit(job)

        try:
            result = await asyncio.wait_for(self.pipeline.run(job.request, on_stage), JOB_TIMEOUT_SECONDS)
            await self.db.run(repo.complete_note, job.id, result.as_record())
            job.result, job.status, job.stage = result, NoteStatus.DONE, "done"
            lane.record(time.perf_counter() - started)
            log.info("note %s done in %dms via %s", job.id, result.total_ms, result.models)
        except VaaniError as exc:
            log.warning("note %s failed (%s): %s", job.id, exc.code, exc.detail)
            await self._fail(job, exc.user_message, exc.code)
        except TimeoutError:
            log.error("note %s timed out after %ss", job.id, JOB_TIMEOUT_SECONDS)
            await self._fail(job, "Processing took too long. Please try a shorter recording.", "timeout")
        except Exception:
            log.exception("note %s failed unexpectedly", job.id)
            await self._fail(job, GENERIC_FAILURE, "internal_error")
        finally:
            if job.request.audio_path is not None:
                job.request.audio_path.unlink(missing_ok=True)
            job.finished_at = time.monotonic()
            job._done.set()
            self._emit(job)

    async def _fail(self, job: Job, message: str, code: str) -> None:
        job.status, job.stage, job.error, job.error_code = NoteStatus.FAILED, "failed", message, code
        try:
            await self.db.run(repo.fail_note, job.id, message)
        except Exception:
            log.exception("could not persist failure for note %s", job.id)

    def _emit(self, job: Job) -> None:
        # Wake long-pollers, then hand the event to channel listeners without blocking the job.
        event, job._changed = job._changed, asyncio.Event()
        event.set()
        stage = job.stage
        for listener in job.listeners:
            task = asyncio.create_task(self._safe_listener(listener, job, stage))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _safe_listener(listener: Listener, job: Job, stage: str) -> None:
        async with job._deliver:
            try:
                await listener(job, stage)
            except Exception:
                log.exception("job listener failed for note %s", job.id)

    async def _janitor(self) -> None:
        while True:
            await asyncio.sleep(60)
            cutoff = time.monotonic() - FINISHED_JOB_TTL_SECONDS
            for note_id in [k for k, j in self.jobs.items() if j.finished_at and j.finished_at < cutoff]:
                self.jobs.pop(note_id, None)

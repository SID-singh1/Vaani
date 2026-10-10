import asyncio

import pytest

from conftest import FakeASR, FakeLLM
from vaani.db import repo
from vaani.db.migrate import run_migrations
from vaani.db.models import NoteStatus
from vaani.db.session import Database
from vaani.engines.base import Engine
from vaani.engines.registry import EngineRegistry
from vaani.jobs import JobManager
from vaani.pipeline import NotePipeline, NoteRequest

pytestmark = pytest.mark.anyio


class SlowLLM(FakeLLM):
    def __init__(self, gate: asyncio.Event):
        super().__init__("slow")
        self.gate = gate

    async def generate(self, **kwargs):
        await self.gate.wait()
        return await super().generate(**kwargs)


def make_manager(tmp_path, llm, concurrency=1):
    db = Database(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    run_migrations(db.engine)
    engine = Engine(
        name="cloud",
        label="Fast",
        description="",
        asr=FakeASR(),
        llms=[llm],
        transliteration="llm",
        concurrency=concurrency,
        max_audio_seconds=600,
        typical_seconds=10,
    )
    return db, JobManager(db, NotePipeline(EngineRegistry({"cloud": engine}, "cloud")))


async def queue_note(db, manager, text="Kal meeting hai, slides ready rakhna please.", listener=None):
    note = await db.run(repo.create_note, user_id="u1", channel="web", engine="cloud", source="text")
    job = await manager.submit(
        NoteRequest(note_id=note.id, user_id="u1", engine="cloud", source="text", text=text), listener
    )
    return job


async def test_jobs_run_in_order_with_queue_positions(tmp_path):
    gate = asyncio.Event()
    db, manager = make_manager(tmp_path, SlowLLM(gate))
    await manager.start()
    try:
        first, second, third = [await queue_note(db, manager) for _ in range(3)]
        await asyncio.sleep(0.05)
        assert manager.progress(first.id)["queue_position"] is None  # running
        assert manager.progress(second.id)["queue_position"] == 1
        assert manager.progress(third.id)["queue_position"] == 2
        assert manager.progress(third.id)["eta_seconds"] >= manager.progress(second.id)["eta_seconds"]

        gate.set()
        for job in (first, second, third):
            await manager.wait_until_done(job, timeout=5)
            assert job.status == NoteStatus.DONE
        stored = await db.run(repo.get_note, third.id)
        assert stored.status == NoteStatus.DONE and stored.title == "Team meeting tomorrow"
    finally:
        await manager.stop()


async def test_listeners_see_stages_in_order(tmp_path):
    db, manager = make_manager(tmp_path, FakeLLM())
    seen = []

    async def listener(job, stage):
        await asyncio.sleep(0.01 if stage == "analyzing" else 0)  # a slow early event must not arrive late
        seen.append(stage)

    await manager.start()
    try:
        job = await queue_note(db, manager, listener=listener)
        await manager.wait_until_done(job, timeout=5)
        await asyncio.sleep(0.1)
        assert seen == ["analyzing", "done"]
    finally:
        await manager.stop()


async def test_failure_is_recorded_with_safe_message(tmp_path):
    db, manager = make_manager(tmp_path, FakeLLM(fail=True))
    await manager.start()
    try:
        job = await queue_note(db, manager)
        await manager.wait_until_done(job, timeout=5)
        stored = await db.run(repo.get_note, job.id)
        assert job.status == NoteStatus.FAILED == stored.status
        assert "traffic" in stored.error_message and "fake-llm" not in stored.error_message
    finally:
        await manager.stop()


async def test_restart_marks_interrupted_jobs_failed(tmp_path):
    db, manager = make_manager(tmp_path, FakeLLM())
    stranded = await db.run(repo.create_note, user_id="u1", channel="web", engine="cloud", source="text")
    await db.run(repo.set_stage, stranded.id, "transcribing")

    await manager.start()  # a fresh process: whatever was in flight is gone
    await manager.stop()
    note = await db.run(repo.get_note, stranded.id)
    assert note.status == NoteStatus.FAILED and "restart" in note.error_message


async def test_audio_file_is_deleted_even_on_failure(tmp_path):
    db, manager = make_manager(tmp_path, FakeLLM(fail=True))
    audio = tmp_path / "note.ogg"
    audio.write_bytes(b"not really audio")
    await manager.start()
    try:
        note = await db.run(repo.create_note, user_id="u1", channel="web", engine="cloud", source="audio")
        job = await manager.submit(
            NoteRequest(note_id=note.id, user_id="u1", engine="cloud", source="audio", audio_path=audio)
        )
        await manager.wait_until_done(job, timeout=10)
        assert job.status == NoteStatus.FAILED
        assert not audio.exists()
    finally:
        await manager.stop()

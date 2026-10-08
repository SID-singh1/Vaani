"""Evaluate Vaani on the clips in evaluation/test_manifest.json.

    python scripts/run_eval_suite.py                          # cloud engine, all clips, LLM judge on
    python scripts/run_eval_suite.py --source human           # real recordings only
    python scripts/run_eval_suite.py --llm gemini:gemini-3.5-flash-lite   # compare another LLM
    python scripts/run_eval_suite.py --engine private --llm-model models/phi-3-mini-q4_k_m.gguf
    python scripts/run_eval_suite.py --report-only            # rebuild the report from saved runs

Speech-to-text output is cached per clip and model (evaluation/.cache/asr), so comparing
LLMs doesn't re-spend speech quota; pass --fresh-asr to re-transcribe (and re-time) audio.
Each run is saved to evaluation/results/<config>.json and the Markdown report is rebuilt
from every saved run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402

from evaluation.judge import Judge  # noqa: E402
from evaluation.metrics import basic_normalize, cer, hinglish_normalize, wer  # noqa: E402
from evaluation.report import RESULTS_DIR, write_report  # noqa: E402
from vaani.analysis import NoteAnalyzer  # noqa: E402
from vaani.audio import probe_duration  # noqa: E402
from vaani.config import load_settings  # noqa: E402
from vaani.engines.gemini import GeminiClient  # noqa: E402
from vaani.engines.openai_compat import ChatCompletionsClient  # noqa: E402
from vaani.engines.registry import GROQ_OPENAI_BASE, build_registry  # noqa: E402
from vaani.text.cleanup import clean_transcript  # noqa: E402

MANIFEST = ROOT / "evaluation" / "test_manifest.json"
ASR_CACHE = ROOT / "evaluation" / ".cache" / "asr"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def make_llm(spec: str, settings, http):
    provider, _, model = spec.partition(":")
    if provider == "gemini":
        return GeminiClient(settings.gemini_api_key, model, http)
    if provider == "groq":
        extra = {"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else {}
        return ChatCompletionsClient(name=f"groq:{model}", base_url=GROQ_OPENAI_BASE, model=model, http=http,
                                     api_key=settings.groq_api_key, extra_body=extra)
    raise SystemExit(f"unknown LLM spec {spec!r}; use gemini:<model> or groq:<model>")


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
    except OSError:
        return "unknown"


async def transcribe_cached(engine, clip_id: str, path: Path, fresh: bool) -> tuple[str, float]:
    cache = ASR_CACHE / slug(engine.asr.name) / f"{clip_id}.json"
    if cache.exists() and not fresh:
        data = json.loads(cache.read_text(encoding="utf-8"))
        return data["text"], data["seconds"]
    started = time.perf_counter()
    result = await engine.asr.transcribe(path)
    seconds = time.perf_counter() - started
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"text": result.text, "seconds": seconds}, ensure_ascii=False), encoding="utf-8")
    return result.text, seconds


async def evaluate_clip(clip: dict, engine, llms, judge: Judge | None, fresh_asr: bool) -> dict:
    path = ROOT / clip["audio_file"]
    duration = await probe_duration(path) or 0.0
    raw, asr_seconds = await transcribe_cached(engine, clip["id"], path, fresh_asr)
    transcript_in = clean_transcript(raw)

    started = time.perf_counter()
    analyzer = NoteAnalyzer(llms, engine.transliteration)
    outcome = await analyzer.process(transcript_in)
    llm_seconds = time.perf_counter() - started

    reference = clip["reference_transcript"]
    hypothesis = outcome.transcript
    tasks = [item.task for item in outcome.analysis.action_items]
    row = {
        "id": clip["id"],
        "title": clip["title"],
        "source": clip.get("source", "tts"),
        "duration_sec": round(duration, 1),
        "asr_sec": round(asr_seconds, 2),
        "llm_sec": round(llm_seconds, 2),
        "total_sec": round(asr_seconds + llm_seconds, 2),
        "rtf": round((asr_seconds + llm_seconds) / duration, 3) if duration else None,
        "wer": round(100 * wer(reference, hypothesis, basic_normalize), 2),
        "cer": round(100 * cer(reference, hypothesis, basic_normalize), 2),
        "hwer": round(100 * wer(reference, hypothesis, hinglish_normalize), 2),
        "transliteration": outcome.transliteration,
        "models": outcome.models,
        "hypothesis": hypothesis,
        "title_out": outcome.analysis.title,
        "summary": outcome.analysis.summary,
        "action_items": outcome.analysis.action_items_dicts(),
        "sentiment": outcome.analysis.sentiment,
    }
    if judge is not None:
        row["judge"] = await judge.grade(reference, clip.get("expected_actions", []), row["summary"], tasks)
    return row


async def run(args) -> None:
    settings = load_settings()
    if args.engine == "private":
        os.environ["PRIVATE_ENGINE_ENABLED"] = "true"
        settings = load_settings()
    async with httpx.AsyncClient(timeout=180) as http:
        registry = build_registry(settings, http)
        engine = registry.get(args.engine)
        llms = [make_llm(spec, settings, http) for spec in args.llm] if args.llm else engine.llms
        judge = None if args.no_judge else Judge(make_llm(args.judge, settings, http))

        clips = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if args.source != "all":
            clips = [c for c in clips if c.get("source", "tts") == args.source]
        if args.id:
            clips = [c for c in clips if c["id"] in args.id]
        clips = [c for c in clips if (ROOT / c["audio_file"]).exists()][: args.limit or None]
        if not clips:
            raise SystemExit("No clips with audio found. Run scripts/bootstrap_eval_audio.py first.")

        config_name = args.name or "-".join([engine.name, slug(engine.asr.name), *[slug(llm.name) for llm in llms[:1]]])
        print(f"Evaluating {len(clips)} clip(s) | engine={engine.name} asr={engine.asr.name} "
              f"llm={' -> '.join(llm.name for llm in llms)} judge={judge.llm.name if judge else 'off'}")
        rows = []
        for i, clip in enumerate(clips, 1):
            print(f"[{i}/{len(clips)}] {clip['id']} ({clip.get('source', 'tts')})...", end=" ", flush=True)
            try:
                row = await evaluate_clip(clip, engine, llms, judge, args.fresh_asr)
            except Exception as exc:  # keep going; a failed clip is reported, not hidden
                print(f"FAILED: {exc}")
                rows.append({"id": clip["id"], "title": clip["title"], "source": clip.get("source", "tts"),
                             "error": str(exc)[:300]})
                continue
            judged = row.get("judge") or {}
            recall = judged.get("recall")
            print(f"WER {row['wer']}% hWER {row['hwer']}% | {row['total_sec']}s"
                  + (f" | recall {recall:.0%}" if recall is not None else ""))
            rows.append(row)
        await registry.close()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "config": {
            "name": config_name,
            "engine": engine.name,
            "asr": engine.asr.name,
            "llms": [llm.name for llm in llms],
            "judge": judge.llm.name if judge else None,
            "asr_cached": not args.fresh_asr,
            "git_commit": git_commit(),
            "date": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        },
        "rows": rows,
    }
    out = RESULTS_DIR / f"{config_name}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Saved {out.relative_to(ROOT)}")
    print(f"Report: {write_report().relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", choices=["cloud", "private"], default="cloud")
    parser.add_argument("--llm", action="append", help="override the engine's LLMs, e.g. gemini:gemini-2.5-flash")
    parser.add_argument("--judge", default="groq:openai/gpt-oss-120b", help="judge model (another vendor is best)")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--source", choices=["all", "human", "tts"], default="all")
    parser.add_argument("--id", action="append", help="only these clip ids")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--fresh-asr", action="store_true", help="ignore cached transcripts (re-times speech-to-text)")
    parser.add_argument("--name", help="name for this configuration in the report")
    parser.add_argument("--llm-model", help="private engine: GGUF model path")
    parser.add_argument("--llama-bin", default=str(ROOT / "ml" / ("llama-server.exe" if os.name == "nt" else "llama-server")))
    parser.add_argument("--asr-backend", help="private engine: faster-whisper or onnx")
    parser.add_argument("--asr-model", help="private engine: model size or path")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()

    if args.report_only:
        print(f"Report: {write_report().relative_to(ROOT)}")
        return
    if args.engine == "private":
        os.environ["LLAMA_SERVER_BIN"] = args.llama_bin
        if args.llm_model:
            os.environ["PRIVATE_LLM_MODEL_PATH"] = str(Path(args.llm_model).resolve())
        if args.asr_backend:
            os.environ["PRIVATE_ASR_BACKEND"] = args.asr_backend
        if args.asr_model:
            os.environ["PRIVATE_ASR_MODEL"] = args.asr_model
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

# Evaluation

`scripts/run_eval_suite.py` runs every clip in `test_manifest.json` through an engine and writes
`results/<configuration>.json`; [`benchmark_report.md`](benchmark_report.md) is regenerated from all saved runs.

## What is measured

| Metric | How |
|---|---|
| **WER / CER** | Word/character error rate of the final Hinglish transcript against a human reference. Lowercased, punctuation removed. The strict metric. |
| **hWER** | WER after Hinglish normalization: digits → words, split compounds joined ("front end"), common spelling variants unified ("toh"/"to", "nahin"/"nahi"), doubled vowels collapsed. Forgives spelling conventions, not recognition errors. Always quote it next to WER. |
| **Action recall** | Share of the human-written expected action items that the output covers, decided by an LLM judge. |
| **Action precision** | Share of generated action items the transcript actually supports (the rest are invented). |
| **Unsupported claims** | Share of summaries in which the judge found a claim the true transcript doesn't support. |
| **Latency / RTF** | Wall-clock processing time from the evaluation machine; real-time factor = processing time ÷ audio length. |

The judge (default `groq:qwen/qwen3.8-27b`) is deliberately from a different model family than every model being
compared, sees the human reference transcript (so meaning-changing speech errors are penalized), and its verdicts are
cached in `.cache/` so re-running a report costs nothing. Earlier versions of this project matched action items by
keyword overlap, which over-counted; numbers from before v2 aren't comparable.

## The clips are mostly synthetic. Read results accordingly

| Source | Clips | What it is |
|---|---|---|
| `human` | 1 (2.9 min) | A real recording of natural Hinglish speech by the author. |
| `tts` | 14 | Google TTS (Hindi voice) reading the reference text. Clean, single speaker, no noise: optimistic. |

Synthetic clips are useful for *comparing configurations* (models, prompts) on identical input. They are not a
real-world accuracy number. The report keeps the two groups in separate tables.

**The most valuable improvement to this evaluation is more real recordings.** Different speakers, accents, phone
microphones, background noise, voice notes forwarded from WhatsApp.

## Adding a real recording

1. Record or collect a voice note (with the speaker's permission) and save it as `evaluation/audio/<id>.<ext>`.
   Audio files are git-ignored; keep them out of the repo if they're personal.
2. Write the reference transcript **by hand**, in casual Romanized Hinglish, exactly as spoken. Don't paste the
   system's output and correct it lightly: that biases the reference toward the system.
3. List the action items a careful human would extract.
4. Add an entry to `test_manifest.json`:
   ```json
   {
     "id": "rec_03_family_plans",
     "title": "Weekend family plans",
     "category": "Personal",
     "source": "human",
     "speaker": "friend, Delhi accent, phone mic",
     "duration_category": "Short (<30s)",
     "audio_file": "evaluation/audio/rec_03_family_plans.m4a",
     "reference_transcript": "…",
     "expected_actions": ["Book the train tickets for Saturday", "…"]
   }
   ```

## Running

```bash
python scripts/run_eval_suite.py                                   # default chain, all clips
python scripts/run_eval_suite.py --source human                    # real recordings only
python scripts/run_eval_suite.py --llm gemini:gemini-3.5-flash     # one specific model
python scripts/run_eval_suite.py --llm groq:openai/gpt-oss-120b --resume   # finish a run that hit rate limits
python scripts/run_eval_suite.py --engine private --llm-model models/<model>.gguf
python scripts/run_eval_suite.py --report-only
```

Free tiers allow only a few requests per minute per model, so long runs pause on rate limits. `--resume` keeps
clips that already succeeded. Speech-to-text output is cached per clip (`.cache/asr`); pass `--fresh-asr` to
re-transcribe and re-time it.

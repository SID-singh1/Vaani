import os
import sys
import time
import json
import argparse
import asyncio
from datetime import datetime
import dotenv
import jiwer

# Ensure project root is in sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BASE_DIR)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

dotenv.load_dotenv(os.path.join(BASE_DIR, ".env"))

from backend.services.asr_service import transcribe_audio, _get_audio_duration
from backend.services.llm_service import summarize_transcript

MANIFEST_PATH = os.path.join(BASE_DIR, "evaluation", "test_manifest.json")
REPORT_MD_PATH = os.path.join(BASE_DIR, "evaluation", "benchmark_report.md")
REPORT_JSON_PATH = os.path.join(BASE_DIR, "evaluation", "latest_results.json")

# Standard normalization for ASR evaluation (lowercasing, punctuation stripping)
eval_transform = jiwer.Compose([
    jiwer.ToLowerCase(),
    jiwer.RemovePunctuation(),
    jiwer.RemoveMultipleSpaces(),
    jiwer.Strip()
])

def normalize_text(text: str) -> str:
    if not text:
        return ""
    return eval_transform(text)

async def evaluate_single_test(test_item: dict) -> dict:
    test_id = test_item["id"]
    audio_path = os.path.join(BASE_DIR, test_item["audio_file"])
    reference = test_item["reference_transcript"]
    expected_actions = test_item.get("expected_actions", [])
    
    if not os.path.exists(audio_path):
        return {
            "id": test_id,
            "title": test_item["title"],
            "category": test_item["category"],
            "error": f"Audio file not found: {audio_path}",
            "skipped": True
        }

    duration = _get_audio_duration(audio_path)
    
    # 1. Measure ASR Latency & Output
    t0 = time.time()
    raw_transcript = await transcribe_audio(audio_path)
    asr_latency = time.time() - t0
    
    # 2. Run LLM Summarization & Transliteration
    t1 = time.time()
    llm_result = await summarize_transcript(raw_transcript)
    llm_latency = time.time() - t1
    
    hyp_transcript = llm_result.get("transcript") or raw_transcript
    summary = llm_result.get("summary", "")
    actions = llm_result.get("action_items", [])
    sentiment = llm_result.get("sentiment", "Neutral")
    
    # 3. Calculate Speech Accuracy (WER & CER)
    norm_ref = normalize_text(reference)
    norm_hyp = normalize_text(hyp_transcript)
    
    wer = jiwer.wer(norm_ref, norm_hyp)
    cer = jiwer.cer(norm_ref, norm_hyp)
    
    # 4. Action Item Recall (simple keyword overlap check)
    actions_found = 0
    actions_combined = " ".join(actions).lower()
    for exp in expected_actions:
        # Check if key words from expected action appear in generated actions
        keywords = [w.lower() for w in exp.split() if len(w) > 3]
        if any(kw in actions_combined for kw in keywords):
            actions_found += 1
            
    action_recall = (actions_found / len(expected_actions)) if expected_actions else 1.0

    return {
        "id": test_id,
        "title": test_item["title"],
        "category": test_item["category"],
        "duration_sec": round(duration, 1),
        "asr_latency_sec": round(asr_latency, 2),
        "llm_latency_sec": round(llm_latency, 2),
        "total_latency_sec": round(asr_latency + llm_latency, 2),
        "wer": round(wer * 100, 2),
        "cer": round(cer * 100, 2),
        "actions_found": actions_found,
        "actions_total": len(expected_actions),
        "action_recall_pct": round(action_recall * 100, 1),
        "sentiment": sentiment,
        "hyp_transcript": hyp_transcript,
        "summary": summary,
        "action_items": actions,
        "skipped": False
    }

async def run_suite(limit: int = None, target_id: str = None):
    print("=" * 80)
    print("🚀 VAANI AUTOMATED EVALUATION & BENCHMARK SUITE")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    if not os.path.exists(MANIFEST_PATH):
        print(f"❌ Error: Test manifest not found at {MANIFEST_PATH}")
        return

    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        tests = json.load(f)

    if target_id:
        tests = [t for t in tests if t["id"] == target_id]
        if not tests:
            print(f"❌ No test found with ID '{target_id}'")
            return
    elif limit:
        tests = tests[:limit]

    print(f"Running {len(tests)} evaluation tests...\n")
    
    results = []
    for idx, t in enumerate(tests, 1):
        print(f"[{idx}/{len(tests)}] Evaluating: {t['title']} ({t['id']})...", end=" ", flush=True)
        res = await evaluate_single_test(t)
        if res.get("skipped"):
            print(f"SKIPPED ({res.get('error')})")
        else:
            print(f"DONE (WER: {res['wer']}%, CER: {res['cer']}%, Latency: {res['asr_latency_sec']}s)")
        results.append(res)

    valid_results = [r for r in results if not r.get("skipped")]
    if not valid_results:
        print("\n❌ No successful test evaluations to report.")
        return

    # Aggregate Statistics
    avg_wer = sum(r["wer"] for r in valid_results) / len(valid_results)
    avg_cer = sum(r["cer"] for r in valid_results) / len(valid_results)
    avg_asr_latency = sum(r["asr_latency_sec"] for r in valid_results) / len(valid_results)
    avg_action_recall = sum(r["action_recall_pct"] for r in valid_results) / len(valid_results)

    # Print Formatted Markdown Table
    print("\n" + "=" * 80)
    print("📊 BENCHMARK SUMMARY TABLE")
    print("=" * 80)
    header = f"| {'Test ID':<28} | {'Category':<18} | {'Dur (s)':<7} | {'ASR (s)':<7} | {'WER (%)':<7} | {'CER (%)':<7} | {'Actions':<8} |"
    sep = f"|:{'-'*28}-|-{'-'*18}-|-{'-'*7}:|-{'-'*7}:|-{'-'*7}:|-{'-'*7}:|-{'-'*8}:|"
    print(header)
    print(sep)
    for r in valid_results:
        act_str = f"{r['actions_found']}/{r['actions_total']}"
        print(f"| {r['id']:<28} | {r['category']:<18} | {r['duration_sec']:<7} | {r['asr_latency_sec']:<7} | {r['wer']:<7} | {r['cer']:<7} | {act_str:<8} |")

    print("-" * 80)
    print(f"✨ OVERALL BENCHMARK RESULTS (N = {len(valid_results)}):")
    print(f"  • Average Word Error Rate (WER) : {avg_wer:.2f}%")
    print(f"  • Average Character Error Rate (CER) : {avg_cer:.2f}%")
    print(f"  • Average ASR Latency : {avg_asr_latency:.2f}s")
    print(f"  • Action Item Extraction Recall : {avg_action_recall:.1f}%")
    print("=" * 80)

    # Save to JSON
    summary_payload = {
        "timestamp": datetime.now().isoformat(),
        "total_tests": len(valid_results),
        "overall_metrics": {
            "average_wer_pct": round(avg_wer, 2),
            "average_cer_pct": round(avg_cer, 2),
            "average_asr_latency_sec": round(avg_asr_latency, 2),
            "average_action_recall_pct": round(avg_action_recall, 1)
        },
        "results": valid_results
    }
    with open(REPORT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(summary_payload, f, ensure_ascii=False, indent=2)

    # Save to Markdown Report
    with open(REPORT_MD_PATH, "w", encoding="utf-8") as f:
        f.write("# Vaani Evaluation & Accuracy Benchmark Report\n\n")
        f.write(f"*Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
        f.write("### Overall Metrics\n")
        f.write(f"- **Total Tests Evaluated:** {len(valid_results)}\n")
        f.write(f"- **Mean Word Error Rate (WER):** {avg_wer:.2f}%\n")
        f.write(f"- **Mean Character Error Rate (CER):** {avg_cer:.2f}%\n")
        f.write(f"- **Mean ASR Latency:** {avg_asr_latency:.2f}s\n")
        f.write(f"- **Action Item Recall:** {avg_action_recall:.1f}%\n\n")
        f.write("### Detailed Per-Test Breakdown\n\n")
        f.write(header + "\n")
        f.write(sep + "\n")
        for r in valid_results:
            act_str = f"{r['actions_found']}/{r['actions_total']}"
            f.write(f"| {r['id']} | {r['category']} | {r['duration_sec']} | {r['asr_latency_sec']} | {r['wer']}% | {r['cer']}% | {act_str} |\n")
        f.write("\n")

    print(f"\n📁 Report saved to:")
    print(f"  • {REPORT_MD_PATH}")
    print(f"  • {REPORT_JSON_PATH}\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Vaani Speech & Intelligence Evaluation Suite")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of tests to run")
    parser.add_argument("--id", type=str, default=None, help="Run a specific test by ID")
    args = parser.parse_args()

    asyncio.run(run_suite(limit=args.limit, target_id=args.id))

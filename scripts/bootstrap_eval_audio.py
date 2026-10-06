import os
import json
import subprocess
import shutil
from gtts import gTTS

MANIFEST_PATH = "evaluation/test_manifest.json"
AUDIO_DIR = "evaluation/audio"

def bootstrap_audio():
    os.makedirs(AUDIO_DIR, exist_ok=True)
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        tests = json.load(f)

    print(f"Checking {len(tests)} evaluation test cases...")
    for item in tests:
        audio_path = item["audio_file"]
        if os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000:
            print(f"  [EXISTS] {item['id']}: {audio_path}")
            continue

        # If test_02 and we already have the clean 2.5 min audio, copy it directly
        if item["id"] == "test_02_team_sync_long" and os.path.exists("scratch/full_user_clean.wav"):
            shutil.copy("scratch/full_user_clean.wav", audio_path)
            print(f"  [COPIED] {item['id']} from scratch/full_user_clean.wav")
            continue

        print(f"  [GENERATING] {item['id']} via gTTS...")
        temp_mp3 = audio_path + ".temp.mp3"
        try:
            tts = gTTS(text=item["reference_transcript"], lang="hi")
            tts.save(temp_mp3)

            # Convert to clean 16kHz mono WAV
            subprocess.run([
                "ffmpeg", "-y", "-fflags", "+genpts", "-i", temp_mp3,
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", audio_path
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

            print(f"    -> Saved: {audio_path} ({os.path.getsize(audio_path)/(1024):.1f} KB)")
        finally:
            if os.path.exists(temp_mp3):
                os.remove(temp_mp3)

    print("\n✅ All 15 audio test files are ready in evaluation/audio/!")

if __name__ == "__main__":
    bootstrap_audio()

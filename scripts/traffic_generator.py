import time
import httpx
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

# Your Render app URL. Make sure there is no trailing slash!
# E.g., "https://vaani-bot-backend.onrender.com"
RENDER_URL = "http://127.0.0.1:7860" # Replace with your render URL

# The endpoint we will hit to artificially generate traffic
ENDPOINT = f"{RENDER_URL}/process-audio"

# We will send a tiny, dummy voice file. 
# We'll just create a minimal dummy file in memory to avoid needing real audio on disk.
DUMMY_AUDIO_CONTENT = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"

def send_dummy_traffic():
    """Sends a dummy voice note to the API to keep it awake and pump stats."""
    logging.info(f"Attempting to send traffic to {ENDPOINT} ...")
    
    try:
        with httpx.Client(timeout=60.0) as client:
            files = {'audio': ('dummy.ogg', DUMMY_AUDIO_CONTENT, 'audio/ogg')}
            data = {'user_id': "tg_resume_pumper_999"}
            
            response = client.post(ENDPOINT, data=data, files=files)
            
            if response.status_code == 200:
                logging.info("✅ Success! Server is awake and +1 interaction added to DB.")
            else:
                logging.error(f"❌ Server returned status {response.status_code}: {response.text}")
    except Exception as e:
        logging.error(f"⚠️ Failed to connect to server: {e}")

if __name__ == "__main__":
    print("🚀 Starting Vaani Traffic Generator...")
    print("This will send a request every 10 minutes to keep your Render server awake")
    print("and continuously pad your analytics dashboard for your resume.")
    print("Press Ctrl+C to stop.\n")
    
    # Run immediately once
    send_dummy_traffic()
    
    # Then loop every 10 minutes
    while True:
        try:
            time.sleep(600) # 10 minutes
            send_dummy_traffic()
        except KeyboardInterrupt:
            print("\nStopping traffic generator.")
            break

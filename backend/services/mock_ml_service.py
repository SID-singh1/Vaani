import asyncio

async def mock_transcribe_audio(file_path: str) -> str:
    # Simulate CPU processing time
    await asyncio.sleep(2)
    return "Meeting kal 10 baje hai, please prepare the slides."

async def mock_summarize_transcript(transcript: str) -> dict:
    # Simulate LLM processing time
    await asyncio.sleep(2)
    return {
        "summary": "Meeting scheduled for tomorrow at 10 AM.",
        "action_items": ["Prepare slides for the 10 AM meeting."],
        "sentiment": "neutral"
    }

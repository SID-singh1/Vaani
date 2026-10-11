"""The real Telegram handlers, driven offline through a fake Bot API."""

import json
import time

import pytest
from telegram import Update
from telegram.request import BaseRequest

from conftest import FakeASR, FakeLLM, fake_registry, make_wav, requires_ffmpeg
from vaani.channels.common import NoteView, format_note_html, split_text
from vaani.channels.telegram import TelegramChannel

CHAT = {"id": 555, "type": "private"}
USER = {"id": 555, "is_bot": False, "first_name": "Sid"}


class FakeBotAPI(BaseRequest):
    def __init__(self, media: bytes = b""):
        self.calls: list[tuple[str, dict]] = []
        self.media = media
        self._next_id = 100

    @property
    def read_timeout(self):
        return 5

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    def _message(self, params: dict) -> dict:
        self._next_id += 1
        return {"message_id": self._next_id, "date": int(time.time()), "chat": CHAT, "text": params.get("text", "")}

    async def do_request(self, url, method, request_data=None, **_):
        if "/file/bot" in url:
            return 200, self.media
        name = url.rsplit("/", 1)[-1]
        params = request_data.parameters if request_data else {}
        self.calls.append((name, params))
        result = {
            "getMe": {"id": 1, "is_bot": True, "first_name": "Vaani", "username": "vaani_test_bot"},
            "sendMessage": self._message(params),
            "editMessageText": self._message(params),
            "editMessageReplyMarkup": self._message(params),
            "getFile": {
                "file_id": "f1",
                "file_unique_id": "u1",
                "file_size": len(self.media),
                "file_path": "voice/file_1.oga",
            },
        }.get(name, True)
        return 200, json.dumps({"ok": True, "result": result}).encode()

    def sent(self, method="sendMessage") -> list[dict]:
        return [params for name, params in self.calls if name == method]


@pytest.fixture
def bot(make_client):
    channels = []

    def _make(registry=None, media=b"", **overrides):
        client = make_client(registry, telegram_bot_token="123:TEST", **overrides)
        fake = FakeBotAPI(media)
        channel = TelegramChannel(client.app.state.ctx, request=fake)
        client.portal.call(channel.app.initialize)
        channels.append((client, channel))

        def send(payload: dict):
            update = Update.de_json({"update_id": int(time.time() * 1000) % 10**9, **payload}, channel.app.bot)
            client.portal.call(channel.app.process_update, update)

        return client, fake, send

    yield _make
    for client, channel in channels:
        client.portal.call(channel.app.shutdown)


def text_message(text: str, message_id: int = 10) -> dict:
    return {"message": {"message_id": message_id, "date": int(time.time()), "chat": CHAT, "from": USER, "text": text}}


def command(text: str, message_id: int = 10) -> dict:
    payload = text_message(text, message_id)
    payload["message"]["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    return payload


def callback(data: str) -> dict:
    return {
        "callback_query": {
            "id": "cq1",
            "from": USER,
            "chat_instance": "ci",
            "data": data,
            "message": {"message_id": 99, "date": int(time.time()), "chat": CHAT, "text": "x"},
        }
    }


@requires_ffmpeg
def test_voice_note_flow(bot, tmp_path):
    client, api, send = bot(
        fake_registry(asr=FakeASR("Kal client demo hai.")), media=make_wav(tmp_path / "v.wav", seconds=1).read_bytes()
    )
    send(
        {
            "message": {
                "message_id": 11,
                "date": int(time.time()),
                "chat": CHAT,
                "from": USER,
                "voice": {
                    "file_id": "f1",
                    "file_unique_id": "u1",
                    "duration": 1,
                    "mime_type": "audio/ogg",
                    "file_size": 2000,
                },
            }
        }
    )
    messages = api.sent()
    assert messages[0]["text"].startswith("⏳")  # status message
    result = messages[-1]
    assert result["parse_mode"] == "HTML"
    assert "<b>📝 Team meeting tomorrow</b>" in result["text"]
    assert "Prepare the slides" in result["text"] and "👤 Rahul" in result["text"]
    keyboard = json.dumps(result["reply_markup"])
    assert '"tr:' in keyboard and '"rate:up:' in keyboard
    assert api.sent("deleteMessage")  # status message removed

    # Same update delivered again (webhook retry): no second processing.
    before = len(api.sent())
    send(
        {
            "message": {
                "message_id": 11,
                "date": int(time.time()),
                "chat": CHAT,
                "from": USER,
                "voice": {"file_id": "f1", "file_unique_id": "u1", "duration": 1, "file_size": 2000},
            }
        }
    )
    assert len(api.sent()) == before + 1  # just re-sends the finished result

    note_id = keyboard.split('"tr:')[1].split('"')[0]
    send(callback(f"tr:{note_id}"))
    assert "Kal client demo hai." in api.sent()[-1]["text"]

    send(callback(f"rate:up:{note_id}"))
    assert api.sent("answerCallbackQuery")[-1]["text"].startswith("Thanks")


def test_model_output_is_html_escaped(bot):
    nasty = {
        "title": "<b>bold</b> & co",
        "summary": "Use <script>alert(1)</script> *here*",
        "action_items": [{"task": "Fix <i>it</i>", "owner": None, "due": None}],
        "sentiment": "Negative",
    }
    client, api, send = bot(fake_registry(llms=[FakeLLM(analysis=nasty)]))
    send(text_message("Yeh ek lamba message hai jisme kaafi saare words hain bhai log."))
    result = api.sent()[-1]["text"]
    assert "&lt;script&gt;" in result and "<script>" not in result
    assert "&lt;b&gt;bold&lt;/b&gt; &amp; co" in result


def test_short_text_gets_guidance(bot):
    client, api, send = bot()
    send(text_message("hello"))
    assert "voice note" in api.sent()[-1]["text"]


def test_quota_error_is_reported(bot):
    client, api, send = bot(user_daily_note_limit=1)
    long_text = "Yeh ek lamba message hai jisme kaafi saare words hain bhai log."
    send(text_message(long_text, 20))
    send(text_message(long_text, 21))
    assert "free notes for today" in api.sent()[-1]["text"]


def test_delete_flow(bot):
    client, api, send = bot()
    send(text_message("Yeh ek lamba message hai jisme kaafi saare words hain bhai log.", 30))
    send(command("/delete", 31))
    assert "Which notes should I delete" in api.sent()[-1]["text"]
    send(text_message("1", 32))
    assert "Permanently delete" in api.sent()[-1]["text"]
    send(callback("del:yes"))
    assert "Deleted 1 note" in api.sent("editMessageText")[-1]["text"]
    send(command("/history", 33))
    assert "No notes yet" in api.sent()[-1]["text"]


def test_mode_command_with_one_engine(bot):
    client, api, send = bot()
    send(command("/mode"))
    assert "Fast</b> mode only" in api.sent()[-1]["text"]


def test_mode_switch(bot):
    client, api, send = bot(fake_registry(private=True))
    send(command("/mode"))
    assert "callback_data" in json.dumps(api.sent()[-1]["reply_markup"])
    send(callback("mode:private"))
    assert "Private mode" in api.sent()[-1]["text"]
    send(text_message("Yeh ek lamba message hai jisme kaafi saare words hain bhai log.", 40))
    from vaani.db import repo

    ctx = client.app.state.ctx
    notes = client.portal.call(ctx.db.run, repo.list_notes, "tg_555", 5)
    assert notes[0].engine == "private"


def test_webhook_requires_secret(make_client):
    client = make_client()

    class StubChannel:
        mode = "webhook"
        webhook_secret = "s3cret"
        received = []

        async def feed_webhook(self, payload):
            self.received.append(payload)

        async def stop(self):
            pass

    client.app.state.ctx.telegram = StubChannel()
    assert client.post("/telegram/webhook", json={"update_id": 1}).status_code == 401
    ok = client.post("/telegram/webhook", json={"update_id": 1}, headers={"x-telegram-bot-api-secret-token": "s3cret"})
    assert ok.status_code == 200 and StubChannel.received == [{"update_id": 1}]


def test_long_results_are_split_on_line_boundaries():
    view = NoteView(id="n", title="T", summary="S " * 3000, action_items=[], sentiment="Neutral", transcript="")
    chunks = split_text(format_note_html(view), 4000)
    assert len(chunks) > 1 and all(len(c) <= 4000 for c in chunks)


def test_transcript_button_disappears_after_use_and_history_reopens_notes(bot):
    me_owner = {
        "title": "Plan",
        "summary": "We plan the launch.",
        "sentiment": "Neutral",
        "action_items": [{"task": "Send the deck", "owner": "Me", "due": None}],
    }
    client, api, send = bot(fake_registry(llms=[FakeLLM(analysis=me_owner)]))
    send(text_message("Yeh ek lamba message hai jisme kaafi saare words hain bhai log.", 50))
    result = api.sent()[-1]
    assert "👤 You" in result["text"] and "⏱ ready in" in result["text"]
    note_id = json.dumps(result["reply_markup"]).split('"tr:')[1].split('"')[0]

    payload = callback(f"tr:{note_id}")
    payload["callback_query"]["message"]["reply_markup"] = result["reply_markup"]
    send(payload)
    edited = api.sent("editMessageReplyMarkup")[-1]["reply_markup"]
    assert "tr:" not in json.dumps(edited) and "rate:up:" in json.dumps(edited)

    send(command("/history", 51))
    history = api.sent()[-1]
    assert "We plan the launch." in history["text"] and f"open:{note_id}" in json.dumps(history["reply_markup"])
    send(callback(f"open:{note_id}"))
    assert "<b>📝 Plan</b>" in api.sent()[-1]["text"]

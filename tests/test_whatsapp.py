import hashlib
import hmac
import json
import time

import httpx
import pytest

from conftest import FakeASR, fake_registry, make_wav, requires_ffmpeg
from vaani.channels.whatsapp import DailyCap, verify_signature

APP_SECRET = "app-secret"
WA_SETTINGS = dict(
    whatsapp_access_token="wa-token",
    whatsapp_phone_number_id="12345",
    whatsapp_app_secret=APP_SECRET,
    whatsapp_verify_token="verify-me",
    whatsapp_daily_message_cap=0,
)


class FakeGraph:
    """Stands in for graph.facebook.com and the media CDN."""

    def __init__(self, media: bytes = b""):
        self.sent: list[dict] = []
        self.statuses: list[dict] = []
        self.media = media

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer wa-token"
        url = str(request.url)
        if request.method == "POST" and url.endswith("/12345/messages"):
            body = json.loads(request.content)
            (self.statuses if body.get("status") == "read" else self.sent).append(body)
            return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})
        if request.method == "GET" and url.endswith("/media-1"):
            return httpx.Response(
                200, json={"url": "https://cdn.example/media-1", "mime_type": "audio/ogg", "file_size": len(self.media)}
            )
        if request.method == "GET" and url == "https://cdn.example/media-1":
            return httpx.Response(200, content=self.media)
        return httpx.Response(404)


def signed(client, payload: dict):
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/whatsapp/webhook", content=raw, headers={"x-hub-signature-256": sig, "content-type": "application/json"}
    )


def message_event(message: dict) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "messages", "value": {"messages": [message]}}]}],
    }


def wait_until(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


@pytest.fixture
def wa(make_client):
    def _make(graph: FakeGraph, **overrides):
        client = make_client(
            fake_registry(asr=FakeASR("Kal client demo hai, deck ready rakhna.")), **{**WA_SETTINGS, **overrides}
        )
        client.app.state.ctx.http = httpx.AsyncClient(transport=httpx.MockTransport(graph.handler))
        return client

    return _make


def test_signature_verification():
    raw = b'{"a":1}'
    good = "sha256=" + hmac.new(b"s", raw, hashlib.sha256).hexdigest()
    assert verify_signature("s", raw, good)
    assert not verify_signature("s", raw + b" ", good)
    assert not verify_signature("s", raw, None)
    assert not verify_signature("s", raw, good.replace("sha256=", ""))


def test_verification_handshake(wa):
    client = wa(FakeGraph())
    ok = client.get(
        "/whatsapp/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "1158201444"},
    )
    assert ok.status_code == 200 and ok.text == "1158201444"
    bad = client.get(
        "/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"}
    )
    assert bad.status_code == 401


def test_rejects_unsigned_events(wa):
    client = wa(FakeGraph())
    assert client.post("/whatsapp/webhook", json=message_event({})).status_code == 401


def test_disabled_without_config(client):
    assert client.get("/whatsapp/webhook").status_code == 404


@requires_ffmpeg
def test_voice_note_end_to_end(wa, tmp_path):
    graph = FakeGraph(media=make_wav(tmp_path / "v.wav", seconds=1).read_bytes())
    client = wa(graph)
    event = message_event(
        {
            "from": "919876543210",
            "id": "wamid.in1",
            "type": "audio",
            "audio": {"id": "media-1", "mime_type": "audio/ogg; codecs=opus", "voice": True},
        }
    )
    assert signed(client, event).status_code == 200
    assert signed(client, event).status_code == 200  # Meta redelivery: must not process twice

    wait_until(lambda: graph.sent)
    time.sleep(0.2)
    assert len(graph.sent) == 1
    reply = graph.sent[0]
    assert reply["to"] == "919876543210" and reply["type"] == "interactive"
    assert "Team meeting tomorrow" in reply["interactive"]["body"]["text"]
    button_ids = [b["reply"]["id"] for b in reply["interactive"]["action"]["buttons"]]
    assert button_ids[0].startswith("tr:") and button_ids[1].startswith("rate:up:")
    assert graph.statuses and graph.statuses[0]["typing_indicator"] == {"type": "text"}

    # The user is stored under a pseudonym, never the phone number.
    from vaani.db import repo

    ctx = client.app.state.ctx
    note = client.portal.call(ctx.db.run, repo.get_note, button_ids[0].removeprefix("tr:"))
    assert note.user_id.startswith("wa_") and "9876543210" not in note.user_id
    assert note.channel == "whatsapp"

    # Tapping "Transcript" sends the transcript.
    tap = {
        "from": "919876543210",
        "id": "wamid.in2",
        "type": "interactive",
        "interactive": {"type": "button_reply", "button_reply": {"id": button_ids[0], "title": "Transcript"}},
    }
    signed(client, message_event(tap))
    wait_until(lambda: len(graph.sent) == 2)
    assert "Kal client demo hai" in graph.sent[1]["text"]["body"]


def test_short_text_gets_help_and_commands_work(wa):
    graph = FakeGraph()
    client = wa(graph)
    signed(client, message_event({"from": "911", "id": "w1", "type": "text", "text": {"body": "hi"}}))
    wait_until(lambda: graph.sent)
    assert "I'm Vaani" in graph.sent[0]["text"]["body"]

    signed(client, message_event({"from": "911", "id": "w2", "type": "text", "text": {"body": "delete all"}}))
    wait_until(lambda: len(graph.sent) == 2)
    assert graph.sent[1]["type"] == "interactive"


def test_long_text_is_summarized(wa):
    graph = FakeGraph()
    client = wa(graph)
    body = "Kal team ke saath sync hai, Rahul ko deck ready rakhna hai aur Priya recordings bhejegi."
    signed(client, message_event({"from": "912", "id": "w3", "type": "text", "text": {"body": body}}))
    wait_until(lambda: graph.sent)
    assert graph.sent[0]["type"] == "interactive"


def test_daily_cap_stops_sending(wa):
    graph = FakeGraph()
    client = wa(graph, whatsapp_daily_message_cap=2)
    for i in range(4):
        signed(client, message_event({"from": "913", "id": f"c{i}", "type": "text", "text": {"body": "help"}}))
    wait_until(lambda: len(graph.sent) == 2)
    time.sleep(0.3)
    assert len(graph.sent) == 2
    assert "today's free limit" in graph.sent[-1]["text"]["body"]


def test_daily_cap_resets():
    cap = DailyCap(1)
    assert cap.take() and not cap.take()
    cap.day = None  # a new day
    assert cap.take()

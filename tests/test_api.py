"""HTTP API: sessions, isolation between users, uploads, quotas, admin."""

from dataclasses import replace

from conftest import FakeASR, auth_headers, fake_registry, make_wav, requires_ffmpeg, wait_for_note

TEXT = "Kal subah team meeting hai, Rahul please slides ready rakhna."


def submit_text(client, headers, text=TEXT):
    return client.post("/api/v1/notes", data={"text": text}, headers=headers)


def test_health_and_pages(client):
    assert client.get("/health").json()["status"] == "ok"
    page = client.get("/")
    assert page.status_code == 200
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["x-frame-options"] == "DENY"
    assert client.get("/privacy").status_code == 200
    assert client.head("/health").status_code == 200


def test_requires_a_valid_session(client):
    assert client.get("/api/v1/notes").status_code == 401
    bad = client.get("/api/v1/notes", headers={"Authorization": "Bearer v1.web_abcdefghijk.forged"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "unauthorized"


def test_text_note_lifecycle(client):
    headers = auth_headers(client)
    created = submit_text(client, headers)
    assert created.status_code == 202
    assert created.json()["status"] == "queued"

    note = wait_for_note(client, headers, created.json()["id"])
    assert note["status"] == "done"
    assert note["title"] == "Team meeting tomorrow"
    assert note["action_items"] == [{"task": "Prepare the slides", "owner": "Rahul", "due": "tomorrow morning"}]
    assert note["transcript"] == TEXT
    assert note["timings"]["total_ms"] is not None

    listed = client.get("/api/v1/notes", headers=headers).json()["notes"]
    assert [n["id"] for n in listed] == [note["id"]]

    assert client.post(f"/api/v1/notes/{note['id']}/rating", json={"rating": "up"}, headers=headers).status_code == 200
    assert client.get(f"/api/v1/notes/{note['id']}", headers=headers).json()["rating"] == "up"

    assert client.delete(f"/api/v1/notes/{note['id']}", headers=headers).json() == {"deleted": 1}
    assert client.get(f"/api/v1/notes/{note['id']}", headers=headers).status_code == 404


def test_users_cannot_see_each_others_notes(client):
    alice, bob = auth_headers(client), auth_headers(client)
    note_id = submit_text(client, alice).json()["id"]
    wait_for_note(client, alice, note_id)

    assert client.get(f"/api/v1/notes/{note_id}", headers=bob).status_code == 404
    assert client.get("/api/v1/notes", headers=bob).json()["notes"] == []
    assert client.delete(f"/api/v1/notes/{note_id}", headers=bob).status_code == 404
    assert client.post(f"/api/v1/notes/{note_id}/rating", json={"rating": "down"}, headers=bob).status_code == 404
    assert client.delete("/api/v1/notes", headers=bob).json() == {"deleted": 0}
    assert client.get(f"/api/v1/notes/{note_id}", headers=alice).status_code == 200


def test_legacy_web_id_can_be_claimed_once(client):
    # A pre-v2 user with history, identified only by a localStorage id.
    old = auth_headers(client, legacy_user_id="user_ab12cd34e")  # first claim: user doesn't exist yet
    assert client.get("/api/v1/me", headers=old).json()["user_id"] != "user_ab12cd34e"

    from vaani.db import repo

    ctx = client.app.state.ctx
    client.portal.call(ctx.db.run, repo.get_or_create_user, "user_zz98yy76x")
    first = client.post("/api/v1/session", json={"legacy_user_id": "user_zz98yy76x"}).json()
    assert first["migrated"] and first["user_id"] == "user_zz98yy76x"
    second = client.post("/api/v1/session", json={"legacy_user_id": "user_zz98yy76x"}).json()
    assert not second["migrated"] and second["user_id"] != "user_zz98yy76x"


def test_validation_errors_are_friendly(client):
    headers = auth_headers(client)
    empty = client.post("/api/v1/notes", data={"text": "   "}, headers=headers)
    assert empty.status_code == 400 and "audio file or some text" in empty.json()["error"]["message"]
    bad_rating = client.post("/api/v1/notes/x/rating", json={"rating": "meh"}, headers=headers)
    assert bad_rating.status_code == 400


def test_unsupported_upload_type(client):
    headers = auth_headers(client)
    files = {"audio": ("notes.pdf", b"%PDF-1.4", "application/pdf")}
    res = client.post("/api/v1/notes", files=files, headers=headers)
    assert res.status_code == 415


def test_oversized_upload_is_rejected(make_client):
    client = make_client(max_upload_mb=1)
    headers = auth_headers(client)
    files = {"audio": ("big.mp3", b"\0" * (3 * 1024 * 1024), "audio/mpeg")}
    res = client.post("/api/v1/notes", files=files, headers=headers)
    assert res.status_code == 413


@requires_ffmpeg
def test_audio_upload_end_to_end(make_client, tmp_path):
    asr = FakeASR("Kal client demo hai, payment gateway bug fix kar dena.")
    client = make_client(fake_registry(asr=asr))
    headers = auth_headers(client)
    wav = make_wav(tmp_path / "note.wav", seconds=2)
    with wav.open("rb") as fh:
        created = client.post("/api/v1/notes", files={"audio": ("note.wav", fh, "audio/wav")}, headers=headers)
    assert created.status_code == 202
    note = wait_for_note(client, headers, created.json()["id"])
    assert note["status"] == "done" and note["source"] == "audio"
    assert note["audio_duration_sec"] == 2.0
    assert asr.calls == 1
    temp = client.app.state.ctx.settings.temp_dir
    assert list(temp.iterdir()) == []  # uploaded audio deleted after processing


@requires_ffmpeg
def test_audio_over_engine_limit_fails_cleanly(make_client, tmp_path):
    def registry(settings, http):
        reg = fake_registry()(settings, http)
        reg.get("cloud").max_audio_seconds = 1
        return reg

    client = make_client(registry)
    headers = auth_headers(client)
    with make_wav(tmp_path / "long.wav", seconds=3).open("rb") as fh:
        note_id = client.post("/api/v1/notes", files={"audio": ("long.wav", fh, "audio/wav")}, headers=headers).json()[
            "id"
        ]
    note = wait_for_note(client, headers, note_id)
    assert note["status"] == "failed" and "minutes" in note["error"]


def test_daily_quota(make_client):
    client = make_client(user_daily_note_limit=2)
    headers = auth_headers(client)
    for _ in range(2):
        wait_for_note(client, headers, submit_text(client, headers).json()["id"])
    blocked = submit_text(client, headers)
    assert blocked.status_code == 429
    assert "2 free notes for today" in blocked.json()["error"]["message"]
    assert client.get("/api/v1/me", headers=headers).json()["usage"] == {"used_today": 2, "daily_limit": 2}


def test_failed_notes_do_not_use_quota(make_client):
    from conftest import FakeLLM

    client = make_client(fake_registry(llms=[FakeLLM(fail=True)]), user_daily_note_limit=1)
    headers = auth_headers(client)
    failed = wait_for_note(client, headers, submit_text(client, headers).json()["id"])
    assert failed["status"] == "failed"
    assert "traffic" in failed["error"] and "fake-llm" not in failed["error"]  # calm wording, no provider names
    assert submit_text(client, headers).status_code == 202


def test_global_cloud_capacity(make_client):
    client = make_client(global_daily_cloud_limit=1)
    a, b = auth_headers(client), auth_headers(client)
    wait_for_note(client, a, submit_text(client, a).json()["id"])
    res = submit_text(client, b)
    assert res.status_code == 429 and "full capacity for today" in res.json()["error"]["message"]


def test_unlimited_users_bypass_quotas(make_client):
    client = make_client(user_daily_note_limit=1)
    headers = auth_headers(client)
    me = client.get("/api/v1/me", headers=headers).json()["user_id"]
    ctx = client.app.state.ctx
    ctx.quotas.settings = replace(ctx.quotas.settings, unlimited_user_ids=[me])
    for _ in range(3):
        assert submit_text(client, headers).status_code == 202


def test_engine_preference(make_client):
    client = make_client(fake_registry(private=True))
    headers = auth_headers(client)
    engines = client.get("/api/v1/engines").json()
    assert [e["name"] for e in engines["engines"]] == ["cloud", "private"]

    me = client.patch("/api/v1/me", json={"engine": "private"}, headers=headers).json()
    assert me["engine"] == "private"
    note = wait_for_note(client, headers, submit_text(client, headers).json()["id"])
    assert note["engine"] == "private"


def test_unavailable_engine(client):
    headers = auth_headers(client)
    assert client.patch("/api/v1/me", json={"engine": "private"}, headers=headers).status_code == 503


def test_feedback(client):
    headers = auth_headers(client)
    assert (
        client.post("/api/v1/feedback", json={"message": "Please add PDF export"}, headers=headers).status_code == 200
    )


def test_admin_requires_key(client):
    assert client.get("/api/v1/admin/analytics").status_code == 401
    assert client.get("/api/v1/admin/analytics", headers={"x-admin-key": "wrong"}).status_code == 401


def test_admin_analytics(client):
    headers = auth_headers(client)
    note_id = submit_text(client, headers).json()["id"]
    wait_for_note(client, headers, note_id)
    client.post(f"/api/v1/notes/{note_id}/rating", json={"rating": "up"}, headers=headers)

    data = client.get("/api/v1/admin/analytics?days=7", headers={"x-admin-key": "admin-key"}).json()
    assert data["users"]["total"] == 1 and data["users"]["wau"] == 1
    assert data["notes"]["in_window"] == 1 and data["notes"]["failure_rate"] == 0.0
    assert data["ratings"] == {"thumbs_up": 1, "thumbs_down": 0, "accuracy_pct": 100.0}
    assert data["latency_ms"]["samples"] == 1
    assert data["channels"] == {"web": 1}
    assert data["recent"][0]["user"].startswith("web_…")
    assert len(data["timeline"]) == 8


def test_admin_analytics_without_ratings_reports_none(client):
    data = client.get("/api/v1/admin/analytics", headers={"x-admin-key": "admin-key"}).json()
    assert data["ratings"]["accuracy_pct"] is None  # not a fake 100%


def test_request_info_helps_configure_proxy_hops(client):
    headers = {"x-admin-key": "admin-key", "x-forwarded-for": "1.2.3.4, 203.0.113.7"}
    info = client.get("/api/v1/admin/request-info", headers=headers).json()
    assert info["x_forwarded_for"] == "1.2.3.4, 203.0.113.7"
    assert info["trusted_proxy_hops"] == 0 and info["derived_client_ip"] == "testclient"
    assert client.get("/api/v1/admin/request-info").status_code == 401


def test_deep_health_checks_database(client):
    assert client.get("/health?deep=1").json()["database"] == "ok"


def test_admin_reports_time_saved(client):
    data = client.get("/api/v1/admin/analytics", headers={"x-admin-key": "admin-key"}).json()
    assert data["time_saved"] == {"listening_minutes": 0, "waiting_minutes": 0, "median_speedup": None}

# Deploying Vaani

Everything here stays on free tiers. Hosted setup: **Render** (free web service) + **Supabase** (free
Postgres) + **Groq** and **Gemini** (free API keys). Total cost: ₹0.

- [1. Upgrading an existing v1 deployment](#1-upgrading-an-existing-v1-deployment)
- [2. Fresh deployment](#2-fresh-deployment)
- [3. Keeping it awake (and Supabase from pausing)](#3-keeping-it-awake-and-supabase-from-pausing)
- [4. WhatsApp](#4-whatsapp-optional)
- [5. Self-hosting with the private engine](#5-self-hosting-with-the-private-engine)
- [6. Free-tier limits and capacity](#6-free-tier-limits-and-capacity)
- [7. Rollback](#7-rollback)

---

## 1. Upgrading an existing v1 deployment

v2 adds columns to your database (it never drops or rewrites data), switches the Telegram bot to webhooks,
and needs a few new environment variables. Do these in order.

### 1.1 Back up the database

The migration is additive and was tested against a v1-shaped database, but it's the first time it touches
*your* data, and Supabase's free plan has no automatic backups. A backup takes a minute:

```bash
python scripts/backup_db.py
```

It reads `DATABASE_URL` from `.env`, changes nothing, and writes `backups/vaani-<timestamp>.json`
(git-ignored; it contains transcripts, so keep it private). Alternative without Python: Supabase dashboard →
Table Editor → each table → **Export → CSV**.

### 1.2 Remove the synthetic traffic

The old traffic generator wrote fake notes under one user id. Usage numbers should only count real people:

```bash
python scripts/purge_user.py --user-id tg_resume_pumper_999          # dry run: shows what would go
python scripts/purge_user.py --user-id tg_resume_pumper_999 --yes    # deletes it
```

### 1.3 Update Render's environment variables

Render dashboard → your service → **Environment**:

| Variable | Value | Why |
|---|---|---|
| `APP_ENV` | `production` | **Required.** Without it the bot stays off and sessions reset on every restart. |
| `SECRET_KEY` | output of `python -c "import secrets; print(secrets.token_urlsafe(32))"` | **Required.** Signs web sessions. Never change it afterwards (it also keys WhatsApp user ids). |
| `TELEGRAM_MODE` | `webhook` | Telegram calls your server, so the bot works even when Render sleeps. |
| `TRUSTED_PROXY_HOPS` | `1` | Correct client IPs for rate limiting (verify in step 1.5). |
| `ANALYTICS_EXCLUDED_USER_IDS` | your own ids, e.g. `tg_123456789` | Keeps your testing out of the usage numbers. |
| `UNLIMITED_USER_IDS` | your own ids | Exempts you from the daily note limit. |

Keep `DATABASE_URL`, `GROQ_API_KEY`, `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `ADMIN_SECRET_KEY`.
Delete the variables v2 no longer uses: `USE_LOCAL_MODELS`, `INTERNAL_API_SECRET`, `FASTAPI_BACKEND_URL`,
`MODEL_DIR`, `HOST`. Don't set `PORT`; Render provides it.

Your Telegram user id: send any message to [@userinfobot](https://t.me/userinfobot); Vaani's id for you is
`tg_` + that number.

### 1.4 Deploy

Merge the `upgrade/v2` branch into `main` and push (or point Render at the branch). Render rebuilds from the
`Dockerfile`. In **Logs**, a healthy start looks like:

```
Adopting pre-migration database: stamping 0001_baseline
Running upgrade 0001_baseline -> 0002_jobs_and_metrics
Telegram webhook registered at https://<your-app>.onrender.com/telegram/webhook
Vaani 2.0.0 started (production) | engines: cloud | telegram: webhook | whatsapp: off
```

### 1.5 Check it works

1. Open `https://<your-app>.onrender.com/health`. You should see `"status": "ok"` and `"telegram": "webhook"`.
2. Send the bot a voice note on Telegram.
3. Open `/admin`, enter your admin key, and check the note appears.
4. Proxy check (once): this request sends a fake forwarded address and should report your real IP, not `1.2.3.4`:
   ```bash
   curl -H "X-Admin-Key: <ADMIN_SECRET_KEY>" -H "X-Forwarded-For: 1.2.3.4" https://<your-app>.onrender.com/api/v1/admin/request-info
   ```
   If `derived_client_ip` shows `1.2.3.4` or a Render-internal address instead of your IP, adjust
   `TRUSTED_PROXY_HOPS` (try `2`) and check again.

---

## 2. Fresh deployment

1. **Database:** create a free project at [supabase.com](https://supabase.com) → Project Settings →
   Database → copy the connection string (use the *Session pooler* URI) as `DATABASE_URL`.
2. **Keys:** Groq at [console.groq.com/keys](https://console.groq.com/keys), Gemini at
   [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
3. **Telegram bot:** talk to [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
4. **Render:** New → **Blueprint** → select this repo. `render.yaml` creates the service; fill in the
   variables it asks for (see the table in 1.3).
5. Follow 1.5 to check it.

Tables are created automatically on first start.

---

## 3. Keeping it awake (and Supabase from pausing)

Render's free instances sleep after 15 minutes without traffic, and the first request then takes ~1 minute.
Telegram retries webhook deliveries, so messages still arrive, just slower. Supabase pauses free projects
after a week without database activity.

Fix both with a free monitor at [uptimerobot.com](https://uptimerobot.com): HTTP(s) monitor on
`https://<your-app>.onrender.com/health?deep=1` every 10 minutes. `deep=1` also runs a trivial database
query. This creates no fake usage: health checks never appear in your analytics.

One free Render service running 24/7 uses ~744 of the 750 free instance-hours per month, so don't run a second
always-on free service in the same Render workspace.

---

## 4. WhatsApp (optional)

Uses Meta's **WhatsApp Cloud API** directly (no paid middleman like Twilio).

**Cost:** Meta's [pricing page](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
says replies inside the 24-hour customer-service window are free, but some providers report per-message
billing for service messages from October 2026 (with ~1,000 free per month). Vaani never sends unprompted
(template) messages, and `WHATSAPP_DAILY_MESSAGE_CAP` (default 30/day ≈ 900/month) hard-stops outgoing
messages. **Start with Meta's test number**, which is free and lets you add up to 5 tester phone numbers:
perfect for a friends-and-family beta.

1. [developers.facebook.com](https://developers.facebook.com) → **My Apps → Create app** → use case
   *Connect with customers through WhatsApp* → link or create a business portfolio.
2. In the app: **WhatsApp → API Setup**. Note the **Phone number ID** of the test number, and add your own
   phone under *To* as a recipient.
3. **Permanent token:** business.facebook.com → Settings → **System users** → add one (Admin) → *Add assets*
   → your app (full control) → **Generate token** with `whatsapp_business_messaging` and
   `whatsapp_business_management`. (The token on the API Setup page expires after 24 hours.)
4. **App secret:** App settings → Basic → *App secret*.
5. Set on Render: `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, and
   `WHATSAPP_VERIFY_TOKEN` (any random string you make up). Redeploy.
6. **WhatsApp → Configuration → Webhook → Edit**: callback URL `https://<your-app>.onrender.com/whatsapp/webhook`,
   verify token = your `WHATSAPP_VERIFY_TOKEN` → **Verify and save**. Then *Manage* webhook fields →
   subscribe to **messages**.
7. Send "hi" to the test number from your phone, then a voice note.

To go public later (real number, anyone can message): add a phone number in WhatsApp Manager, complete business
verification, set the app to **Live** (needs the privacy policy URL: `https://<your-app>.onrender.com/privacy`),
and check *Payment settings* in Business Manager first.

---

## 5. Self-hosting with the private engine

Runs everything on your own machine (~6 GB RAM): speech-to-text and the LLM never leave it.

1. Download a GGUF model into `models/llm.gguf` (see the README for the current recommendation).
2. `cp .env.example .env`, set `SECRET_KEY`, optionally `ADMIN_SECRET_KEY` and the cloud keys.
3. `docker compose up --build` → http://localhost:8000. With cloud keys set, users can switch between
   ⚡ Fast and 🔒 Private mode.

Without Docker, for development: `python scripts/dev.py --private` (uses `ml/llama-server.exe` and
`models/phi-3-mini-q4_k_m.gguf` by default).

---

## 6. Free-tier limits and capacity

| Service | Free limit (check your own console; these change) | How Vaani handles it |
|---|---|---|
| Groq Whisper large-v3 | 2,000 requests/day, plus audio-seconds limits | One request per note |
| Gemini 2.5 Flash / 3.5 Flash | small per-model daily and per-minute quotas (≈20/day reported for 2.5 Flash) | First in the chain; on quota errors the model is skipped until it recovers |
| Groq gpt-oss-120b | 1,000 requests/day, 8,000 tokens/minute | Carries volume once Gemini quotas run out |
| Render | 512 MB RAM, sleeps after 15 min idle, 750 h/month | Webhooks + uptime monitor |
| Supabase | 500 MB database, pauses after a week idle | `/health?deep=1` monitor |

Vaani's own limits (environment variables): `USER_DAILY_NOTE_LIMIT` (20), `USER_BURST_LIMIT` (5 per
10 minutes), `GLOBAL_DAILY_CLOUD_LIMIT` (300 notes/day across everyone). Users get a clear message when a limit
is reached; nothing fails silently.

When you outgrow this: enabling billing on the Gemini API raises its limits by an order of magnitude (paid per
token, usually cents per hundred notes), and Render's starter plan removes sleeping.

---

## 7. Rollback

Render → your service → **Events** → pick the previous deploy → **Rollback**. The v2 migration only *adds*
columns, so the old code keeps working against the upgraded database. In the worst case, restore from the backup
in 1.1.

# vanbutler

WhatsApp meal-planning butler for a household food group: Whapi webhooks, Postgres (Neon), LLM intent handling, and scheduled evening greetings.

## Quick start

1. Copy `.env.sample` to `.env` and fill in required values (see [Environment (.env)](#environment-env) below).
2. Python 3.12: `python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
3. `alembic upgrade head` (with `DATABASE_URL` set)
4. Run: `uvicorn main:app --host 0.0.0.0 --port 8787`

**Dev tooling (optional):** `pip install -e ".[dev]"` then `pre-commit install` — runs Ruff on each commit.

**Required env:** `WHAPI_URL`, `WHAPI_TOKEN`, `FOOD_GROUP_ID`, `DATABASE_URL`, `GEMINI_API_KEY`, `GROQ_KEY`  
**Optional env:** `COHERE_KEY` (trivia ingest), persona vars (`BUTLER_NAME`, `HOUSEHOLD_NAMES`, `BUTLER_BACKSTORY`), webhook toggles (`WEBHOOK_QUIET`, `WEBHOOK_DUMP_PATH`, `ENABLE_SLOT_OPTIONS_TRIGGER`, `WEBHOOK_PORT`)

---

vanbutler — Whapi food-group webhook + outbound helpers
========================================================

Overview
--------
This repo connects a WhatsApp group (the “food” group) to Whapi.cloud: a small
FastAPI app receives incoming message webhooks, filters to that group, and
logs them. A separate helper sends outbound text via the Whapi HTTP API.

When you add significant behavior (new routes, auth, reply logic, deployment),
update this README so the flow and env vars stay accurate.


Python version
--------------
This repo targets **Python 3.12** (see pyproject.toml and .python-version for
pyenv). Older runtimes are not supported.

To switch an existing machine from 3.8 (or any) venv to 3.12:

1. Install Python 3.12 (e.g. https://www.python.org/downloads/ or
   brew install python@3.12).

2. From the repo root, remove the old venv and create a new one:
     deactivate   # if a venv is active
     rm -rf .venv
     python3.12 -m venv .venv
     source .venv/bin/activate
     pip install -U pip
     pip install -r requirements.txt

3. Confirm: python --version  →  Python 3.12.x

If you use conda as well, "conda deactivate" until only (.venv) remains so
python/pip resolve inside the project venv, not base.


End-to-end flow (incoming webhooks)
----------------------------------
1. Whapi receives a WhatsApp event and POSTs JSON to your public webhook URL
   (messages are typically in a top-level "messages" array in body mode).

2. In local development, ngrok (or similar) exposes HTTPS and tunnels to your
   machine, e.g. https://<subdomain>.ngrok-free.app -> localhost. Ngrok is not
   just "install and run": it requires a free ngrok account and a one-time
   authtoken on each machine (see "Ngrok setup" below). Without that you get
   ERR_NGROK_4018 / "authentication failed".

3. uvicorn runs the FastAPI app in main.py on a TCP port (default 8787).

4. FastAPI handles POST /webhook: it parses JSON, keeps only messages whose
   chat_id matches FOOD_GROUP_ID, logs text (and other types briefly), and
   always responds with HTTP 200 quickly so Whapi does not retry aggressively.

   Whapi -> HTTPS (ngrok) -> uvicorn -> FastAPI POST /webhook -> 200 OK


Implementation (files)
----------------------
config.py
  Persona settings from env: BUTLER_NAME, HOUSEHOLD_NAMES, BUTLER_BACKSTORY
  (defaults preserve Van / Jiwesh and Mansi / Tulip Cruise backstory).

main.py
  FastAPI application: GET /health ({"status": "ok"}), POST /webhook (filter +
  log + 200). Loads .env via python-dotenv at import. Optional: python main.py
  uses WEBHOOK_PORT (default 8787). Webhook debug dumps default to
  runtime/last_webhook_body.json when WEBHOOK_QUIET=0.

messages_service/
  Package: ``message_service.py`` (Whapi normalize + upsert into ``messages``),
  ``helpers.py`` (outbound ``send_food_group_message`` and canned replies),
  ``debounce.py`` (global coalescing delay before async callbacks),
  and ``intent/`` (conversation snippet, LLM intent JSON, meal handlers).

trivia/
  Trivia ingest with semantic dedup (Cohere embed + rerank via REST) and
  greeting helpers. All matching logic lives in ``trivia/matching.py``.
  Near-duplicates rejected at ingest are appended to ``trivia/semanticals.csv``.

llm/
  LLM integration spanning two providers:
    - Trivia greeting → Gemini (``google-genai``). Gemini's prior produces
      warmer, wittier WhatsApp prose for the daily greeting.
      ``generate_trivia_greeting`` (prompt in
      ``llm/prompts.py::get_trivia_greeting_prompt``).
    - Intent classification → Groq (``groq``), Llama 3.3 70B with JSON mode.
      ``analyze_conversation_intent_raw`` (intent classification only — meal
      options are formatted in plain Python by
      ``meal_planning/meal_options.py::format_meal_options_message``, no LLM).
      JSON mode constrains the model to emit a valid JSON object matching the
      schema in the prompt.
  Every call is wrapped by ``_call_and_log``, which dispatches to the right
  provider, times the request, and persists one row to ``llm_logs`` (kind,
  model, full prompt, raw response, token counts, latency, attempt, error if
  any, plus ``request_metadata.provider`` so logs are filterable by backend).
  Transient errors (provider-appropriate 5xx, 429 rate-limit, and connection/
  timeout) are retried with the delays in ``_TRANSIENT_RETRY_DELAYS``
  (currently 30s, 60s — three attempts total per call). Logging is
  best-effort and never breaks the main flow. Prompt strings live in
  ``llm/prompts.py``.

database.py
  SQLAlchemy Base, engine (postgresql+psycopg), SessionLocal, get_session(),
  check_connection(). See "Neon" and "Database models & migrations" below.

models.py
  ORM tables: Message (messages), Meal, MealPrep, DailyOption, DailyChoice, LLMLog,
  Trivia (trivia), TriviaEmbedding (trivia_embeddings).
  DB column "from" → attribute from_wa; DB "date" → calendar_date; DB "timestamp"
  → message_at. direction: e.g. inbound/outbound. Meal.slot is JSONB: JSON array of
  slot strings; each must be breakfast, lunch, or dinner (models.ALLOWED_MEAL_SLOTS and
  assert_valid_meal_slot_values). daily_options.slot / daily_choices.slot use the same
  three strings only. DailyOption.meal_ids is JSONB: JSON array of integer meal ids.

alembic/
  env.py, versions/… — Postgres migrations (initial revision creates all tables).

requirements.txt
  fastapi, uvicorn, python-dotenv, requests, psycopg, sqlalchemy, alembic,
  google-genai + groq (LLM SDKs — Gemini for greeting, Groq for intent),
  rapidfuzz (meal-name matching).

pyproject.toml
  declares requires-python >= 3.12.

.python-version
  optional; pyenv/asdf use this to pick 3.12 in this directory.

scripts/seed_meals.py
  Optional preliminary rows in meals and meal_prep (see python -m scripts.seed_meals
  under "Database models & migrations").

scripts/add_trivia.py, scripts/seed_trivia.py, scripts/send_trivia_greeting.py
  Trivia ingest, seed data, and test greeting (see "Trivia" below).

scripts/run_daily_evening_trigger.py
  Daily 18:42 IST orchestration: trivia greeting + first unfrozen meal slot (see
  "Daily evening trigger" below). Production schedule uses GitHub Actions.

meal_planning/orchestration.py
  Shared slot-order logic for the evening trigger and post-freeze follow-ups
  (menu summary or next-slot options).


Environment (.env)
------------------
Do not commit real secrets. Copy `.env.sample` to `.env`. Typical variables:

  WHAPI_URL       Base URL for your Whapi channel (no trailing slash required;
                  code strips it).
  WHAPI_TOKEN     Bearer token for Whapi API calls.
  FOOD_GROUP_ID   WhatsApp group id used both for outbound "to" and inbound
                  webhook filtering (chat_id on each message).

  DATABASE_URL    Neon PostgreSQL connection URI (see below). When set,
                  GET /health includes "database": "ok" or "error".

  COHERE_KEY      Cohere API key for trivia embed + rerank (ingest dedup).
  GEMINI_API_KEY  Google Gemini key (trivia greeting, cook absence).
  GROQ_KEY        Groq API key for intent-classification.

  BUTLER_NAME     Optional; butler persona name in prompts (default Van).
  HOUSEHOLD_NAMES Optional; household members phrasing (default "Jiwesh and Mansi").
  BUTLER_BACKSTORY Optional; backstory paragraph for intent prompt only.

  WEBHOOK_PORT    Optional; only when you run "python main.py" (default 8787).
  WEBHOOK_QUIET   Optional; 1=quiet (default). Set 0 to dump full webhook JSON.
  WEBHOOK_DUMP_PATH Optional; dump file when WEBHOOK_QUIET=0 (default
                  runtime/last_webhook_body.json).
  ENABLE_SLOT_OPTIONS_TRIGGER Optional; set 1 to enable POST /internal/send-slot-options.


Neon (Postgres) — connect this system to your database
----------------------------------------------------
Neon is managed PostgreSQL. Your FastAPI app talks to it over the internet using
the official connection string from the Neon Console.

1. In https://console.neon.tech open your project → Connect → copy the URI
   (includes user, password, host, dbname).

2. Put it in .env (quoted if it contains special characters), for example:
     DATABASE_URL="postgresql://USER:PASSWORD@HOST/neondb?sslmode=require&channel_binding=require"
   Use the exact string Neon shows; it must use TLS (sslmode=require is typical).

3. pip install -r requirements.txt  (SQLAlchemy + psycopg + Alembic).

4. Apply migrations from the repo root (DATABASE_URL must be set):
     alembic upgrade head

5. Use database.get_session() for ORM sessions; import model classes from models.
   GET /health calls check_connection() when DATABASE_URL is set.

If /health shows "database":"error", check the URI, IP allowlists (if any), and
Neon docs: https://neon.com/docs/guides/python

Neon compute can scale to zero; first query after idle may be slower. The engine
uses pool_pre_ping=True; adjust pool_recycle per Neon docs if you see idle SSL
errors.

Database models & migrations
----------------------------
- Schema is defined in models.py and created by Alembic revision 20260428_0001
  (tables: messages, meals, meal_prep, daily_options, daily_choices).
- daily_options.meal_ids is JSONB (JSON array of integer meal ids). meals.slot is
  JSONB (JSON array of slot name strings). Revision 20260429_0002 converts varchar/array
  to JSONB if needed; 20260430_0003 adds CHECK constraints so every slot value is only
  breakfast, lunch, or dinner (lowercase).
- daily_choices has a unique constraint on (date, slot) so there is at most one
  choice per day per slot.
- meal_prep.meal_id CASCADE-deletes when a meal is deleted; daily_choices.meal_id
  RESTRICTs delete if still referenced.
- Revision 20260508_0006 adds ``llm_logs`` (kind, model, full prompt, response_text,
  response_metadata JSONB with token counts/finish_reason, request_metadata JSONB,
  latency_ms, attempt, error). Written best-effort by ``llm.llm_service`` after every
  ``generate_content`` call — both happy path and exceptions — so every request and
  response is queryable for debugging.
- Revision 20260518_0007 adds ``trivia`` (category, trivia text, created_at,
  last_sent_on) and ``trivia_embeddings`` (trivia_id FK CASCADE, model, input_type,
  embedding JSONB float vector). One embedding row per accepted trivia row.

Commands (repo root, venv active, DATABASE_URL in .env):

  alembic upgrade head       # apply pending migrations
  alembic current            # show current revision
  alembic history            # list revisions

Optional sample data for meals + meal_prep (skips if meals already has rows):

  python -m scripts.seed_meals
  python scripts/seed_meals.py

After editing models.py for new columns/tables:

  alembic revision --autogenerate -m "describe change"
  # review alembic/versions/*.py, then:
  alembic upgrade head


Trivia — ingest, dedup, and greeting
------------------------------------
Van can open the food-group chat with a short greeting that weaves in a trivia
fact. Trivia is stored in Postgres; new facts go through semantic dedup so
near-duplicates are not added twice.

Tables (see models.Trivia, models.TriviaEmbedding):
  trivia            category, trivia (text), created_at, last_sent_on (null until sent)
  trivia_embeddings one row per trivia: Cohere embed stored as JSONB float array
                    (input_type search_document at ingest time)

Ingest pipeline (``trivia.matching.ingest_trivia``):
  1. Embed incoming text with Cohere ``embed-v4.0``, input_type ``search_query``.
  2. Cosine similarity vs existing embeddings in the same category (Python;
     cutoff 0.5, keep top 30).
  3. If any candidates: Cohere rerank ``rerank-v3.5``, top_n 5. If any result has
     relevance_score > 0.8, reject ingest and append rows to ``trivia/semanticals.csv``
     (columns: input_trivia, db_trivia, relevance_score, model, created_at).
  4. Otherwise insert trivia + embed with input_type ``search_document``.

Add trivia (CLI):
  python -m scripts.add_trivia --category Cricket --trivia "Your fact here..."
  # prints {"status": "added", "trivia_id": N} or {"status": "rejected", "matches": [...]}

Seed initial cricket facts (skips if exact trivia text already in DB):
  python -m scripts.seed_trivia

Send a test greeting (uses TEST_TRIVIA in scripts/send_trivia_greeting.py;
does not read from DB or update last_sent_on):
  python -m scripts.send_trivia_greeting
  # requires WHAPI_*, FOOD_GROUP_ID, GEMINI_API_KEY

Send greeting for a DB row and set last_sent_on (Python):
  from trivia import send_trivia_by_id
  send_trivia_by_id(1)

Or send arbitrary trivia text without DB (Python):
  from messages_service.helpers import send_trivia_greeting
  send_trivia_greeting("Some trivia text...")
  # Gemini generates the message; logged to llm_logs as kind trivia_greeting

Requires DATABASE_URL + COHERE_KEY for ingest; WHAPI_* + GEMINI_API_KEY for send.

Random unsent greeting (evening trigger uses this; marks last_sent_on):
  from trivia.matching import send_random_unsent_trivia_greeting
  send_random_unsent_trivia_greeting()
  # random row with last_sent_on IS NULL; if all sent, reuses oldest by created_at


Daily evening trigger (18:42 IST)
---------------------------------
Van opens each evening with a trivia greeting, then advances meal planning for
**tomorrow** (Asia/Kolkata calendar date — same as daily_options / daily_choices).

**Frozen** means a row exists in ``daily_choices`` for that date + slot (breakfast,
lunch, or dinner). **Unfrozen** means no row for that slot.

System meals (seeded in ``meals``, migration ``20260520_0008``):
  - ``meal_id = 0`` → name **None** (user is skipping that slot; distinct from unfrozen).
  - ``meal_id = -1`` → name **Cook Not Coming** (cook/didi absent for that slot).
  These never appear in suggested option lists or meal-name matching.

WhatsApp intents (Groq, ``llm/prompts.py``):
  - **SKIP_MEAL** — user won't have a slot tomorrow; writes ``meal_id = 0``.
  - **COOK_NOT_COMING** — cook absence or correction; secondary LLM
    (``cook_absence_resolve``, last 10 messages **IST today**) outputs ``set_cook_absent``
    (writes ``-1``) or ``clear`` (deletes only existing ``-1`` rows — undo/narrowing).
  Corrections (e.g. "didi is coming tomorrow") still use **COOK_NOT_COMING** on the main
  classifier; the secondary prompt applies ``clear`` only for those turns, not first-time absence.

Evening flow (``meal_planning.orchestration.run_daily_evening_trigger``):
  1. Send trivia greeting (random unsent trivia, or oldest if all have been sent).
  2. If all three slots are frozen → message that tomorrow's menu is already frozen.
  3. Else send options for the **first unfrozen** slot in order breakfast → lunch → dinner.
     - Breakfast: options immediately after the greeting.
     - Lunch or dinner: a short intro line first ("We'll decide the lunch menu now.", etc.).

Post-freeze flow (after FREEZE_MEAL_OPTION in WhatsApp, ``on_daily_choices_updated``):
  Runs after the existing freeze-confirmation message.
  1. If all three slots frozen → one message with tomorrow's menu (meal names per slot).
     Optional **Preparations** and **Ingredients** sections appear only when at least
     one chosen meal has non-blank ``meal_prep`` data; each section lists one line per
     slot that has data (e.g. ``Breakfast: soak overnight``).
  2. Else → "I'll share {Slot} options now." then options for the first unfrozen slot.

Manual CLI (repo root, venv active):
  python -m scripts.run_daily_evening_trigger
  python -m scripts.run_daily_evening_trigger --storage-date 2026-05-20

Requires DATABASE_URL, WHAPI_URL, WHAPI_TOKEN, FOOD_GROUP_ID, GEMINI_API_KEY, and at
least one row in ``trivia``.

GitHub Actions (``.github/workflows/daily_evening_trigger.yml``):
  - Schedule: 18:42 IST daily (cron 13:12 UTC).
  - ``workflow_dispatch`` for manual runs.
  - Repository secrets: DATABASE_URL, WHAPI_URL, WHAPI_TOKEN, FOOD_GROUP_ID,
    GEMINI_API_KEY (COHERE_KEY not required for this job).

Slot options alone (without the evening greeting) remain available for ad hoc use:
  python -m scripts.send_slot_options --slot breakfast
The old crontab example in that script (18:52 IST) is superseded by the unified
evening trigger for production scheduling.


Ngrok setup (once per machine, before "ngrok http …")
-----------------------------------------------------
Ngrok will refuse to tunnel until you register and install an authtoken.

1. Sign up (free): https://dashboard.ngrok.com/signup
   Verify your email if ngrok asks.

2. Copy your authtoken: https://dashboard.ngrok.com/get-started/your-authtoken

3. On this Mac, run once (replace with your real token; do not commit it):
     ngrok config add-authtoken YOUR_AUTHTOKEN_HERE

4. Install the ngrok CLI if needed (e.g. brew install ngrok/ngrok/ngrok) so
   "ngrok" is on your PATH.

If you see ERR_NGROK_4018 or "authentication failed", the token is missing,
wrong, or the account is not verified — repeat steps 1–3.


Run locally (webhook receiver)
------------------------------
1. Use Python 3.12 (see "Python version" above). Create a virtualenv, then:
     python3.12 -m venv .venv && source .venv/bin/activate
     pip install -r requirements.txt

2. Fill .env with WHAPI_URL, WHAPI_TOKEN, FOOD_GROUP_ID.

3. Start the app:
     uvicorn main:app --host 0.0.0.0 --port 8787
   or:
     python main.py

4. Complete "Ngrok setup" above if you have not already. Then in a second
   terminal (venv not required): ngrok http 8787

5. In the Whapi dashboard (or settings API), set the webhook URL to:
     https://<your-ngrok-host>/webhook
   Enable the messages webhook (POST, body mode) per Whapi docs.

6. GET http://localhost:8787/health should return {"status":"ok"} (and
   "database":"ok" when DATABASE_URL is set and Neon is reachable).


Notes / limits (current milestone)
----------------------------------
- Webhook handler does not verify signatures or shared secrets (v1).
- Inbound text to ``FOOD_GROUP_ID`` is debounced (see ``messages_service/debounce.py``)
  then classified via the intent LLM (Groq, see ``llm/``);
  ``messages_service/helpers.send_food_group_message`` may reply. Outbound rows are
  ignored for intent so Van does not loop on her own messages.
- Invalid or non-JSON bodies still return 200 after a failed parse, to reduce
  noisy retries; tighten this if you need strict validation later.

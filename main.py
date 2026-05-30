import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

load_dotenv()

from database import engine, get_session
from messages_service.debounce import arm_or_reset_debounce
from messages_service.intent.intent_service import process_food_group
from messages_service.message_service import extract_text_body, upsert_message_from_whapi

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


class _SuppressHealthPing(logging.Filter):
    """Drop uvicorn access log lines for the root health ping endpoint."""

    def filter(self, record: logging.LogRecord) -> bool:
        return " GET / " not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_SuppressHealthPing())

# Full webhook capture (file + stderr/stdout) is OFF by default.
# Set WEBHOOK_QUIET=0 in .env to re-enable verbose JSON dumps for debugging.
_WEBHOOK_QUIET = os.environ.get("WEBHOOK_QUIET", "1").lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Written on every POST /webhook so you can open the full payload in an editor.
_WEBHOOK_DUMP_PATH = Path(
    os.environ.get("WEBHOOK_DUMP_PATH", "runtime/last_webhook_body.json")
)

app = FastAPI()


@app.on_event("startup")
def _log_webhook_persistence() -> None:
    if engine is None:
        log.warning(
            "DATABASE_URL is not set or engine failed to init: "
            "webhook hits are logged but messages are NOT written to the database"
        )
    else:
        log.info("Database engine ready; webhook messages will be upserted into messages")
    if not _WEBHOOK_QUIET:
        log.warning(
            "Webhook debug: every POST /webhook writes the full JSON to %s "
            "(set WEBHOOK_QUIET=1 to disable)",
            _WEBHOOK_DUMP_PATH.resolve(),
        )


def _persist_webhook_payload_for_inspection(body: object) -> None:
    """Guaranteed copy of what Whapi sent — survives terminal truncation / log filters."""
    if _WEBHOOK_QUIET:
        return
    try:
        text = json.dumps(body, indent=2, ensure_ascii=False, default=str)
    except Exception:
        log.exception("json.dumps webhook body failed")
        text = repr(body)

    try:
        _WEBHOOK_DUMP_PATH.write_text(text, encoding="utf-8")
    except Exception:
        log.exception("failed to write %s", _WEBHOOK_DUMP_PATH)

    out = (
        "\n=== WEBHOOK FULL JSON (vanbutler) ===\n"
        f"{text}\n"
        "=== END WEBHOOK FULL JSON ===\n"
    )
    for stream in (sys.stderr, sys.stdout):
        try:
            stream.write(out)
            stream.flush()
        except Exception:
            pass

    log.warning(
        "Webhook payload (%d bytes) written to %s and printed to stdout+stderr above",
        len(text.encode("utf-8", errors="replace")),
        _WEBHOOK_DUMP_PATH.resolve(),
    )


@app.get("/")
async def root():
    return {"status": "Van is awake and at your service!"}


@app.get("/health")
def health() -> dict:
    out: dict = {"status": "Van is awake and at your service!"}
    if os.environ.get("DATABASE_URL"):
        from database import check_connection

        out["database"] = "ok" if check_connection() else "error"
    return out


class SlotOptionsRequest(BaseModel):
    slot: str
    storage_date: date | None = None
    option_count: int = Field(default=3, ge=1, le=20)


@app.post("/internal/send-slot-options")
async def internal_send_slot_options(body: SlotOptionsRequest) -> dict:
    """Generate slot options, format plain text, send WhatsApp, upsert daily_options.

    Disabled unless ``ENABLE_SLOT_OPTIONS_TRIGGER`` is set to 1/true/yes/on.
    """
    if os.environ.get("ENABLE_SLOT_OPTIONS_TRIGGER", "0").lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        raise HTTPException(
            status_code=403,
            detail="Slot options trigger is disabled (set ENABLE_SLOT_OPTIONS_TRIGGER).",
        )
    from meal_planning.meal_options import run_slot_options

    try:
        return await run_slot_options(
            body.slot,
            storage_date=body.storage_date,
            option_count=body.option_count,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class DailyEveningTriggerRequest(BaseModel):
    storage_date: date | None = None


@app.post("/internal/daily-evening-trigger")
async def internal_daily_evening_trigger(
    body: DailyEveningTriggerRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    """Execute the daily evening trigger for meal planning.

    Requires Bearer token matching CRON_SECRET from GitHub Actions.
    """
    cron_secret = os.environ.get("CRON_SECRET")
    if cron_secret:
        expected_auth_header = f"Bearer {cron_secret}"
        if authorization != expected_auth_header:
            raise HTTPException(status_code=401, detail="Unauthorized request.")

    from meal_planning.orchestration import run_daily_evening_trigger

    try:
        return await run_daily_evening_trigger(storage_date=body.storage_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/webhook")
async def webhook(request: Request) -> Response:
    group_id = os.environ.get("FOOD_GROUP_ID")

    raw = await request.body()
    try:
        body = json.loads(raw)
    except Exception:
        if not _WEBHOOK_QUIET:
            log.warning(
                "webhook body is not JSON (first 500 bytes): %r",
                raw[:500],
            )
        return Response(status_code=200)

    if not isinstance(body, dict):
        return Response(status_code=200)

    if not _WEBHOOK_QUIET:
        _persist_webhook_payload_for_inspection(body)

    messages = body.get("messages")
    if not isinstance(messages, list):
        return Response(status_code=200)

    persist = engine is not None

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("chat_id") != group_id:
            continue

        sender = msg.get("from") or msg.get("from_name") or msg.get("author")
        msg_type = msg.get("type")
        # Entire message object (what Whapi puts in `messages[]`) — no guessing in the log line.
        try:
            msg_json = json.dumps(msg, ensure_ascii=False, default=str)
        except Exception:
            msg_json = repr(msg)

        if msg_type == "text":
            text_body = extract_text_body(msg)
            log.info(
                "food group text: sender=%s extracted_body=%s full_message_json=%s",
                sender,
                text_body,
                msg_json,
            )
        else:
            log.info(
                "food group message: type=%s sender=%s full_message_json=%s",
                msg_type,
                sender,
                msg_json,
            )

        if persist:
            try:
                with get_session() as session:
                    upsert_outcome = upsert_message_from_whapi(session, msg)
                if upsert_outcome == "skipped":
                    raw_id = msg.get("id")
                    log.warning(
                        "messages persist skipped: missing or unsupported message id (type=%s)",
                        type(raw_id).__name__,
                    )
                elif (
                    upsert_outcome == "inserted"
                    and msg_type == "text"
                    and not bool(msg.get("from_me"))
                ):
                    body_for_intent = extract_text_body(msg)
                    if body_for_intent and str(body_for_intent).strip():
                        await arm_or_reset_debounce(process_food_group)
            except Exception:
                log.exception("messages upsert failed for webhook message")

    return Response(status_code=200)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("WEBHOOK_PORT", "8787"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)

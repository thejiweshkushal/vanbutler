"""Normalize Whapi webhook/API payloads and upsert into ``messages`` by ``whapi_message_id``."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from models import Message


def _unwrap_text_body(value: Any) -> str | None:
    """Resolve Whapi ``body`` / ``text.body`` which may be a string or nested ``{\"body\": \"...\"}``."""
    seen: set[int] = set()
    cur: Any = value
    for _ in range(8):
        if isinstance(cur, str):
            return cur
        if not isinstance(cur, dict):
            return None
        oid = id(cur)
        if oid in seen:
            return None
        seen.add(oid)
        if "body" in cur:
            cur = cur["body"]
            continue
        return None
    return None


def unix_ts_to_utc(ts: object) -> datetime | None:
    if ts is None:
        return None
    try:
        n = int(ts)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(n, tz=timezone.utc)


def extract_text_body(msg: dict[str, Any]) -> str | None:
    """Best-effort plain text for Whapi ``type == \"text\"`` (nested ``text.body``, etc.)."""
    text_obj = msg.get("text")
    if text_obj is not None:
        s = _unwrap_text_body(text_obj) if isinstance(text_obj, dict) else (
            text_obj if isinstance(text_obj, str) else None
        )
        if s is not None:
            return s
    s = _unwrap_text_body(msg.get("body"))
    if s is not None:
        return s
    nested = msg.get("message")
    if isinstance(nested, dict):
        return _unwrap_text_body(nested.get("body"))
    return None


def normalize_whapi_message_dict(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Build column kwargs for ``Message`` from a Whapi message object. Returns None if no id."""
    mid = msg.get("id")
    if mid is None or mid == "":
        return None
    if isinstance(mid, bool):
        return None
    if not isinstance(mid, (str, int)):
        return None
    mid = str(mid)

    from_me = bool(msg.get("from_me"))
    direction = "outbound" if from_me else "inbound"
    wa_type = msg.get("type")
    wa_type_s = wa_type if isinstance(wa_type, str) else None

    body: str | None = None
    if wa_type_s == "text":
        body = extract_text_body(msg)

    st = msg.get("status")
    status_s = st if isinstance(st, str) and st else "received"

    from_wa = msg.get("from")
    if isinstance(from_wa, str):
        from_wa_s = from_wa
    elif isinstance(from_wa, int):
        from_wa_s = str(from_wa)
    else:
        from_wa_s = None
    from_name = msg.get("from_name")
    from_name_s = from_name if isinstance(from_name, str) else None

    chat = msg.get("chat_id")
    chat_s = chat if isinstance(chat, str) else None

    return {
        "whapi_message_id": mid,
        "from_wa": from_wa_s,
        "from_name": from_name_s,
        "message_at": unix_ts_to_utc(msg.get("timestamp")),
        "direction": direction,
        "message": body,
        "status": status_s,
        "chat_id": chat_s,
        "wa_type": wa_type_s,
        "status_metadata": None,
    }


def _message_row_values(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Map ORM kwargs to table column keys (``from`` is reserved)."""
    return {
        "whapi_message_id": kwargs["whapi_message_id"],
        "from": kwargs.get("from_wa"),
        "from_name": kwargs.get("from_name"),
        "timestamp": kwargs.get("message_at"),
        "direction": kwargs.get("direction"),
        "message": kwargs.get("message"),
        "status": kwargs.get("status"),
        "chat_id": kwargs.get("chat_id"),
        "wa_type": kwargs.get("wa_type"),
        "status_metadata": kwargs.get("status_metadata"),
    }


UpsertOutcome = Literal["inserted", "updated", "skipped"]


def upsert_message_from_whapi(session: Session, msg: dict[str, Any]) -> UpsertOutcome:
    """
    Insert or update a row keyed by ``whapi_message_id``.
    Returns ``skipped`` if the payload has no usable Whapi id; otherwise ``inserted``
    or ``updated`` depending on whether the row existed before this call.
    """
    normalized = normalize_whapi_message_dict(msg)
    if not normalized:
        return "skipped"

    mid = normalized["whapi_message_id"]
    existed_before = (
        session.scalar(select(Message.id).where(Message.whapi_message_id == mid).limit(1))
        is not None
    )

    tbl = Message.__table__
    row = _message_row_values(normalized)

    stmt = pg_insert(tbl).values(**row)
    excluded = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=[tbl.c.whapi_message_id],
        set_={
            tbl.c["from"]: excluded["from"],
            tbl.c.from_name: excluded.from_name,
            tbl.c.timestamp: excluded.timestamp,
            tbl.c.direction: excluded.direction,
            tbl.c.message: excluded.message,
            tbl.c.status: excluded.status,
            tbl.c.chat_id: excluded.chat_id,
            tbl.c.wa_type: excluded.wa_type,
            tbl.c.status_metadata: excluded.status_metadata,
        },
    )
    session.execute(stmt)
    return "updated" if existed_before else "inserted"


def parse_whapi_message_id_from_send_response(data: dict[str, Any]) -> str | None:
    """Extract Whapi message ``id`` from POST ``/messages/text`` JSON body."""
    if not isinstance(data, dict):
        return None
    m = data.get("message")
    if isinstance(m, dict) and m.get("id"):
        return str(m["id"])
    if data.get("id"):
        return str(data["id"])
    sent = data.get("sent")
    if isinstance(sent, dict):
        inner = sent.get("message")
        if isinstance(inner, dict) and inner.get("id"):
            return str(inner["id"])
        if sent.get("id"):
            return str(sent["id"])
    return None


def parse_status_from_send_response(data: dict[str, Any]) -> str:
    """Delivery-ish status from send response when present."""
    if not isinstance(data, dict):
        return "sent"
    m = data.get("message")
    if isinstance(m, dict):
        s = m.get("status")
        if isinstance(s, str) and s:
            return s
    return "sent"


def delete_message_row_by_pk(session: Session, pk: int) -> None:
    session.execute(delete(Message).where(Message.id == pk))

from __future__ import annotations

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
WORK_RESULTS = {
    "Nicht erreicht": ("not_reached", None, None),
    "Später melden": ("gatekeeper", None, "callback_at"),
    "Mail gewünscht": ("gatekeeper", None, "callback_at"),
    "Follow-up": ("gf", "follow_up", "callback_at"),
    "Termin": ("gf", "meeting", "meeting_at"),
    "Verloren": ("gf", "lost", None),
    "Nicht passend": ("gf", "unfit", None),
    "Nummer falsch": ("wrong_number", None, None),
}


def authorized_user(email, lookup):
    if not email:
        return None
    return lookup(str(email).strip().lower())


def status_label(status, last_reach=None):
    if status == "in_bearbeitung":
        return "🟠 später melden" if last_reach == "gatekeeper" else "🟡 nicht erreicht"
    return {
        "neu": "⚪ neu",
        "follow_up": "🔵 Follow-up",
        "termin": "🟢 Termin",
        "uebergeben": "🟢 Termin",
        "verloren": "🔴 verloren",
        "nicht_passend": "⚫ raus",
        "gesperrt": "⚫ raus",
        "nummer_pruefen": "⚫ raus",
    }[status]


def attempt_from_row(row, caller_id, now):
    result = row.get("result")
    if result not in WORK_RESULTS:
        raise ValueError("Ungültiges Ergebnis.")
    reach, outcome, date_field = WORK_RESULTS[result]
    payload = {"reach": reach, "outcome": outcome, "mail_requested": result == "Mail gewünscht",
               "note": row.get("note") or None}
    value = row.get("date")
    if date_field and value is not None and value == value:
        if not isinstance(value, datetime):
            value = datetime.combine(value, datetime.min.time())
        payload[date_field] = value.replace(tzinfo=BERLIN) if value.tzinfo is None else value.astimezone(BERLIN)
    elif outcome in ("follow_up", "meeting"):
        raise ValueError(f"Für {result} ist ein Datum erforderlich.")
    return payload


def idempotency_key(state, lead_id):
    keys = state.setdefault("akquise_attempt_keys", {})
    return keys.setdefault(str(lead_id), str(uuid4()))


def renew_idempotency_key(state, lead_id):
    state.setdefault("akquise_attempt_keys", {})[str(lead_id)] = str(uuid4())


def save_rows(rows, user, record, state, now):
    saved, errors, warnings = [], [], []
    for row in rows:
        if not row.get("result"):
            continue
        no = row.get("lead_no")
        try:
            payload = attempt_from_row(row, user["id"], now)
            result = record(user, row["id"], payload, idempotency_key(state, row["id"]), now)
            renew_idempotency_key(state, row["id"])
            saved.append(no)
            if result.get("double_call_warning"):
                warnings.append(no)
        except Exception as exc:
            errors.append((no, str(exc)))
    return saved, errors, warnings

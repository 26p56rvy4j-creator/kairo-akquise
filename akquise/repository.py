from __future__ import annotations

import json
from contextlib import contextmanager, nullcontext
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import Engine

from database.session import connection_scope
from services.contact_import import clean_text, normalize_company_name, normalize_domain, normalize_email
from akquise.phone import normalize_phone_de
from akquise.permissions import can
from akquise.status import TERMINAL_STATUSES, calculate_status, validate_attempt

BERLIN = ZoneInfo("Europe/Berlin")
LEAD_FIELDS = {"company_name", "website", "phone_raw", "gf_name", "email", "source", "segment",
               "enrichment_status", "enrichment_score_raw", "enrichment_note", "legacy_status",
               "legacy_note", "legacy_follow_up", "dnc", "dnc_at", "dnc_reason", "unfit_manual"}


def _utc(value):
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Zeitpunkte müssen eine Zeitzone enthalten.")
    return value.astimezone(timezone.utc)


@contextmanager
def _transaction(conn):
    if isinstance(conn, Engine):
        with connection_scope(conn) as scoped:
            yield scoped
    else:
        with nullcontext(conn) as scoped:
            yield scoped


def _user_id(user):
    return user.get("id") if isinstance(user, dict) else user.id


def _lead_state(conn, lead_id):
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        raise LookupError("Lead nicht gefunden.")
    attempts = conn.execute("SELECT * FROM call_attempts WHERE lead_id = ? ORDER BY called_at, id", (lead_id,)).fetchall()
    return lead, attempts, calculate_status(lead, attempts)


def _persist_status(conn, lead_id, state, increment=True):
    conn.execute(
        "UPDATE leads SET acquisition_status = ?, next_action_at = ?, next_action_owner = ?, "
        "last_attempt_at = ?, last_reach = ?, version = version + ?, updated_at = now() WHERE id = ?",
        (state["status"], state["next_action_at"], state["next_action_owner"], state["last_attempt_at"],
         state["last_reach"], int(increment), lead_id),
    )


def record_attempt(conn, user, lead_id, data, idempotency_key, now):
    now = _utc(now)
    if now is None:
        raise ValueError("Ein aktueller Zeitpunkt ist erforderlich.")
    payload = {**data, "called_at": _utc(data.get("called_at", now))}
    if payload["called_at"] is None:
        raise ValueError("called_at ist erforderlich.")
    for field in ("callback_at", "meeting_at"):
        if field in payload:
            payload[field] = _utc(payload[field])
    validate_attempt(payload)
    idempotency_key = clean_text(idempotency_key)
    if not idempotency_key:
        raise ValueError("Ein Idempotenzschlüssel ist erforderlich.")
    if not can(user, "record_attempt"):
        raise PermissionError("Keine Berechtigung für Anrufversuche.")
    with _transaction(conn) as db:
        # Serialize competing writes for a lead and make the version update atomic.
        db.execute("SELECT id FROM leads WHERE id = ? FOR UPDATE", (lead_id,)).fetchone()
        existing = db.execute("SELECT * FROM call_attempts WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        if existing:
            return {"attempt": existing, "double_call_warning": False}
        uid = _user_id(user)
        inserted = db.execute(
            "INSERT INTO call_attempts (lead_id, caller_id, called_at, reach, outcome, mail_requested, "
            "dnc_requested, callback_at, meeting_at, note, idempotency_key, replaces_attempt_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (idempotency_key) DO NOTHING RETURNING id",
            (lead_id, uid, payload["called_at"], payload["reach"], payload.get("outcome"),
             payload.get("mail_requested", False), payload.get("dnc_requested", False), payload.get("callback_at"),
             payload.get("meeting_at"), payload.get("note"), idempotency_key, payload.get("replaces_attempt_id")),
        )
        is_new = inserted.fetchone() is not None
        attempt = db.execute("SELECT * FROM call_attempts WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        if is_new:
            duplicate = db.execute(
                "SELECT 1 FROM call_attempts WHERE lead_id = ? AND caller_id <> ? AND voided_at IS NULL "
                "AND called_at >= ? AND called_at < ? AND id <> ? LIMIT 1",
                (lead_id, uid, now - timedelta(minutes=15), now, attempt["id"]),
            ).fetchone()
            lead, attempts, state = _lead_state(db, lead_id)
            _persist_status(db, lead_id, state)
            return {"attempt": attempt, "double_call_warning": bool(duplicate), "status": state}
        return {"attempt": attempt, "double_call_warning": False}


def void_attempt(conn, user, attempt_id, reason):
    reason = clean_text(reason)
    if not reason:
        raise ValueError("Ein Stornogrund ist erforderlich.")
    with _transaction(conn) as db:
        attempt = db.execute("SELECT * FROM call_attempts WHERE id = ?", (attempt_id,)).fetchone()
        if not attempt:
            raise LookupError("Anrufversuch nicht gefunden.")
        if not can(user, "void_attempt", attempt):
            raise PermissionError("Keine Berechtigung, diesen Anrufversuch zu stornieren.")
        db.execute("SELECT id FROM leads WHERE id = ? FOR UPDATE", (attempt["lead_id"],)).fetchone()
        voided = db.execute(
            "UPDATE call_attempts SET voided_at = now(), voided_by = ?, void_reason = ? "
            "WHERE id = ? AND voided_at IS NULL RETURNING voided_at",
            (_user_id(user), reason, attempt_id),
        ).fetchone()
        if not voided:
            raise ValueError("Der Anrufversuch wurde bereits storniert.")
        _, _, state = _lead_state(db, attempt["lead_id"])
        _persist_status(db, attempt["lead_id"], state)
        return state


def update_lead(conn, user, lead_id, expected_version, changes, reason):
    reason = clean_text(reason)
    if not reason:
        raise ValueError("Ein Änderungsgrund ist erforderlich.")
    if not can(user, "update_lead"):
        raise PermissionError("Keine Berechtigung, Leads zu ändern.")
    if not isinstance(changes, dict) or not changes or set(changes) - LEAD_FIELDS:
        raise ValueError("Ungültige oder leere Lead-Änderung.")
    with _transaction(conn) as db:
        lead = db.execute("SELECT * FROM leads WHERE id = ? FOR UPDATE", (lead_id,)).fetchone()
        if not lead:
            raise LookupError("Lead nicht gefunden.")
        if lead["version"] != expected_version:
            raise RuntimeError("Versionskonflikt: Der Lead wurde zwischenzeitlich geändert.")
        normalized = dict(changes)
        if "company_name" in normalized:
            normalized["company_name"] = clean_text(normalized["company_name"])
            normalized["company_name_norm"] = normalize_company_name(normalized["company_name"])
        if "website" in normalized:
            normalized["domain_norm"] = normalize_domain(normalized["website"])
        if "email" in normalized:
            normalized["email"] = normalize_email(normalized["email"])
        if "phone_raw" in normalized:
            phone_e164, phone_status = normalize_phone_de(normalized["phone_raw"])
            normalized["phone_e164"], normalized["phone_status"] = phone_e164, phone_status
            if phone_e164 != lead["phone_e164"] or (phone_e164 is None and normalized["phone_raw"] != lead["phone_raw"]):
                normalized["phone_changed_at"] = datetime.now(timezone.utc)
        if "dnc_at" in normalized:
            normalized["dnc_at"] = _utc(normalized["dnc_at"])
        for flag in ("unfit_manual", "dnc"):
            if flag in normalized and not isinstance(normalized[flag], bool):
                raise ValueError(f"{flag} muss boolesch sein.")
        values = {**normalized}
        for field, value in normalized.items():
            db.execute("INSERT INTO lead_changes (lead_id, user_id, field, old_value, new_value, reason) "
                       "VALUES (?, ?, ?, CAST(? AS jsonb), CAST(? AS jsonb), ?)",
                       (lead_id, _user_id(user), field, json.dumps(lead.get(field), default=str), json.dumps(value, default=str), reason))
        assignments = ", ".join(f"{column} = ?" for column in values)
        db.execute(f"UPDATE leads SET {assignments}, version = version + 1, updated_at = now() "
                   "WHERE id = ? AND version = ?", (*values.values(), lead_id, expected_version))
        _, attempts, _ = _lead_state(db, lead_id)
        newlead = db.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        state = calculate_status(newlead, attempts)
        _persist_status(db, lead_id, state, increment=False)
        return state


def hand_over(conn, user, lead_id, need_note):
    if not can(user, "hand_over"):
        raise PermissionError("Keine Berechtigung zur Übergabe.")
    with _transaction(conn) as db:
        db.execute("SELECT id FROM leads WHERE id = ? FOR UPDATE", (lead_id,)).fetchone()
        existing = db.execute("SELECT * FROM opportunities WHERE lead_id = ?", (lead_id,)).fetchone()
        if existing:
            return existing
        lead, attempts, state = _lead_state(db, lead_id)
        if state["status"] != "termin":
            raise ValueError("Nur Leads mit Termin können übergeben werden.")
        meeting = next(a for a in reversed(attempts)
                       if a["voided_at"] is None and a["reach"] == "gf" and a["outcome"] == "meeting")
        db.execute("INSERT INTO opportunities (lead_id, source_attempt_id, created_by, meeting_at, need_note) "
                   "VALUES (?, ?, ?, ?, ?) ON CONFLICT (lead_id) DO NOTHING",
                   (lead_id, meeting["id"], _user_id(user), meeting["meeting_at"], clean_text(need_note)))
        opportunity = db.execute("SELECT * FROM opportunities WHERE lead_id = ?", (lead_id,)).fetchone()
        db.execute("UPDATE leads SET handed_over_at = COALESCE(handed_over_at, now()), "
                   "version = version + 1, updated_at = now() WHERE id = ?",
                   (lead_id,))
        updated, valid, _ = _lead_state(db, lead_id)
        _persist_status(db, lead_id, calculate_status(updated, valid), increment=False)
        return opportunity


def is_due(next_action_at, now):
    return next_action_at is not None and _utc(next_action_at) <= _utc(now)


def sort_today_rows(rows):
    due = sorted((row for row in rows if row.get("next_action_at") is not None and row.get("is_due")),
                 key=lambda row: (row["next_action_at"], row["lead_no"]))
    later = sorted((row for row in rows if row.get("next_action_at") is not None and not row.get("is_due")),
                   key=lambda row: (row["next_action_at"], row["lead_no"]))
    fresh = sorted((row for row in rows if row.get("next_action_at") is None), key=lambda row: row["lead_no"])
    return due + later + fresh


def _list_row(row, now):
    item = dict(row)
    item["id"] = str(item["id"])
    item["acquisition_status"] = item.pop("resolved_status")
    item["next_action_at"] = item.pop("resolved_next_action_at")
    item["next_action_owner"] = item.pop("resolved_next_action_owner")
    item["is_due"] = is_due(item["next_action_at"], now)
    return item


def list_today(conn, user, lead_no_from, lead_no_to, now):
    now = _utc(now)
    if now is None:
        raise ValueError("Ein aktueller Zeitpunkt ist erforderlich.")
    tomorrow = datetime.combine(now.astimezone(BERLIN).date() + timedelta(days=1), time.min, BERLIN)
    terminal_sql = ", ".join(f"'{status}'" for status in sorted(TERMINAL_STATUSES))
    with _transaction(conn) as db:
        action_rows = db.execute(
            "SELECT l.*, l.acquisition_status AS resolved_status, l.next_action_at AS resolved_next_action_at, "
            "l.next_action_owner AS resolved_next_action_owner FROM leads l "
            f"WHERE l.acquisition_status NOT IN ({terminal_sql}) AND l.next_action_at IS NOT NULL "
            "AND l.next_action_at < ? AND (l.next_action_owner = ? OR l.next_action_owner IS NULL)",
            (tomorrow, _user_id(user)),
        ).fetchall()
        fresh_rows = db.execute(
            "SELECT l.*, 'neu' AS resolved_status, NULL::timestamptz AS resolved_next_action_at, "
            "NULL::uuid AS resolved_next_action_owner FROM leads l WHERE l.acquisition_status = 'neu' "
            f"AND l.acquisition_status NOT IN ({terminal_sql}) "
            "AND l.dnc = false AND l.handed_over_at IS NULL AND l.unfit_manual = false "
            "AND l.last_attempt_at IS NULL AND l.next_action_at IS NULL "
            "AND l.lead_no BETWEEN ? AND ?", (lead_no_from, lead_no_to),
        ).fetchall()
    return sort_today_rows([_list_row(row, now) for row in (*action_rows, *fresh_rows)])


def list_weekly_round(conn, now):
    now = _utc(now)
    if now is None:
        raise ValueError("Ein aktueller Zeitpunkt ist erforderlich.")
    cutoff = now - timedelta(days=7)
    terminal_sql = ", ".join(f"'{status}'" for status in sorted(TERMINAL_STATUSES))
    with _transaction(conn) as db:
        rows = db.execute(
            "SELECT l.*, l.acquisition_status AS resolved_status, NULL::timestamptz AS resolved_next_action_at, "
            "NULL::uuid AS resolved_next_action_owner FROM leads l "
            f"WHERE l.acquisition_status NOT IN ({terminal_sql}) AND ((l.last_attempt_at IS NOT NULL "
            "AND l.acquisition_status = 'in_bearbeitung' "
            "AND l.next_action_at IS NULL AND l.last_reach IN ('not_reached','gatekeeper') AND l.last_attempt_at <= ?) "
            "OR (l.acquisition_status = 'in_bearbeitung' AND l.last_attempt_at IS NULL AND l.next_action_at IS NULL "
            "AND l.legacy_status = 'Nicht Erreicht' AND l.dnc = false AND l.handed_over_at IS NULL "
            "AND l.unfit_manual = false)) ORDER BY l.lead_no",
            (cutoff,),
        ).fetchall()
    return [_list_row(row, now) for row in rows]

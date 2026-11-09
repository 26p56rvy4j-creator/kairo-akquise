from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
TERMINAL_STATUSES = {"gesperrt", "verloren", "nicht_passend", "uebergeben"}

OUTCOME_OPTIONS = (
    ("nicht erreicht", "not_reached", None),
    ("falsche Nummer", "wrong_number", None),
    ("Vorzimmer: später melden", "gatekeeper", None),
    ("Vorzimmer: nicht passend", "gatekeeper", "unfit"),
    ("GF: Termin", "gf", "meeting"),
    ("GF: Follow-up", "gf", "follow_up"),
    ("GF: verloren", "gf", "lost"),
    ("GF: nicht passend", "gf", "unfit"),
)
LEGACY_STATUS = {
    "Kaltakquise anruf starten": "neu", "Nicht Erreicht": "in_bearbeitung",
    "Follow-Up": "follow_up", "Unqualifiziert": "nicht_passend",
    "Nicht Verkauft": "verloren", "Closing Call 1": "uebergeben",
}
VALID_REACH = {"not_reached", "wrong_number", "gatekeeper", "gf"}
VALID_OUTCOMES = {"meeting", "follow_up", "lost", "unfit"}
STATUS_COLORS = {
    "neu": "grau", "follow_up": "blau", "termin": "gruen", "uebergeben": "gruen",
    "verloren": "rot", "gesperrt": "rot", "nicht_passend": "lila", "nummer_pruefen": "lila",
}


def validate_attempt(data: dict) -> None:
    if not isinstance(data, dict):
        raise ValueError("Anrufdaten müssen als Objekt übergeben werden.")
    reach, outcome = data.get("reach"), data.get("outcome")
    if not isinstance(reach, str) or reach not in VALID_REACH:
        raise ValueError("Ungültiger Gesprächskontakt.")
    if outcome is not None and (not isinstance(outcome, str) or outcome not in VALID_OUTCOMES):
        raise ValueError("Ungültiges Anrufergebnis.")
    for flag in ("mail_requested", "dnc_requested"):
        if flag in data and not isinstance(data[flag], bool):
            raise ValueError(f"{flag} muss boolesch sein.")
    if reach in {"not_reached", "wrong_number"}:
        if outcome is not None or data.get("mail_requested") or data.get("dnc_requested"):
            raise ValueError("Bei diesem Kontakt sind Ergebnis, Mail und Sperre unzulässig.")
    elif reach == "gatekeeper" and outcome not in (None, "unfit"):
        raise ValueError("Beim Vorzimmer ist nur 'unfit' oder kein Ergebnis zulässig.")
    elif reach == "gf" and outcome not in VALID_OUTCOMES:
        raise ValueError("Beim Geschäftsführer ist genau ein gültiges Ergebnis erforderlich.")
    if reach == "wrong_number" and data.get("callback_at") is not None:
        raise ValueError("Bei falscher Nummer ist kein Rückruf zulässig.")
    if data.get("dnc_requested") and data.get("callback_at") is not None:
        raise ValueError("Bei einer Sperrbitte ist kein Rückruf zulässig.")
    if outcome == "follow_up" and data.get("callback_at") is None:
        raise ValueError("Follow-up benötigt einen Rückrufzeitpunkt.")
    if outcome == "meeting" and data.get("meeting_at") is None:
        raise ValueError("Ein Termin benötigt einen Terminzeitpunkt.")


def valid_attempts(attempts: list[dict]) -> list[dict]:
    return sorted((a for a in attempts if a.get("voided_at") is None),
                  key=lambda a: (a["called_at"], str(a.get("id", ""))))


def calculate_status(lead: dict, attempts: list[dict]) -> dict:
    valid = valid_attempts(attempts)
    if lead.get("dnc") or any(a.get("dnc_requested") for a in valid):
        state = "gesperrt"
    elif lead.get("handed_over_at"):
        state = "uebergeben"
    elif lead.get("unfit_manual") or any(a.get("outcome") == "unfit" for a in valid):
        state = "nicht_passend"
    else:
        gf = next((a for a in reversed(valid) if a.get("reach") == "gf"), None)
        if gf and gf.get("outcome") in {"lost", "follow_up", "meeting"}:
            state = {"lost": "verloren", "follow_up": "follow_up", "meeting": "termin"}[gf["outcome"]]
        elif valid and valid[-1].get("reach") == "wrong_number" and not (
            lead.get("phone_changed_at") and lead["phone_changed_at"] > valid[-1]["called_at"]
        ):
            state = "nummer_pruefen"
        elif valid:
            state = "in_bearbeitung"
        else:
            state = LEGACY_STATUS.get(lead.get("legacy_status"), "in_bearbeitung" if lead.get("legacy_status") else "neu")
    callback = valid[-1] if valid and valid[-1].get("callback_at") is not None else None
    next_action_at = callback.get("callback_at") if callback else None
    next_action_owner = callback.get("caller_id") if callback else None
    if not valid and lead.get("legacy_follow_up"):
        next_action_at = datetime.combine(lead["legacy_follow_up"], time.min, BERLIN)
    if state in TERMINAL_STATUSES:
        next_action_at = next_action_owner = None
    last = valid[-1] if valid else None
    return {"status": state, "next_action_at": next_action_at,
            "next_action_owner": next_action_owner,
            "last_attempt": last,
            "last_attempt_at": last["called_at"] if last else None,
            "last_reach": last["reach"] if last else None}


def status_color(status: str, last_attempt: dict | None = None) -> str:
    if status == "in_bearbeitung":
        return "orange" if last_attempt and last_attempt.get("reach") == "gatekeeper" else "gelb"
    return STATUS_COLORS.get(status, "grau")

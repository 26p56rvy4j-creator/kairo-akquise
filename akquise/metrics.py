from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")


def _local_date(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Zeitpunkte müssen eine Zeitzone enthalten.")
        return value.astimezone(BERLIN).date()
    return value


def _in_period(value, start: date, end: date) -> bool:
    return value is not None and start <= _local_date(value) <= end


def _ratio(numerator, denominator):
    return round(100 * numerator / denominator) if denominator else None


def calculate_metrics(attempts: list[dict], manual_marks: list[dict], opportunities: list[dict],
                      start: date, end: date, user_id=None) -> dict:
    """Aggregate a Berlin calendar-date interval; all rates are integer percentages."""
    if end < start:
        raise ValueError("Der Zeitraum endet vor seinem Beginn.")
    valid = [a for a in attempts if a.get("voided_at") is None]
    person = [a for a in valid if user_id is None or a.get("caller_id") == user_id]
    period = [a for a in person if _in_period(a.get("called_at"), start, end)]
    leads = {a["lead_id"] for a in period}
    reached = [a for a in period if a.get("reach") in {"gatekeeper", "gf"}]
    gf = [a for a in period if a.get("reach") == "gf"]
    first_events = {}
    for a in sorted(valid, key=lambda x: (x["called_at"], str(x.get("id", "")))):
        if a.get("outcome") in {"meeting", "lost", "unfit"}:
            first_events.setdefault((a["lead_id"], a["outcome"]), a)
    meetings = [a for (lead, outcome), a in first_events.items()
                if outcome == "meeting" and _in_period(a["called_at"], start, end)
                and (user_id is None or a.get("caller_id") == user_id)]
    losses = [a for (lead, outcome), a in first_events.items()
              if outcome == "lost" and _in_period(a["called_at"], start, end)
              and (user_id is None or a.get("caller_id") == user_id)]
    unfit_events = {}
    for (lead, outcome), a in first_events.items():
        if outcome == "unfit":
            unfit_events.setdefault(lead, a)
    for mark in manual_marks:
        lead = mark["lead_id"]
        if lead not in unfit_events or mark["marked_at"] < unfit_events[lead].get("called_at", unfit_events[lead].get("marked_at")):
            unfit_events[lead] = mark
    unfit = sum(_in_period(event.get("marked_at", event.get("called_at")), start, end)
                and (user_id is None or event.get("user_id", event.get("caller_id")) == user_id)
                for event in unfit_events.values())
    gf_leads = {a["lead_id"] for a in gf}
    unfit_leads = {lead for lead, event in unfit_events.items()
                   if _in_period(event.get("marked_at", event.get("called_at")), start, end)}
    caller_by_attempt = {a.get("id"): a.get("caller_id") for a in valid}
    held = [o for o in opportunities if _in_period(o.get("meeting_held_at"), start, end)
            and (user_id is None or o.get("caller_id", caller_by_attempt.get(o.get("source_attempt_id"))) == user_id)]
    cancelled = [a for a in attempts if a.get("voided_at") is not None and _in_period(a["voided_at"], start, end)
                 and (user_id is None or a.get("caller_id") == user_id)]
    result = {
        "attempts": len(period), "leads": len(leads), "reached": len(reached), "gf_reached": len(gf),
        "later_contact": sum(a.get("reach") == "gatekeeper" and a.get("outcome") is None for a in period),
        "follow_ups": sum(a.get("reach") == "gf" and a.get("outcome") == "follow_up" for a in period),
        "meetings": len(meetings), "lost": len(losses), "unfit": unfit,
        "mail_requested": sum(bool(a.get("mail_requested")) for a in period),
        "voided": len(cancelled), "held_meetings": len(held),
        "reachability_rate": _ratio(len(reached), len(period)),
        "decision_maker_rate": _ratio(len(gf), len(reached)),
        "meeting_rate": _ratio(len(({a["lead_id"] for a in gf} - unfit_leads) & {a["lead_id"] for a in meetings}),
                                len(gf_leads - unfit_leads)),
    }
    return result

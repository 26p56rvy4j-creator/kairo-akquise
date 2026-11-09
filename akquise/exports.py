from __future__ import annotations

import csv
from datetime import date, datetime
from io import StringIO

from akquise.permissions import can
from database.session import connection_scope


LEAD_COLUMNS = tuple("""id lead_no company_name company_name_norm website domain_norm phone_raw
phone_e164 phone_status phone_changed_at gf_name email source segment enrichment_status
enrichment_score_raw enrichment_note legacy_status legacy_note legacy_follow_up acquisition_status
next_action_at next_action_owner last_attempt_at last_reach dnc dnc_at dnc_reason unfit_manual
handed_over_at version created_at updated_at""".split())
ATTEMPT_COLUMNS = tuple("""id lead_id caller_id called_at reach outcome mail_requested dnc_requested
callback_at meeting_at note idempotency_key voided_at voided_by void_reason replaces_attempt_id
created_at""".split())


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    # Spreadsheet programs must not execute imported text as a formula.
    if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@"))
                                   or value.startswith(("\t", "\r", "\n"))):
        return "'" + value
    return value


def rows_to_csv(rows, columns):
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows({key: _csv_value(row.get(key)) for key in columns} for row in rows)
    return output.getvalue().encode("utf-8-sig")


def load_exports(engine, user):
    if not can(user, "export"):
        raise PermissionError("CSV-Export ist nur für aktive Admins erlaubt.")
    fields = ", ".join("l." + key for key in LEAD_COLUMNS)
    attempts = ", ".join("a." + key for key in ATTEMPT_COLUMNS)
    with connection_scope(engine) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        leads = db.execute(f"SELECT {fields} FROM leads l ORDER BY l.lead_no").fetchall()
        calls = db.execute(f"SELECT {attempts}, l.lead_no, u.display_name AS caller_name, "
                           "v.display_name AS voided_by_name FROM call_attempts a "
                           "JOIN leads l ON l.id=a.lead_id JOIN users u ON u.id=a.caller_id "
                           "LEFT JOIN users v ON v.id=a.voided_by ORDER BY a.called_at,a.id").fetchall()
        mail = db.execute(f"SELECT {fields} FROM leads l WHERE EXISTS "
                          "(SELECT 1 FROM call_attempts a WHERE a.lead_id=l.id "
                          "AND a.mail_requested AND a.voided_at IS NULL) ORDER BY l.lead_no").fetchall()
    return {"leads.csv": rows_to_csv(leads, LEAD_COLUMNS),
            "anrufversuche.csv": rows_to_csv(calls, (*ATTEMPT_COLUMNS, "lead_no", "caller_name", "voided_by_name")),
            "mail_gewuenscht.csv": rows_to_csv(mail, LEAD_COLUMNS)}

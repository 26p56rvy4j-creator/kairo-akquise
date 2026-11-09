from __future__ import annotations

import sys
from datetime import datetime, time, timedelta
from pathlib import Path

# Community Cloud starts this entrypoint from the repository root.
ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import streamlit as st
from sqlalchemy import create_engine

from akquise.config import get_database_url
from akquise.metrics import calculate_metrics
from akquise.permissions import can
from akquise.repository import list_today, list_weekly_round
from akquise.ui_logic import BERLIN, authorized_user
from database.session import connection_scope

st.set_page_config(page_title="Kairo Akquise", page_icon="📞", layout="wide")


@st.cache_resource
def _engine():
    return create_engine(get_database_url())


def lookup(email):
    with connection_scope(_engine()) as db:
        return db.execute("SELECT * FROM users WHERE email = ? AND active = true", (email,)).fetchone()


def main(identity=None, user_lookup=None, engine=None):
    identity = st.user if identity is None else identity
    email = identity.get("email") if isinstance(identity, dict) else getattr(identity, "email", None)
    logged_in = identity.get("is_logged_in", False) if isinstance(identity, dict) else getattr(identity, "is_logged_in", False)
    if not logged_in:
        st.title("Kairo Akquise")
        if st.button("Mit Google anmelden", type="primary"):
            st.login()
        st.stop()
    user = authorized_user(email, user_lookup or lookup)
    if not user or not user["active"]:
        st.title("Kein Zugang")
        st.button("Abmelden", on_click=st.logout)
        st.stop()
    engine = engine or _engine()
    st.sidebar.caption(user["display_name"])
    if st.sidebar.button("Abmelden"):
        st.logout()
    pages = [st.Page(lambda: work_page(engine,user,False), title="Heute", icon="📞", url_path="heute", default=True),
             st.Page(lambda: work_page(engine,user,True), title="Nachfassrunde", icon="↩️", url_path="nachfassrunde"),
             st.Page(lambda: evaluation_page(engine, user, datetime.now(BERLIN)), title="Auswertung", url_path="auswertung"),
             st.Page(lambda: leads_page(engine,user), title="Leads", icon="🔎", url_path="leads")]
    if can(user,"manage_users"):
        pages.append(st.Page(lambda: users_page(engine,user), title="Nutzer", icon="👥", url_path="nutzer"))
    st.navigation(pages).run()


def kpi_card(label, value):
    from html import escape

    return (
        '<div class="akquise-kpi-card">'
        f'<div class="akquise-kpi-label">{escape(label)}</div>'
        f'<div class="akquise-kpi-value">{escape(str(value))}</div>'
        '</div>'
    )


METRIC_COUNTS = (
    ("Anrufe", "attempts"), ("Erreicht", "reached"), ("GF erreicht", "gf_reached"),
    ("Später melden", "later_contact"), ("Mail gewünscht", "mail_requested"),
    ("Follow-ups", "follow_ups"), ("Termine", "meetings"), ("Verloren", "lost"),
    ("Nicht passend", "unfit"),
)
METRIC_RATES = (
    ("Erreichbarkeit", "reachability_rate"), ("GF-Quote", "decision_maker_rate"),
    ("Terminquote", "meeting_rate"),
)


def metric_person(engine, user, key):
    with connection_scope(engine) as db:
        users = db.execute("SELECT id, display_name FROM users WHERE active ORDER BY display_name, id").fetchall()
    labels = {row["id"]: row["display_name"] for row in users}
    labels["both"] = "Beide"
    selected = st.segmented_control(
        "Person", list(labels), default=user["id"] if user["id"] in labels else "both",
        format_func=labels.__getitem__, key=key, label_visibility="collapsed",
    )
    return None if selected in (None, "both") else selected


def metric_history(engine, end):
    cutoff = datetime.combine(end + timedelta(days=1), time.min, BERLIN)
    with connection_scope(engine) as db:
        # Earlier attempts and marks establish the first event per lead.
        attempts = db.execute(
            "SELECT id, lead_id, caller_id, called_at, reach, outcome, mail_requested, voided_at "
            "FROM call_attempts WHERE called_at < ?", (cutoff,),
        ).fetchall()
        marks = db.execute(
            "SELECT lead_id, user_id, changed_at AS marked_at FROM lead_changes "
            "WHERE field = 'unfit_manual' AND new_value = 'true'::jsonb AND changed_at < ?", (cutoff,),
        ).fetchall()
    return attempts, marks


def metric_cards(totals):
    st.html("""
        <style>
        .akquise-kpi-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:.45rem; }
        .akquise-kpi-grid + .akquise-kpi-grid { margin-top:.55rem; }
        .akquise-kpi-card { background:var(--secondary-background-color); border:1px solid rgba(128,128,128,.3);
            border-radius:8px; padding:.45rem .65rem; }
        .akquise-kpi-label { font-size:.82rem; opacity:.8; }
        .akquise-kpi-value { font-size:1.38rem; font-weight:650; line-height:1.15; }
        @media (max-width:600px) { .akquise-kpi-grid { grid-template-columns:minmax(0,1fr); } }
        </style>
    """ + '<div class="akquise-kpi-grid">' +
        "".join(kpi_card(label, totals[key]) for label, key in METRIC_COUNTS) +
        '</div><div class="akquise-kpi-grid">' +
        "".join(kpi_card(label, "–" if totals[key] is None else f"{totals[key]}%") for label, key in METRIC_RATES) +
        '</div>')


def daily_summary(engine, user, now):
    day = now.astimezone(BERLIN).date()
    with st.expander("Tagesbilanz"):
        person = metric_person(engine, user, "daily_metric_person")
        st.subheader(day.strftime("%d.%m.%Y"))
        attempts, marks = metric_history(engine, day)
        metric_cards(calculate_metrics(attempts, marks, [], day, day, person))


def evaluation_page(engine, user, now):
    import pandas as pd

    st.title("Auswertung")
    person = metric_person(engine, user, "weekly_metric_person")
    today = now.astimezone(BERLIN).date()
    choice = st.segmented_control(
        "Woche", ("Vorwoche", "Diese Woche", "Datum"), default="Diese Woche",
    )
    selected = today
    if choice == "Vorwoche":
        selected -= timedelta(days=7)
    elif choice == "Datum":
        selected = st.date_input("Datum", today, format="DD.MM.YYYY") or today
    monday = selected - timedelta(days=selected.weekday())
    sunday = monday + timedelta(days=6)
    attempts, marks = metric_history(engine, sunday)
    st.subheader(f"Wochensumme · {monday:%d.%m.%Y}–{sunday:%d.%m.%Y}")
    metric_cards(calculate_metrics(attempts, marks, [], monday, sunday, person))

    rows = []
    for offset, weekday in enumerate(("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")):
        day = monday + timedelta(days=offset)
        totals = calculate_metrics(attempts, marks, [], day, day, person)
        rows.append({"Tag": f"{weekday}, {day:%d.%m.%Y}", **{label: totals[key] for label, key in METRIC_COUNTS}})
    frame = pd.DataFrame(rows)
    st.dataframe(frame, hide_index=True)
    st.subheader("Aktivität pro Tag")
    st.bar_chart(frame, x="Tag", y=["Anrufe", "Erreicht", "GF erreicht"], sort=False, stack=False)
    st.subheader("Ergebnisse pro Tag")
    st.bar_chart(frame, x="Tag", y=["Termine", "Follow-ups", "Verloren"], sort=False, stack=False)


def work_page(engine,user,weekly):
    import pandas as pd
    from datetime import datetime
    from akquise.repository import record_attempt
    from akquise.ui_logic import WORK_RESULTS, status_label, save_rows
    title="Nachfassrunde" if weekly else "Heute"
    st.title(title)
    feedback=st.session_state.pop(f"call_feedback_{weekly}",None)
    if feedback:
        st.info(f"{feedback['saved']} gespeichert, {len(feedback['errors'])} Fehler")
        for no,error in feedback["errors"]: st.error(f"Lead Nr. {no}: {error}")
        for no in feedback["warnings"]: st.warning(f"Doppelanruf-Hinweis: Lead {no}")
    if not weekly:
        daily_summary(engine, user, datetime.now(BERLIN))
    if weekly:
        with connection_scope(engine) as db: rows=list_weekly_round(db,datetime.now(BERLIN))
    else:
        a, b = st.columns(2)
        start = a.number_input("Lead-Nr. von", min_value=1, value=1, key="today_range_from")
        end = b.number_input("Lead-Nr. bis", min_value=int(start), value=max(40, int(start)), key="today_range_to")
        with connection_scope(engine) as db: rows=list_today(db,user,start,end,datetime.now(BERLIN))
    if not rows: st.info("Keine Leads in dieser Liste."); return
    compact=st.toggle("Kompaktansicht",key=f"compact_{weekly}")
    data=[]
    for r in rows:
        d=dict(r); at=d.get("next_action_at")
        data.append({"id":d["id"],"lead_no":d["lead_no"],"status":status_label(d["acquisition_status"],d.get("last_reach")),
                     "due":(at.astimezone(BERLIN).strftime("%H:%M") + (" · jetzt" if d["is_due"] else " · später")) if at else "",
                     "company_name":d["company_name"],"gf_name":d.get("gf_name") or "","phone_e164":d.get("phone_e164") or "",
                     "website":d.get("website") or "","last_attempt":d.get("last_attempt_at"),"legacy_note":(d.get("legacy_note") or "")[:140],
                     "result":"","date":None,"note":""})
    frame=pd.DataFrame(data).set_index("id",drop=False)
    frame["date"] = pd.to_datetime(frame["date"])
    with st.form(f"calls_{weekly}", clear_on_submit=True):
        st.caption("Ergebnis wählen, bei Follow-up und Termin ein Datum, dann Speichern.")
        submitted_top = st.form_submit_button("Speichern", type="primary", key=f"save_top_{weekly}")
        config={"status":st.column_config.TextColumn("Status",disabled=True),"due":st.column_config.TextColumn("Fällig",disabled=True),
                "company_name":st.column_config.TextColumn("Firma",disabled=True),"gf_name":st.column_config.TextColumn("GF",disabled=True),
                "phone_e164":st.column_config.TextColumn("Telefon",disabled=True),"website":st.column_config.LinkColumn("Website",disabled=True,display_text="Öffnen"),
                "lead_no":st.column_config.NumberColumn("Nr.",disabled=True),
                "result":st.column_config.SelectboxColumn("Ergebnis ▾",options=["", *WORK_RESULTS]),
                "date":st.column_config.DatetimeColumn("Datum",format="DD.MM.YYYY HH:mm"),
                "note":st.column_config.TextColumn("Notiz"),"last_attempt":st.column_config.DatetimeColumn("Letzter Versuch",disabled=True),
                "legacy_note":st.column_config.TextColumn("Altnotiz",disabled=True)}
        order = ["lead_no", "status", "company_name", "gf_name", "phone_e164", "result", "date", "note"]
        if not compact:
            order += ["due", "website", "last_attempt", "legacy_note"]
        edited=st.data_editor(frame,column_config=config,column_order=order,hide_index=True,use_container_width=True,key=f"editor_{weekly}")
        submitted_bottom = st.form_submit_button("Speichern", type="primary", key=f"save_bottom_{weekly}")
    if submitted_top or submitted_bottom:
        result=save_rows(edited.to_dict("records"),user,lambda u,l,p,k,n:record_attempt(engine,u,l,p,k,n),st.session_state,datetime.now(BERLIN))
        st.session_state[f"call_feedback_{weekly}"]={"saved":len(result[0]),"errors":result[1],"warnings":result[2]}
        st.rerun()


def leads_page(engine,user):
    st.title("Leads")
    if can(user, "export"):
        with st.expander("CSV-Export (Admin)"):
            if st.button("CSV-Exporte vorbereiten"):
                from akquise.exports import load_exports
                labels = {"leads.csv": "Alle Leads", "anrufversuche.csv": "Alle Anrufversuche inkl. stornierter",
                          "mail_gewuenscht.csv": "Leads mit Mailwunsch"}
                for filename, data in load_exports(engine, user).items():
                    st.download_button(labels[filename], data, file_name=filename,
                                       mime="text/csv", on_click="ignore")
    feedback=st.session_state.pop("lead_action_feedback",None)
    if feedback: st.error(feedback)
    term=st.text_input("Suche: Nr., Firma, GF, Telefon oder Domain")
    with connection_scope(engine) as db:
        statuses=[r["acquisition_status"] for r in db.execute("SELECT DISTINCT acquisition_status FROM leads ORDER BY acquisition_status").fetchall()]
        sources=[r["source"] for r in db.execute("SELECT DISTINCT source FROM leads WHERE source IS NOT NULL ORDER BY source").fetchall()]
    filters=st.columns(3)
    status_filter=filters[0].selectbox("Status",["Alle",*statuses])
    source_filter=filters[1].selectbox("Quelle",["Alle",*sources])
    mail_filter=filters[2].selectbox("E-Mail",["Alle","Vorhanden","Fehlt"])
    clauses=["cast(lead_no as text) ILIKE ? OR company_name ILIKE ? OR coalesce(gf_name,'') ILIKE ? OR coalesce(phone_raw,'') ILIKE ? OR coalesce(domain_norm,'') ILIKE ?"]
    params=[f"%{term}%"]*5
    if status_filter!="Alle": clauses.append("acquisition_status = ?"); params.append(status_filter)
    if source_filter!="Alle": clauses.append("source = ?"); params.append(source_filter)
    if mail_filter=="Vorhanden": clauses.append("email IS NOT NULL AND trim(email) <> ''")
    if mail_filter=="Fehlt": clauses.append("(email IS NULL OR trim(email) = '')")
    with connection_scope(engine) as db:
        rows=db.execute("SELECT * FROM leads WHERE ("+" AND ".join(f"({c})" for c in clauses)+") ORDER BY lead_no LIMIT 100",params).fetchall()
    if not rows: st.info("Keine Treffer."); return
    selected=st.selectbox("Lead",rows,format_func=lambda r:f"{r['lead_no']} · {r['company_name']}")
    st.write(selected["company_name"],selected.get("phone_e164"),selected.get("website"))
    st.caption(f"Status: {selected['acquisition_status']}")
    st.caption("KI-Einschätzung, unbestätigt")
    st.write(selected.get("enrichment_note") or "",selected.get("legacy_note") or "")
    with connection_scope(engine) as db:
        attempts=db.execute("SELECT a.*,u.display_name,v.display_name AS voided_by_name FROM call_attempts a "
                            "JOIN users u ON u.id=a.caller_id LEFT JOIN users v ON v.id=a.voided_by "
                            "WHERE a.lead_id=? ORDER BY a.called_at DESC",(selected["id"],)).fetchall()
        contacts=db.execute("SELECT * FROM lead_contacts WHERE lead_id=? ORDER BY is_primary DESC,name",(selected["id"],)).fetchall()
    st.subheader("Kontakte"); st.dataframe([dict(x) for x in contacts],hide_index=True)
    st.subheader("Historie")
    for a in attempts:
        line=f"{a['called_at']} · {a['display_name']} · {a['reach']} · {a.get('outcome') or ''}"
        if a.get("voided_at"):
            line+=f" · storniert {a['voided_at']} von {a.get('voided_by_name') or 'unbekannt'} · Grund: {a.get('void_reason') or ''}"
        st.write(line)
    with st.form("lead_actions"):
        voidable=[a for a in attempts if a.get("voided_at") is None]
        attempt=st.selectbox("Versuch stornieren",[None,*voidable],format_func=lambda x:"—" if x is None else str(x["called_at"]))
        reason=st.text_input("Stornogrund")
        phone=st.text_input("Telefon korrigieren",value=selected.get("phone_raw") or "")
        unfit=st.checkbox("Als nicht passend markieren")
        dnc=st.checkbox("Bitte nicht mehr anrufen", disabled=bool(selected.get("dnc")))
        submitted=st.form_submit_button("Änderungen speichern")
    if submitted:
        from akquise.repository import void_attempt,update_lead
        try:
            changes={}
            if phone != (selected.get("phone_raw") or ""): changes["phone_raw"]=phone
            if unfit: changes["unfit_manual"]=True
            if dnc:
                from datetime import datetime
                changes.update(dnc=True, dnc_at=datetime.now(BERLIN), dnc_reason="Bitte nicht mehr anrufen")
            if attempt and changes:
                raise ValueError("Bitte Versuchsstorno und Leadänderung nacheinander speichern.")
            if attempt: void_attempt(engine,user,attempt["id"],reason)
            elif changes: update_lead(engine,user,selected["id"],selected["version"],changes,"Änderung über Leadansicht")
            st.success("Gespeichert."); st.rerun()
        except Exception as exc:
            st.session_state["lead_action_feedback"]=str(exc)
            st.rerun()


def users_page(engine,user):
    st.title("Nutzer verwalten")
    with st.form("users"):
        email=st.text_input("E-Mail"); name=st.text_input("Name"); role=st.selectbox("Rolle",["member","admin"]); active=st.checkbox("Aktiv",True)
        if st.form_submit_button("Speichern"):
            from akquise.manage_users import save_user
            try:
                save_user(email,name,role,active)
                st.success("Nutzer gespeichert.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    with connection_scope(engine) as db: st.dataframe(db.execute("SELECT email,display_name,role,active FROM users ORDER BY email").fetchall(),hide_index=True)


if __name__ == "__main__": main()

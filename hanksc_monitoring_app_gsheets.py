"""HANK SC Athlete Monitoring. Single-file, backwards-compatible update.

Run: streamlit run hanksc_monitoring_app_gsheets.py
Keep existing dependencies, Streamlit secrets and the Google spreadsheet unchanged.
Both legacy worksheets remain in use. No deletion, overwrite or schema migration.
"""
from __future__ import annotations

import hmac
from datetime import date, timedelta
from html import escape
from pathlib import Path
from typing import Any, Dict, List

import gspread
import pandas as pd
import plotly.express as px
import streamlit as st
from google.oauth2.service_account import Credentials

APP_TITLE = "HANK SC Athlete Monitoring"
READINESS_SHEET = "readiness_log"
SESSION_SHEET = "session_log"
READINESS_COLUMNS = [
    "timestamp", "date", "athlete", "context", "sleep_hours", "sleep_quality", "energy",
    "soreness", "stress", "motivation", "general_pain", "pain_location", "confidence",
    "injury_focus", "running_pain", "acceleration_pain", "cutting_pain", "jump_landing_pain",
    "morning_stiffness", "notes", "readiness_score", "flag",
]
SESSION_COLUMNS = [
    "timestamp", "date", "athlete", "session_type", "completed", "duration_min",
    "session_rpe", "session_load", "pain_during", "pain_after", "best_part", "issue_notes", "flag",
]
st.set_page_config(page_title=APP_TITLE, page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

CSS = """
<style>
:root {color-scheme:dark}
[data-testid="stAppViewContainer"] {background:#0c120e;color:#eef3ec}
[data-testid="stHeader"] {background:#0c120eee}
[data-testid="stSidebar"] {background:#131d16;border-right:1px solid #344536}
[data-testid="stMainBlockContainer"],.main .block-container {max-width:1180px;padding-top:2rem;padding-bottom:3rem}
[data-testid="stAppViewContainer"] h1,[data-testid="stAppViewContainer"] h2,[data-testid="stAppViewContainer"] h3 {color:#f2f5f1;letter-spacing:-.035em}
[data-testid="stAppViewContainer"] p,[data-testid="stAppViewContainer"] label,[data-testid="stAppViewContainer"] [data-testid="stCaptionContainer"] {color:#d4dfd1}
[data-testid="stSidebar"] p,[data-testid="stSidebar"] label {color:#e6ecdf}
a {color:#add0ac}
.hank-hero {padding:1.65rem 1.8rem;border:1px solid #425b48;background:linear-gradient(125deg,#1c3021,#101713);margin-bottom:1.4rem}
.hank-kicker {font:11px monospace;letter-spacing:.16em;text-transform:uppercase;color:#add0ac;margin-bottom:.65rem}
.hank-hero h1 {font-size:clamp(1.8rem,4vw,2.8rem);line-height:1.05;margin:0 0 .7rem}
.hank-hero p {font-size:1rem;line-height:1.6;max-width:790px;margin:0}
.hank-principle {padding:1rem 1.2rem;border-left:3px solid #76a078;background:#18251b;font-size:1.1rem;font-weight:650;margin:1rem 0}
[data-testid="stForm"] {border:1px solid #425b48;background:#111b14;padding:1.3rem}
[data-testid="stMetric"] {background:#e6ecdf;border:1px solid #b8c9ae;padding:1rem}
[data-testid="stMetric"] [data-testid="stMetricLabel"] p,[data-testid="stMetric"] [data-testid="stMetricValue"] {color:#15251a!important}
[data-testid="stTextInput"] input,[data-testid="stNumberInput"] input,[data-testid="stTextArea"] textarea {background:#1b281e;color:#f2f5f1}
[data-baseweb="select"]>div {background:#1b281e;color:#f2f5f1}
[data-testid="stButton"] button,[data-testid="stFormSubmitButton"] button,[data-testid="stDownloadButton"] button {background:#76a078;color:#0b140d;border:1px solid #91b38a;font-weight:650}
[data-testid="stButton"] button p,[data-testid="stFormSubmitButton"] button p,[data-testid="stDownloadButton"] button p {color:#0b140d!important}
[data-testid="stExpander"] {border-color:#425b48}
@media(max-width:640px){.hank-hero{padding:1.2rem}[data-testid="stForm"]{padding:.8rem}}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)
LOGO_PATH = Path(__file__).resolve().parent / "assets" / "hank_sc_logo.png"
if LOGO_PATH.is_file():
    if hasattr(st, "logo"):
        st.logo(str(LOGO_PATH), size="large", link="https://hanksc.com")
    else:
        st.sidebar.image(str(LOGO_PATH), width=180)


def hero(title: str, subtitle: str) -> None:
    st.markdown(
        '<div class="hank-hero"><div class="hank-kicker">HANK SC / DAILY CONTEXT</div>'
        f'<h1>{escape(title)}</h1><p>{escape(subtitle)}</p></div>', unsafe_allow_html=True,
    )


def normalize_name(name: str) -> str:
    return " ".join(str(name).strip().split())


def get_coach_password() -> str:
    # No publicly predictable fallback password.
    return str(st.secrets.get("COACH_PASSWORD", ""))


def get_client_codes() -> Dict[str, str]:
    return {normalize_name(k).lower(): str(v).strip()
            for k, v in dict(st.secrets.get("CLIENT_CODES", {})).items()}


def validate_client(athlete: str, code: str) -> bool:
    expected = get_client_codes().get(normalize_name(athlete).lower(), "")
    return bool(expected) and hmac.compare_digest(expected.encode(), str(code).strip().encode())


def authenticated_athlete() -> str:
    if st.session_state.get("hank_role") != "athlete":
        return ""
    return str(st.session_state.get("hank_athlete", ""))


def require_athlete() -> str:
    athlete = authenticated_athlete()
    if not athlete:
        st.error("Please sign in before submitting or viewing data.")
        st.stop()
    return athlete


@st.cache_resource(ttl=600)
def get_gsheet_client():
    info = dict(st.secrets["gcp_service_account"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    return gspread.authorize(Credentials.from_service_account_info(info, scopes=scopes))


def get_worksheet(sheet_name: str):
    return get_gsheet_client().open_by_key(st.secrets["GOOGLE_SHEET_ID"]).worksheet(sheet_name)


def checked_headers(worksheet, columns: List[str]) -> List[str]:
    headers = worksheet.row_values(1)
    if not headers or len(set(headers)) != len(headers) or any(not h for h in headers):
        raise ValueError("Empty or duplicate worksheet headers")
    if any(col not in headers for col in columns):
        raise ValueError("Required legacy worksheet headers are missing")
    return headers


@st.cache_data(ttl=30)
def load_sheet_data(sheet_name: str, columns: List[str]) -> pd.DataFrame:
    try:
        ws = get_worksheet(sheet_name)
        headers = checked_headers(ws, columns)
        df = pd.DataFrame(ws.get_all_records())
        if df.empty:
            return pd.DataFrame(columns=headers)
        # Keep any additional existing columns in exports too.
        return df.reindex(columns=headers)
    except Exception:
        st.error(f"Cannot read {sheet_name}. Check the spreadsheet ID, sharing permissions and original headers. No data was changed.")
        st.stop()


def append_sheet_row(sheet_name: str, columns: List[str], row: Dict[str, Any]) -> None:
    """Append only, mapping the actual existing headers; safe retry in this login session.

    A timestamp+athlete match prevents duplicate writes after an uncertain response.
    Google Sheets does not provide a cross-worksheet transaction or concurrency lock.
    """
    ws = get_worksheet(sheet_name)
    headers = checked_headers(ws, columns)
    timestamp_column = headers.index("timestamp") + 1
    athlete_column = headers.index("athlete") + 1
    for index, value in enumerate(ws.col_values(timestamp_column)[1:], start=2):
        if value == row["timestamp"]:
            if normalize_name(ws.cell(index, athlete_column).value).lower() == normalize_name(row["athlete"]).lower():
                return
    values = ["" if row.get(col) is None else row.get(col, "") for col in headers]
    # RAW keeps free text literal, including strings starting with '='.
    ws.append_row(values, value_input_option="RAW")
    st.cache_data.clear()


def readiness_score(sleep_quality: int, energy: int, soreness: int, stress: int, motivation: int, general_pain: int, confidence: int) -> int:
    # Unchanged from the original: keep historical scores comparable.
    positive = sleep_quality + energy + motivation + confidence
    negative = soreness + stress + (general_pain / 2)
    raw = (positive / 20) * 70 + ((15 - negative) / 15) * 30
    return int(max(0, min(100, round(raw))))


def readiness_flag(score: int, pain: int, sleep_quality: int, energy: int, soreness: int, stress: int, confidence: int) -> str:
    if pain >= 5 or score < 45 or confidence <= 2:
        return "RED"
    if pain >= 3 or score < 65 or sleep_quality <= 2 or energy <= 2 or soreness >= 4 or stress >= 4:
        return "YELLOW"
    return "GREEN"


def session_flag(pain_during: int, pain_after: int, rpe: int) -> str:
    if pain_during >= 5 or pain_after >= 5:
        return "RED"
    if pain_during >= 3 or pain_after >= 3 or rpe >= 9:
        return "YELLOW"
    return "GREEN"


def parse_dates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    return out.dropna(subset=["date"])


def filter_by_athlete_and_dates(df: pd.DataFrame, athlete: str | None, start: date, end: date) -> pd.DataFrame:
    out = parse_dates(df)
    if athlete and athlete != "All athletes":
        out = out[out["athlete"].astype(str).map(normalize_name).str.lower() == normalize_name(athlete).lower()]
    return out[(out["date"].dt.date >= start) & (out["date"].dt.date <= end)].sort_values(["date", "timestamp"])


def add_derived_columns(readiness: pd.DataFrame, sessions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    r, s = readiness.copy(), sessions.copy()
    for frame, cols in [(r, ["sleep_hours", "sleep_quality", "energy", "soreness", "stress", "motivation", "general_pain", "confidence", "running_pain", "acceleration_pain", "cutting_pain", "jump_landing_pain", "morning_stiffness", "readiness_score"]),
                        (s, ["duration_min", "session_rpe", "session_load", "pain_during", "pain_after"])]:
        for col in cols:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if not r.empty:
        # Calendar-day average, not the last seven submissions.
        r["readiness_7d_avg"] = float("nan")
        for _, group in r.groupby("athlete"):
            daily = group.groupby("date")["readiness_score"].mean().sort_index()
            average = daily.rolling("7D", min_periods=1).mean()
            r.loc[group.index, "readiness_7d_avg"] = group["date"].map(average)
    s["week"] = s["date"].dt.to_period("W").astype(str)
    return r, s


def finish_pending_save() -> None:
    pending = st.session_state.get("hank_pending")
    if not pending or pending["athlete"] != authenticated_athlete():
        return
    for item in pending["items"]:
        if item["done"]:
            continue
        try:
            append_sheet_row(item["sheet"], item["columns"], item["row"])
            item["done"] = True
        except Exception:
            st.session_state["hank_pending"] = pending
            st.cache_data.clear()
            return
    st.session_state["hank_saved"] = {"score": pending["score"], "flag": pending["flag"]}
    del st.session_state["hank_pending"]


def pending_save_notice() -> bool:
    pending = st.session_state.get("hank_pending")
    if not pending:
        return False
    if pending["athlete"] != authenticated_athlete():
        return False
    st.warning("Your check-in is not fully confirmed. One part may already be saved. Retry this same check-in instead of submitting another one. Keep this tab open until confirmed.")
    if st.button("Retry saving this check-in", key="retry_pending"):
        finish_pending_save()
        st.rerun()
    return True


def show_readiness_form() -> None:
    athlete = require_athlete()
    hero("Daily Readiness", "One check-in. How you feel today, plus a short review of your latest training if you have something to share.")
    if pending_save_notice():
        return
    saved = st.session_state.pop("hank_saved", None)
    if saved:
        st.success(f"Check-in saved. Readiness {saved['score']}/100. Thank you, {athlete}.")
    st.caption("Readiness is a coaching signal, not a diagnosis or permission to train through pain.")
    include_session = st.checkbox("Add a quick training review", value=False, key="include_session")
    include_symptoms = st.checkbox("Add movement-specific symptoms", value=False, key="include_symptoms")
    with st.form("daily_readiness_form", clear_on_submit=False):
        st.subheader("01 / How are you today?")
        left, right = st.columns(2)
        with left:
            log_date = st.date_input("Check-in date", value=date.today(), max_value=date.today())
            sleep_hours = st.number_input("Sleep, hours", min_value=0.0, max_value=14.0, value=7.0, step=0.25)
            sleep_quality = st.slider("Sleep quality / 1 poor, 5 great", 1, 5, 3)
            energy = st.slider("Energy / 1 low, 5 high", 1, 5, 3)
            motivation = st.slider("Motivation / 1 low, 5 high", 1, 5, 3)
        with right:
            context = st.selectbox("Today's context", ["Normal training day", "Recovery day", "Match day", "Travel day", "Rest day"])
            soreness = st.slider("Muscle soreness / 1 none, 5 high", 1, 5, 2)
            stress = st.slider("Stress / 1 low, 5 high", 1, 5, 2)
            general_pain = st.slider("Current pain or discomfort / 0 none, 10 worst", 0, 10, 0)
            confidence = st.slider("Confidence to train / 1 low, 5 high", 1, 5, 4)
        pain_location = st.text_input("Where do you feel discomfort? (optional)")
        injury_focus = "General"
        symptoms = {k: None for k in ["running_pain", "acceleration_pain", "cutting_pain", "jump_landing_pain", "morning_stiffness"]}
        if include_symptoms:
            with st.expander("Movement-specific symptoms / optional", expanded=True):
                injury_focus = st.selectbox("Area we are monitoring", ["General", "Hamstring", "Knee / ACL", "Achilles", "Shoulder", "Back", "Other"])
                st.caption("Leave a movement blank if you did not try it. Blank is not zero pain.")
                for key, label in [("running_pain", "Running"), ("acceleration_pain", "Acceleration"), ("cutting_pain", "Cutting / change of direction"), ("jump_landing_pain", "Jumping / landing"), ("morning_stiffness", "Morning stiffness")]:
                    value = st.selectbox(label, ["Not assessed"] + list(range(11)), key=f"symptom_{key}")
                    symptoms[key] = None if value == "Not assessed" else value
        session = None
        if include_session:
            st.subheader("02 / Your latest training")
            st.caption("Optional. Rate the whole session here; load in this dashboard is duration × session RPE, not a set-by-set RIR or velocity measure.")
            a, b = st.columns(2)
            with a:
                session_date = st.date_input("Training date", value=min(log_date, date.today() - timedelta(days=1)), max_value=date.today())
                session_type = st.selectbox("Training type", ["Strength", "Running / Sprint", "Conditioning", "Mobility", "Recovery", "Match / Game", "Technical", "Other"])
                completed = st.selectbox("Completed?", ["Yes", "Partially", "No"])
                duration_min = st.number_input("Duration, minutes", min_value=0, max_value=300, value=60, step=5)
            with b:
                session_rpe = st.slider("RPE / Rating of Perceived Exertion", 0, 10, 6)
                pain_during = st.slider("Pain during training / 0-10", 0, 10, 0)
                pain_after = st.slider("Pain afterwards / 0-10", 0, 10, 0)
                issue_notes = st.text_input("Training feedback (optional)")
            session = {"date": str(session_date), "athlete": athlete, "session_type": session_type,
                       "completed": completed, "duration_min": duration_min, "session_rpe": session_rpe,
                       "session_load": int(duration_min * session_rpe), "pain_during": pain_during,
                       "pain_after": pain_after, "best_part": "", "issue_notes": issue_notes,
                       "flag": session_flag(pain_during, pain_after, session_rpe)}
        notes = st.text_area("Anything else I should know? (optional)", height=80)
        submitted = st.form_submit_button("Save my daily check-in", use_container_width=True)
    if submitted:
        if session and session_date > log_date:
            st.error("The training review cannot be later than your check-in date. Adjust the training date.")
            return
        if session and session["completed"] == "No" and session["duration_min"] > 0:
            st.error("For a session you did not start, set duration to 0. Choose Partially if you did some training.")
            return
        score = readiness_score(sleep_quality, energy, soreness, stress, motivation, general_pain, confidence)
        flag = readiness_flag(score, general_pain, sleep_quality, energy, soreness, stress, confidence)
        timestamp = pd.Timestamp.now(tz="UTC").isoformat()
        row = {"timestamp": timestamp, "date": str(log_date), "athlete": athlete, "context": context,
               "sleep_hours": sleep_hours, "sleep_quality": sleep_quality, "energy": energy,
               "soreness": soreness, "stress": stress, "motivation": motivation, "general_pain": general_pain,
               "pain_location": pain_location, "confidence": confidence, "injury_focus": injury_focus,
               **symptoms, "notes": notes, "readiness_score": score, "flag": flag}
        items = [{"sheet": READINESS_SHEET, "columns": READINESS_COLUMNS, "row": row, "done": False}]
        if session:
            session["timestamp"] = timestamp
            items.append({"sheet": SESSION_SHEET, "columns": SESSION_COLUMNS, "row": session, "done": False})
        st.session_state["hank_pending"] = {"athlete": athlete, "items": items, "score": score, "flag": flag}
        finish_pending_save()
        st.rerun()


def plot_line(df: pd.DataFrame, y: str, title: str, y_label: str) -> None:
    if df.empty or y not in df or df[y].dropna().empty:
        st.info(f"No entries yet for {title.lower()}.")
        return
    fig = px.line(df, x="date", y=y, color="athlete" if df["athlete"].nunique() > 1 else None,
                  markers=True, title=title, color_discrete_sequence=["#add0ac", "#d3ba72", "#8bb2b9"])
    fig.update_layout(template="plotly_dark", paper_bgcolor="#111b14", plot_bgcolor="#111b14",
                      font_color="#e6ecdf", xaxis_title="", yaxis_title=y_label, hovermode="x unified",
                      margin=dict(l=15, r=15, t=50, b=25))
    st.plotly_chart(fig, use_container_width=True)


def metric_value(value, suffix="") -> str:
    return "Not logged" if pd.isna(value) else f"{float(value):g}{suffix}"


def safe_csv(df: pd.DataFrame) -> bytes:
    copy = df.copy()
    for col in copy.select_dtypes(include=["object", "string"]).columns:
        copy[col] = copy[col].map(lambda v: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v)
    return copy.to_csv(index=False).encode("utf-8")


def show_dashboard(athlete_restricted: str | None = None) -> None:
    if athlete_restricted:
        if athlete_restricted != require_athlete():
            st.stop()
    elif st.session_state.get("hank_role") != "coach":
        st.stop()
    hero("My training context" if athlete_restricted else "Coach Command Center",
         "Readiness, recovery and training feedback. Look for patterns, then put them in context.")
    r0 = parse_dates(load_sheet_data(READINESS_SHEET, READINESS_COLUMNS))
    s0 = parse_dates(load_sheet_data(SESSION_SHEET, SESSION_COLUMNS))
    selected = athlete_restricted
    if not selected:
        names = sorted(set(r0["athlete"].dropna().astype(str)) | set(s0["athlete"].dropna().astype(str)))
        selected = st.sidebar.selectbox("Athlete", ["All athletes"] + [x for x in names if x.strip()])
    start = st.sidebar.date_input("From", value=date.today() - timedelta(days=30))
    end = st.sidebar.date_input("To", value=date.today())
    if start > end:
        st.error("The start date must be before the end date.")
        return
    # Derive rolling averages before clipping the visible range.
    r, s = add_derived_columns(r0, s0)
    r = filter_by_athlete_and_dates(r, selected, start, end)
    s = filter_by_athlete_and_dates(s, selected, start, end)
    if r.empty and s.empty:
        st.info("No check-ins in this date range yet.")
        return
    cards = st.columns(4)
    latest = r.iloc[-1] if not r.empty else {}
    cards[0].metric("Latest readiness", metric_value(latest.get("readiness_score", float("nan")), "/100"))
    cards[1].metric("Latest discomfort", metric_value(latest.get("general_pain", float("nan")), "/10"))
    week = s[s["date"].dt.date >= end - timedelta(days=6)]
    cards[2].metric("Load / last 7 days", metric_value(week["session_load"].sum(), " AU"))
    cards[3].metric("Signals to review", int((r["flag"] == "RED").sum() + (s["flag"] == "RED").sum()))
    st.caption("Summary reflects the selected athlete(s) and date range. For All athletes, latest values belong to the latest entry, not a team average. Flags are original coaching rules, not validated clinical thresholds.")
    tabs = st.tabs(["Readiness & recovery", "Training feedback", "Data & exports"])
    with tabs[0]:
        a, b = st.columns(2)
        with a:
            plot_line(r, "readiness_score", "Readiness", "Score / 100")
            plot_line(r, "sleep_hours", "Sleep", "Hours")
        with b:
            plot_line(r, "readiness_7d_avg", "Readiness / 7 calendar days", "Score / 100")
            plot_line(r, "general_pain", "Discomfort", "0-10")
        with st.expander("Movement-specific symptoms"):
            for key, title in [("running_pain", "Running"), ("acceleration_pain", "Acceleration"), ("cutting_pain", "Change of direction"), ("jump_landing_pain", "Jumping / landing"), ("morning_stiffness", "Morning stiffness")]:
                plot_line(r, key, title, "0-10")
        st.subheader("Latest notes")
        st.dataframe(r[["date", "athlete", "flag", "notes"]].sort_values("date", ascending=False).head(10), use_container_width=True, hide_index=True)
    with tabs[1]:
        st.caption("Historical session logs are preserved. New feedback comes from the optional training review inside Daily Readiness.")
        a, b = st.columns(2)
        with a:
            plot_line(s, "session_rpe", "Session effort", "RPE / 10")
            plot_line(s, "session_load", "Training load", "Minutes × RPE / AU")
        with b:
            plot_line(s, "pain_after", "Discomfort after training", "0-10")
            plot_line(s, "duration_min", "Training duration", "Minutes")
        st.dataframe(s.sort_values("date", ascending=False), use_container_width=True, hide_index=True)
    with tabs[2]:
        st.caption("Exports contain only the selected athlete(s) and date range. Older entries and additional sheet columns remain intact.")
        for title, df, filename in [("Readiness", r, "hanksc_readiness_export.csv"), ("Training feedback", s, "hanksc_session_export.csv")]:
            st.subheader(title)
            st.download_button(f"Download {title.lower()} CSV", data=safe_csv(df), file_name=filename,
                               mime="text/csv", use_container_width=True)
            st.dataframe(df.sort_values("date", ascending=False), use_container_width=True, hide_index=True)


def show_login() -> None:
    hero("Your day. Your context.", "One sign-in for your daily check-in and your own training trends. Honest feedback helps us make better decisions.")
    st.markdown('<div class="hank-principle">Training only makes sense when you can recover from it.</div>', unsafe_allow_html=True)
    mode = st.radio("Access", ["Athlete", "Coach"], horizontal=True)
    with st.form("login"):
        if mode == "Athlete":
            athlete = normalize_name(st.text_input("Athlete name", help="Use the same name as before."))
            code = st.text_input("Access code", type="password")
        else:
            password = st.text_input("Coach password", type="password")
        submitted = st.form_submit_button("Sign in", use_container_width=True)
    if submitted:
        if mode == "Athlete":
            if not get_client_codes():
                st.error("Athlete access is not configured. Ask Miki to set CLIENT_CODES in the existing app's Secrets.")
            elif validate_client(athlete, code):
                st.session_state["hank_role"] = "athlete"
                st.session_state["hank_athlete"] = athlete
                st.rerun()
            else:
                st.error("Name or access code is incorrect.")
        else:
            expected = get_coach_password()
            if expected and hmac.compare_digest(str(password).encode(), expected.encode()):
                st.session_state["hank_role"] = "coach"
                st.rerun()
            else:
                st.error("Coach access is not configured or the password is incorrect.")
    st.caption("Coaching monitoring, not a medical record or emergency service. Keep your access code private. Existing health-data consent and retention arrangements still apply.")


def main() -> None:
    st.sidebar.title("HANK SC")
    st.sidebar.caption("ATHLETE MONITORING\n\nData. Decisions. Performance.")
    st.sidebar.link_button("Visit HANK SC", "https://hanksc.com", use_container_width=True)
    st.sidebar.link_button("Privacy", "https://hanksc.com/privacy-policy", use_container_width=True)
    role = st.session_state.get("hank_role")
    if role not in ("athlete", "coach"):
        show_login()
        return
    st.sidebar.caption(f"Signed in: {authenticated_athlete() if role == 'athlete' else 'Coach'}")
    if st.sidebar.button("Sign out", use_container_width=True, disabled=bool(st.session_state.get("hank_pending"))):
        st.session_state.clear()
        st.rerun()
    if role == "coach":
        show_dashboard()
    else:
        page = st.sidebar.radio("Your space", ["Daily Readiness", "My Dashboard"])
        if page == "Daily Readiness":
            show_readiness_form()
        else:
            if not pending_save_notice():
                show_dashboard(athlete_restricted=require_athlete())


if __name__ == "__main__":
    main()

"""
Hank SC Athlete Monitoring App — Google Sheets Backend

Streamlit app for:
- Daily readiness / wellness logs
- Post-session RPE logs
- Coach dashboard
- Athlete dashboard
- Google Sheets storage + longitudinal charts

Run:
    streamlit run hanksc_monitoring_app_gsheets.py
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List
from pathlib import Path

import gspread
import pandas as pd
import plotly.express as px
import streamlit as st
from google.oauth2.service_account import Credentials

APP_TITLE = "Hank SC Athlete Monitoring"
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

#zaciatok noveho kodu

BASE_DIR = Path(__file__).resolve().parent
LOGO_PATH = BASE_DIR / "assets" / "hank_sc_logo.png"

st.logo(
    str(LOGO_PATH),
    size="large",
    link="https://hanksc.com",
)

with st.sidebar:
    st.link_button(
        "Visit HANK SC",
        "https://hanksc.com",
        use_container_width=True,
    )
    
st.markdown("---")

st.markdown(
    """
    <div style="text-align:center; font-size:12px; opacity:0.65;">
        HANK Strength & Conditioning<br>
        <a href="https://hanksc.com" target="_blank">
            Data. Decisions. Performance.
        </a>
    </div>
    """,
    unsafe_allow_html=True,
)

#koniec noveho kodu

st.markdown(
    """
    <style>
    .main .block-container {padding-top: 2rem; padding-bottom: 3rem; max-width: 1180px;}
    .hank-hero {padding: 1.4rem 1.6rem; border-radius: 18px; background: linear-gradient(135deg,#111 0%,#2a2a2a 100%); color: white; margin-bottom: 1.2rem;}
    .hank-hero h1 {margin-bottom: .3rem; font-size: 2.3rem; line-height: 1.1;}
    .hank-hero p {opacity: .86; font-size: 1.02rem; margin-bottom: 0;}
    .small-note {opacity: .72; font-size: .9rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


def hero(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="hank-hero"><h1>{title}</h1><p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )


@st.cache_resource(ttl=600)
def get_gsheet_client():
    """Create cached Google Sheets client from Streamlit secrets."""
    try:
        service_account_info = dict(st.secrets["gcp_service_account"])
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        credentials = Credentials.from_service_account_info(service_account_info, scopes=scopes)
        return gspread.authorize(credentials)
    except Exception as e:
        st.error("Google Sheets authentication failed. Check Streamlit Secrets and service account access.")
        st.exception(e)
        st.stop()


@st.cache_data(ttl=30)
def load_sheet_data(sheet_name: str, columns: List[str]) -> pd.DataFrame:
    """Load a worksheet into a DataFrame."""
    try:
        client = get_gsheet_client()
        spreadsheet = client.open_by_key(st.secrets["GOOGLE_SHEET_ID"])
        worksheet = spreadsheet.worksheet(sheet_name)
        rows = worksheet.get_all_records()
        df = pd.DataFrame(rows)
        for col in columns:
            if col not in df.columns:
                df[col] = None
        return df[columns]
    except gspread.exceptions.WorksheetNotFound:
        st.error(f"Worksheet '{sheet_name}' was not found. Create this tab in your Google Sheet.")
        st.stop()
    except Exception as e:
        st.error(f"Could not load worksheet '{sheet_name}'.")
        st.exception(e)
        st.stop()


def append_sheet_row(sheet_name: str, columns: List[str], row: Dict[str, Any]) -> None:
    """Append one row to a worksheet."""
    try:
        client = get_gsheet_client()
        spreadsheet = client.open_by_key(st.secrets["GOOGLE_SHEET_ID"])
        worksheet = spreadsheet.worksheet(sheet_name)
        values = [row.get(col, "") for col in columns]
        worksheet.append_row(values, value_input_option="USER_ENTERED")
        st.cache_data.clear()
    except Exception as e:
        st.error(f"Could not save data to '{sheet_name}'.")
        st.exception(e)
        st.stop()


def normalize_name(name: str) -> str:
    return " ".join(str(name).strip().split())


def get_coach_password() -> str:
    return str(st.secrets.get("COACH_PASSWORD", "change-me"))


def get_client_codes() -> Dict[str, str]:
    raw = st.secrets.get("CLIENT_CODES", {})
    return {str(k).strip().lower(): str(v).strip() for k, v in dict(raw).items()}


def validate_client(athlete: str, code: str) -> bool:
    codes = get_client_codes()
    if not codes:
        return True
    return codes.get(normalize_name(athlete).lower()) == str(code).strip()


def athlete_login(prefix: str = "") -> tuple[str, str, bool]:
    c1, c2 = st.columns([2, 1])
    with c1:
        athlete = normalize_name(st.text_input("Athlete name", key=f"{prefix}_athlete"))
    with c2:
        code = st.text_input("Access code", type="password", key=f"{prefix}_code")
    valid = bool(athlete) and validate_client(athlete, code)
    if athlete and not valid:
        st.error("Access code is not valid for this athlete.")
    return athlete, code, valid


def readiness_score(sleep_quality: int, energy: int, soreness: int, stress: int, motivation: int, general_pain: int, confidence: int) -> int:
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
    if df.empty or "date" not in df.columns:
        return df
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna(subset=["date"])


def get_all_athletes() -> list[str]:
    r = load_sheet_data(READINESS_SHEET, READINESS_COLUMNS)
    s = load_sheet_data(SESSION_SHEET, SESSION_COLUMNS)
    names = sorted(set(r["athlete"].dropna().astype(str)) | set(s["athlete"].dropna().astype(str)))
    return [n for n in names if n.strip()]


def filter_by_athlete_and_dates(df: pd.DataFrame, athlete: str | None, start: date, end: date) -> pd.DataFrame:
    if df.empty:
        return df
    out = parse_dates(df)
    if athlete and athlete != "All athletes":
        out = out[out["athlete"].astype(str).str.lower() == athlete.lower()]
    out = out[(out["date"].dt.date >= start) & (out["date"].dt.date <= end)]
    return out.sort_values("date")


def add_derived_columns(readiness: pd.DataFrame, sessions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    r = readiness.copy()
    s = sessions.copy()
    if not r.empty:
        for col in ["sleep_hours", "sleep_quality", "energy", "soreness", "stress", "motivation", "general_pain", "confidence", "running_pain", "acceleration_pain", "cutting_pain", "jump_landing_pain", "morning_stiffness", "readiness_score"]:
            if col in r.columns:
                r[col] = pd.to_numeric(r[col], errors="coerce")
        r["readiness_7d_avg"] = r.sort_values("date").groupby("athlete")["readiness_score"].transform(lambda x: x.rolling(7, min_periods=1).mean())
    if not s.empty:
        for col in ["duration_min", "session_rpe", "session_load", "pain_during", "pain_after"]:
            if col in s.columns:
                s[col] = pd.to_numeric(s[col], errors="coerce")
        s["week"] = s["date"].dt.to_period("W").astype(str)
    return r, s


def plot_line(df: pd.DataFrame, y: str, title: str, y_label: str | None = None) -> None:
    if df.empty or y not in df.columns:
        st.info(f"No data for {title}.")
        return
    fig = px.line(df, x="date", y=y, color="athlete" if df["athlete"].nunique() > 1 else None, markers=True, title=title)
    fig.update_layout(xaxis_title="", yaxis_title=y_label or y, hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)


def show_summary_cards(readiness: pd.DataFrame, sessions: pd.DataFrame) -> None:
    c1, c2, c3, c4 = st.columns(4)
    latest_readiness = None
    latest_pain = None
    red_flags = 0
    load_7d = 0
    if not readiness.empty:
        latest = readiness.sort_values("date").iloc[-1]
        latest_readiness = int(latest.get("readiness_score", 0))
        latest_pain = int(latest.get("general_pain", 0))
        red_flags += int((readiness["flag"] == "RED").sum())
    if not sessions.empty:
        max_date = sessions["date"].max()
        last_7 = sessions[sessions["date"] >= max_date - pd.Timedelta(days=6)]
        load_7d = int(pd.to_numeric(last_7["session_load"], errors="coerce").fillna(0).sum())
        red_flags += int((sessions["flag"] == "RED").sum())
    with c1:
        st.metric("Latest readiness", "—" if latest_readiness is None else f"{latest_readiness}/100")
    with c2:
        st.metric("Latest pain", "—" if latest_pain is None else f"{latest_pain}/10")
    with c3:
        st.metric("7-day load", f"{load_7d} AU")
    with c4:
        st.metric("Red flags", red_flags)


def show_home() -> None:
    hero("Hank SC Monitoring", "Daily readiness, session RPE, pain response and training-load trends in one simple app.")
    st.markdown(
        """
        ### What this app does
        - **Athletes** submit daily readiness and post-session RPE logs.
        - **Coach** sees longitudinal trends, flags and raw data.
        - **Clients** can view their own trends in a simple dashboard.
        - Data are saved directly into **Google Sheets**.

        ### Flag logic
        - **GREEN**: normal training signal.
        - **YELLOW**: monitor or adjust load.
        - **RED**: high pain / low readiness signal — modify training and check context.

        <p class="small-note">MVP note: this is a lightweight coaching-monitoring tool, not a medical record system. Use proper consent, access control and GDPR-aware data handling for real client data.</p>
        """,
        unsafe_allow_html=True,
    )


def show_readiness_form() -> None:
    hero("Daily Readiness Check-in", "Less than 60 seconds. Be honest, not perfect. This helps adjust training load and return-to-sport progressions.")
    athlete, _, valid = athlete_login("readiness")
    if not valid:
        st.info("Enter your athlete name and access code to continue.")
        return
    with st.form("daily_readiness_form", clear_on_submit=True):
        st.subheader("Today")
        c1, c2, c3 = st.columns(3)
        with c1:
            log_date = st.date_input("Date", value=date.today())
        with c2:
            context = st.selectbox("Context", ["Normal training day", "Recovery day", "Match day", "Travel day", "Rest day"])
        with c3:
            injury_focus = st.selectbox("Injury focus", ["General", "Hamstring", "Knee / ACL", "Achilles", "Shoulder", "Back", "Other"])
        st.subheader("Readiness")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            sleep_hours = st.number_input("Sleep hours", min_value=0.0, max_value=14.0, value=7.0, step=0.25)
            sleep_quality = st.slider("Sleep quality", 1, 5, 3)
        with c2:
            energy = st.slider("Energy", 1, 5, 3)
            motivation = st.slider("Motivation to train", 1, 5, 3)
        with c3:
            soreness = st.slider("Muscle soreness", 1, 5, 2)
            stress = st.slider("Stress level", 1, 5, 2)
        with c4:
            general_pain = st.slider("Current pain / discomfort", 0, 10, 0)
            confidence = st.slider("Confidence to train", 1, 5, 4)
        pain_location = st.text_input("Pain / discomfort location, if any")
        st.subheader("Sport-specific / injury-specific signals")
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            running_pain = st.slider("Running pain", 0, 10, 0)
        with c2:
            acceleration_pain = st.slider("Acceleration pain", 0, 10, 0)
        with c3:
            cutting_pain = st.slider("Cutting / COD pain", 0, 10, 0)
        with c4:
            jump_landing_pain = st.slider("Jump / landing pain", 0, 10, 0)
        with c5:
            morning_stiffness = st.slider("Morning stiffness", 0, 10, 0)
        notes = st.text_area("Anything I should know today?", height=90)
        submitted = st.form_submit_button("Submit daily check-in", use_container_width=True)
    if submitted:
        score = readiness_score(sleep_quality, energy, soreness, stress, motivation, general_pain, confidence)
        flag = readiness_flag(score, general_pain, sleep_quality, energy, soreness, stress, confidence)
        row = {
            "timestamp": pd.Timestamp.now().isoformat(timespec="seconds"), "date": str(log_date), "athlete": athlete,
            "context": context, "sleep_hours": sleep_hours, "sleep_quality": sleep_quality, "energy": energy,
            "soreness": soreness, "stress": stress, "motivation": motivation, "general_pain": general_pain,
            "pain_location": pain_location, "confidence": confidence, "injury_focus": injury_focus,
            "running_pain": running_pain, "acceleration_pain": acceleration_pain, "cutting_pain": cutting_pain,
            "jump_landing_pain": jump_landing_pain, "morning_stiffness": morning_stiffness, "notes": notes,
            "readiness_score": score, "flag": flag,
        }
        append_sheet_row(READINESS_SHEET, READINESS_COLUMNS, row)
        st.success(f"Saved to Google Sheets. Readiness score: {score}/100. Flag: {flag}.")


def show_session_form() -> None:
    hero("Post-Session Training Log", "Quick session feedback: duration, RPE, pain response and anything that felt wrong or unusually good.")
    athlete, _, valid = athlete_login("session")
    if not valid:
        st.info("Enter your athlete name and access code to continue.")
        return
    with st.form("post_session_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            log_date = st.date_input("Session date", value=date.today())
        with c2:
            session_type = st.selectbox("Session type", ["Strength", "Running / Sprint", "Conditioning", "Mobility", "Recovery", "Match / Game", "Technical", "Other"])
        with c3:
            completed = st.selectbox("Session completed?", ["Yes", "Partially", "No"])
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            duration_min = st.number_input("Duration, minutes", min_value=0, max_value=300, value=60, step=5)
        with c2:
            session_rpe = st.slider("Session RPE", 0, 10, 6)
        with c3:
            pain_during = st.slider("Pain during session", 0, 10, 0)
        with c4:
            pain_after = st.slider("Pain after session", 0, 10, 0)
        best_part = st.text_input("Best part of today’s session")
        issue_notes = st.text_area("Anything painful, wrong, unusual or important?", height=90)
        submitted = st.form_submit_button("Submit post-session log", use_container_width=True)
    if submitted:
        session_load = int(duration_min * session_rpe)
        flag = session_flag(pain_during, pain_after, session_rpe)
        row = {
            "timestamp": pd.Timestamp.now().isoformat(timespec="seconds"), "date": str(log_date), "athlete": athlete,
            "session_type": session_type, "completed": completed, "duration_min": duration_min, "session_rpe": session_rpe,
            "session_load": session_load, "pain_during": pain_during, "pain_after": pain_after, "best_part": best_part,
            "issue_notes": issue_notes, "flag": flag,
        }
        append_sheet_row(SESSION_SHEET, SESSION_COLUMNS, row)
        st.success(f"Saved to Google Sheets. Session load: {session_load} AU. Flag: {flag}.")


def show_dashboard(athlete_restricted: str | None = None) -> None:
    hero("Athlete Dashboard" if athlete_restricted else "Coach Dashboard", "Longitudinal monitoring for readiness, pain, session RPE and training load.")
    readiness = parse_dates(load_sheet_data(READINESS_SHEET, READINESS_COLUMNS))
    sessions = parse_dates(load_sheet_data(SESSION_SHEET, SESSION_COLUMNS))
    if athlete_restricted:
        selected_athlete = athlete_restricted
    else:
        selected_athlete = st.sidebar.selectbox("Athlete", ["All athletes"] + get_all_athletes())
    st.sidebar.markdown("### Date range")
    start = st.sidebar.date_input("Start date", value=date.today() - timedelta(days=30))
    end = st.sidebar.date_input("End date", value=date.today())
    if start > end:
        st.error("Start date must be before end date.")
        return
    r = filter_by_athlete_and_dates(readiness, selected_athlete, start, end)
    s = filter_by_athlete_and_dates(sessions, selected_athlete, start, end)
    r, s = add_derived_columns(r, s)
    if r.empty and s.empty:
        st.info("No data for selected filters yet.")
        return
    show_summary_cards(r, s)
    st.divider()
    tab1, tab2, tab3, tab4 = st.tabs(["Readiness", "Pain & symptoms", "Training load", "Raw data"])
    with tab1:
        c1, c2 = st.columns(2)
        with c1:
            plot_line(r, "readiness_score", "Readiness score", "Score / 100")
        with c2:
            plot_line(r, "readiness_7d_avg", "7-day average readiness", "Score / 100")
        if not r.empty:
            st.markdown("### Latest readiness notes")
            notes = r.sort_values("date", ascending=False)[["date", "athlete", "flag", "general_pain", "readiness_score", "notes"]].head(10)
            st.dataframe(notes, use_container_width=True, hide_index=True)
    with tab2:
        c1, c2 = st.columns(2)
        with c1:
            plot_line(r, "general_pain", "General pain / discomfort", "Pain 0-10")
        with c2:
            plot_line(r, "morning_stiffness", "Morning stiffness", "Stiffness 0-10")
        symptom_cols = ["running_pain", "acceleration_pain", "cutting_pain", "jump_landing_pain"]
        if symptom_cols and not r.empty:
            long = r.melt(id_vars=["date", "athlete"], value_vars=symptom_cols, var_name="symptom", value_name="score")
            long["score"] = pd.to_numeric(long["score"], errors="coerce")
            fig = px.line(long, x="date", y="score", color="symptom", markers=True, title="Sport-specific symptom response")
            fig.update_layout(xaxis_title="", yaxis_title="Pain 0-10", hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)
    with tab3:
        if s.empty:
            st.info("No session data for this range.")
        else:
            weekly = s.groupby(["week", "athlete"], as_index=False)["session_load"].sum()
            fig = px.bar(weekly, x="week", y="session_load", color="athlete" if weekly["athlete"].nunique() > 1 else None, title="Weekly training load")
            fig.update_layout(xaxis_title="Week", yaxis_title="Session load AU")
            st.plotly_chart(fig, use_container_width=True)
            c1, c2 = st.columns(2)
            with c1:
                plot_line(s, "session_rpe", "Session RPE", "RPE 0-10")
            with c2:
                plot_line(s, "pain_after", "Pain after session", "Pain 0-10")
            st.markdown("### Session log")
            show_cols = ["date", "athlete", "session_type", "completed", "duration_min", "session_rpe", "session_load", "pain_during", "pain_after", "flag", "issue_notes"]
            st.dataframe(s[show_cols].sort_values("date", ascending=False), use_container_width=True, hide_index=True)
    with tab4:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("### Readiness data")
            st.download_button("Download readiness CSV", data=r.to_csv(index=False).encode("utf-8"), file_name="hanksc_readiness_export.csv", mime="text/csv", use_container_width=True)
            st.dataframe(r.sort_values("date", ascending=False), use_container_width=True, hide_index=True)
        with c2:
            st.markdown("### Session data")
            st.download_button("Download session CSV", data=s.to_csv(index=False).encode("utf-8"), file_name="hanksc_session_export.csv", mime="text/csv", use_container_width=True)
            st.dataframe(s.sort_values("date", ascending=False), use_container_width=True, hide_index=True)


def show_coach_login_and_dashboard() -> None:
    hero("Coach Login", "Protected view for Hank SC monitoring data.")
    password = st.text_input("Coach password", type="password")
    if password == get_coach_password():
        show_dashboard()
    elif password:
        st.error("Wrong password.")
    else:
        st.info("Enter coach password to view dashboard.")


def show_athlete_dashboard_login() -> None:
    hero("Athlete Access", "Enter your name and access code to view your own trends.")
    athlete, _, valid = athlete_login("athlete_dashboard")
    if valid:
        show_dashboard(athlete_restricted=athlete)


def main() -> None:
    st.sidebar.title("Hank SC")
    page = st.sidebar.radio("Navigation", ["Home", "Daily Readiness Check-in", "Post-Session Log", "Athlete Dashboard", "Coach Dashboard"])
    if page == "Home":
        show_home()
    elif page == "Daily Readiness Check-in":
        show_readiness_form()
    elif page == "Post-Session Log":
        show_session_form()
    elif page == "Athlete Dashboard":
        show_athlete_dashboard_login()
    elif page == "Coach Dashboard":
        show_coach_login_and_dashboard()


if __name__ == "__main__":
    main()

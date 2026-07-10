# Hank SC Athlete Monitoring App — Google Sheets Backend

This is the Google Sheets version of the Hank SC monitoring app.

## What it does

- Daily readiness / wellness check-in
- Post-session RPE log
- Session load calculation: `duration × RPE`
- Athlete dashboard
- Coach dashboard
- Longitudinal charts
- Google Sheets storage

## Required Google Sheet tabs

Create a Google Sheet with two tabs:

```text
readiness_log
session_log
```

### readiness_log header

```text
timestamp
date
athlete
context
sleep_hours
sleep_quality
energy
soreness
stress
motivation
general_pain
pain_location
confidence
injury_focus
running_pain
acceleration_pain
cutting_pain
jump_landing_pain
morning_stiffness
notes
readiness_score
flag
```

### session_log header

```text
timestamp
date
athlete
session_type
completed
duration_min
session_rpe
session_load
pain_during
pain_after
best_part
issue_notes
flag
```

## Streamlit Secrets example

Never put real secrets into GitHub.

```toml
COACH_PASSWORD = "your-coach-password"
GOOGLE_SHEET_ID = "your-google-sheet-id"

[CLIENT_CODES]
alika = "ak2026"
jm = "jm2026"

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = '''-----BEGIN PRIVATE KEY-----
...
-----END PRIVATE KEY-----
'''
client_email = "..."
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
universe_domain = "googleapis.com"
```

## Local run

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run hanksc_monitoring_app_gsheets.py
```

## Deployment

Upload these files to GitHub:

- hanksc_monitoring_app_gsheets.py
- requirements.txt
- README.md

Then in Streamlit Cloud:
- Update the main file path to `hanksc_monitoring_app_gsheets.py`
- Add Secrets
- Reboot the app

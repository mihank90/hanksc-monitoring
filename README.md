# Hank SC Athlete Monitoring App

Simple Streamlit app for daily readiness, post-session RPE logging, longitudinal statistics and interactive charts.

## Files

- `hanksc_monitoring_app.py` – main app
- `requirements.txt` – Python packages

## Local installation

```bash
cd hanksc_monitoring_app
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run hanksc_monitoring_app.py
```

On Mac/Linux:

```bash
cd hanksc_monitoring_app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run hanksc_monitoring_app.py
```

## Optional secrets

Create:

```text
.streamlit/secrets.toml
```

Example:

```toml
COACH_PASSWORD = "your-secure-password"

[CLIENT_CODES]
alika = "1234"
client02 = "abcd"
```

If `CLIENT_CODES` are not configured, the MVP allows any athlete name/code combination. That is fine for local testing, not for real client data.

## Data

The app creates:

```text
data/readiness_log.csv
data/session_log.csv
```

Back these up regularly if you use the CSV version with real clients.

## Important privacy note

This is a lightweight MVP coaching-monitoring tool. For real online deployment with multiple clients, use proper login, consent text, secure hosting and GDPR-aware data handling.

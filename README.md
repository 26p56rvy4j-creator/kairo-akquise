# Kairo Akquise

Streamlit application for managing acquisition workflows. Entrypoint: `akquise/streamlit_app.py`.

Configure Streamlit Secrets with the restricted PostgreSQL connection under `AKQUISE_DATABASE_URL` and a Google OpenID Connect `[auth]` configuration. The application verifies membership against its user allowlist.

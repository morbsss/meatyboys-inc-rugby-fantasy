# Deploying to Railway

This Flask app is ready for Railway. The pieces:

| File | Purpose |
|---|---|
| `requirements.txt` (root) | Lets Nixpacks detect Python; pulls in `api/requirements.txt` |
| `api/requirements.txt` | Runtime deps (now includes `gunicorn`) |
| `Procfile` / `railway.json` | Start command + healthcheck (`/healthz`) |
| `.python-version` | Pins Python 3.12 |

The web server is **gunicorn** serving `api.index:app`, bound to Railway's `$PORT`.
A `ProxyFix` middleware trusts Railway's TLS proxy so HTTPS session cookies work.

## 1. Create the project
1. Push this repo to GitHub.
2. Railway → **New Project → Deploy from GitHub repo** → pick this repo.
3. Add a database: **New → Database → PostgreSQL**.

## 2. Set variables (service → Variables)
```
DB_TYPE=postgres
DATABASE_URL=${{Postgres.DATABASE_URL}}   # reference the Postgres plugin
SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
FLASK_ENV=production
ALLOW_UNRESTRICTED_EDITS=false
CRON_SECRET=<another random key>          # optional, for /api/cron/*
```
Do **not** set `PORT` — Railway injects it. See `.env.production.example`.

## 3. Database schema + data
Tables are created automatically (`ensure_schema` runs on first request), but the
database starts **empty** — you need to load data. Pick one:

- **Mock data (quick smoke test):** run once against the Postgres URL —
  ```
  DB_TYPE=postgres DATABASE_URL="<railway postgres url>" python -m api.seed_mock
  ```
  (Run from a machine that can reach the DB; Railway shows a public connection
  string under the Postgres plugin → Connect.)

- **Your real data (`fantasy_2025_26.db`):** copy the SQLite rows into Postgres with
  the migrator (needs `pip install psycopg2-binary` locally):
  ```
  python tools/migrate_to_postgres.py \
      --source fantasy_2025_26.db \
      --database-url "<railway postgres public url>"
  ```
  It builds the schema (`ensure_schema`), copies every table in FK order, resets
  SERIAL sequences, and prints a source-vs-target row-count check. The target's
  matching tables are **replaced** (it's safe to re-run; pass `--keep` to append).
  Tested end-to-end against Postgres 16: all 16 tables migrate with matching
  counts, sequences advance correctly, and the app reads the result.

## 4. Verify
- `https://<your-app>.up.railway.app/healthz` → `{"status":"ok"}`
- `/auth` loads, you can register/log in, `/squad` renders.

## 5. Scheduled jobs (optional)
Scoring/lineups run via HTTP at `/api/cron/tick` (guarded by `CRON_SECRET`). There's
no in-process scheduler, so trigger it externally — a Railway **Cron** service or any
scheduler hitting:
```
curl -H "Authorization: Bearer $CRON_SECRET" https://<your-app>.up.railway.app/api/cron/tick
```

## Notes
- `*.db` and `.env.*` are gitignored — local SQLite files are never deployed.
- Start command lives in both `Procfile` and `railway.json`; `railway.json` wins on
  Railway. Keep them in sync if you change worker/timeout settings.
- ML libs (numpy/pandas/sklearn) are intentionally **not** in `requirements.txt`; the
  analysis pipeline (`api/predict.py`) runs offline and writes prediction tables the
  app only reads.


$env:DB_TYPE="postgres"; $env:DATABASE_URL="postgresql://postgres:hUKDRitfoebfSLWceMTwyTQSbeywtuMY@shortline.proxy.rlwy.net:50637/railway"; python -m api.seed_mock
# Persistent Storage Setup

The combined Flask app (`gunicorn app:app` from the repository root) uses one PostgreSQL database for Profile/Finance documents, Habits, and Calendar state. Do not deploy `Calendar/app.py` on Render: that standalone development server intentionally refuses to start there because it cannot provide the shared Profile-backed Calendar storage. Render must set `DATABASE_URL` to a PostgreSQL connection string and a stable `FLASK_SECRET_KEY`. The combined app fails during startup on Render if either is missing; it does not fall back to JSON, SQLite, or a newly generated session key.

Receipt image bytes are stored as private Cloudinary assets. Set `CLOUDINARY_URL` in Render; the PostgreSQL `receipt_assets` table stores the Cloudinary key and format. Receipt delivery continues through the authenticated Flask route. Local non-Render development may still use JSON, SQLite, and receipt files.

## Local database

Install Docker Desktop, then from the repository root run:

```powershell
docker compose up -d postgres
Copy-Item .env.example .env
```

The Compose service creates the local `daily_planner` database and persists it in a Docker volume. The credentials in `.env.example` are for local development only; use a managed PostgreSQL service and private credentials for deployment.

Install dependencies and migrate existing Finance JSON data:

```powershell
python -m pip install -r requirements.txt
python Finance/migrate_json_to_postgres.py
python Finance/migrate_json_to_postgres.py --apply
```

The first migration command previews the JSON files found. The apply command imports each profile and store; existing database documents are skipped unless `--replace` is explicitly supplied. Source JSON files are not deleted. Back up personal data before migration.

## Render Environment

Set these variables on the Render Web Service:

- `DATABASE_URL`: the connection URL for the existing Render PostgreSQL database.
- `FLASK_SECRET_KEY`: a stable random secret (do not rotate during routine deploys).
- `CLOUDINARY_URL`: the Cloudinary account URL used for private receipt image storage.

The service creates the `finance_documents`, `habits`, `habit_checkins`, `calendar_states`, `calendar_imports`, and `receipt_assets` tables on connection. `create_all` creates missing tables but does not alter existing columns; schema changes beyond these initial tables need a migration.

## Legacy Data Migration

- Finance: preview/import JSON with `Finance/migrate_json_to_postgres.py`; export a Finance backup before deploying.
- Habits: export the old `.data/habits.db` before redeploy and import it with `Calendar/migrate_habits_to_postgres.py --sqlite PATH` after configuring `DATABASE_URL`.
- Calendar: on each old browser origin, export from `Calendar/transfer.html`; import/open the app on the Render origin while signed into the matching Profile. Each browser's legacy data is merged once by record ID into PostgreSQL, and the server tracks import source IDs to keep retries idempotent. Calendar never uses local data as a runtime fallback.
- Receipts: preserve the old `Finance/static/receipts/<profile-id>/` folders and upload them with `Finance/migrate_receipts_to_cloudinary.py`. The script leaves source files in place. Do not redeploy before copying data off an ephemeral Render filesystem.

After migrating, verify one record from each storage class, restart/redeploy, then verify again. Keep independent PostgreSQL backups and Cloudinary asset backups; Render and free-tier services do not guarantee protection from account deletion, provider incidents, or accidental data deletion.

Run the app as usual with `python app.py`. The Calendar UI is unchanged, but in the combined app its state now syncs to PostgreSQL per Profile rather than using localStorage as its runtime store. Habit check-ins use PostgreSQL too.

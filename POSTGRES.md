# PostgreSQL Setup

The combined Flask app and standalone Finance app use PostgreSQL when `DATABASE_URL` is set. SQLAlchemy creates the required tables on first connection. Without that variable, Finance continues using its existing JSON files.

## Local database

Install Docker Desktop, then from the repository root run:

```powershell
docker compose up -d postgres
Copy-Item .env.example .env
```

The Compose service creates the local `daily_planner` database and persists it in a Docker volume. The credentials in `.env.example` are for local development only; use a managed PostgreSQL service and private credentials for deployment.

Install dependencies and migrate the existing Finance JSON data:

```powershell
python -m pip install -r requirements.txt
python Finance/migrate_json_to_postgres.py
python Finance/migrate_json_to_postgres.py --apply
```

The first migration command previews the JSON files found. The apply command imports each profile and store; existing database documents are skipped unless `--replace` is explicitly supplied. Source JSON files are not deleted. Back up personal data before migration.

Run the app as usual with `python app.py`. Set `DATABASE_URL` in the deployment environment to the PostgreSQL connection URL. The calendar's browser localStorage data is unaffected; habit check-ins continue using their existing SQLite database.

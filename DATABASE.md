# Database

Daily Planner keeps all user data in **PostgreSQL**. Render only runs the website. Every day, an **encrypted backup is stored outside Render**, so the data survives a lost or deleted database.

```
Website (Render) ──► PostgreSQL (Render) ──► encrypted daily backup (GitHub Actions + your PC) ──► verified restore
```

If the database is gone, jump straight to **[EMERGENCY DATA RECOVERY](#emergency-data-recovery)**.

- [Data inventory](#data-inventory)
- [Architecture](#architecture)
- [Tables and relationships](#tables-and-relationships)
- [User isolation](#user-isolation)
- [Transactions and concurrency](#transactions-and-concurrency)
- [Passwords](#passwords)
- [Environment variables](#environment-variables)
- [Local development](#local-development)
- [Production deployment on Render](#production-deployment-on-render)
- [Render Free PostgreSQL limitations](#render-free-postgresql-limitations)
- [JSON migration](#json-migration)
- [Backup](#backup)
- [Restore](#restore)
- [Disaster recovery](#disaster-recovery)
- [EMERGENCY DATA RECOVERY](#emergency-data-recovery)
- [Data retention](#data-retention)
- [Schema changes](#schema-changes)
- [Tests](#tests)

## Data inventory

| Data | Production storage | Permanent? | Notes |
| --- | --- | --- | --- |
| Accounts: name, password hash | PostgreSQL `finance_profiles` | yes | |
| Finance: transactions, budgets, accounts, goals, categories, shopping, recurring, debts, net worth, AI insights, currency rates | PostgreSQL `finance_*` | yes | Local dev without `DATABASE_URL`: `Finance/data/**/*.json` |
| Calendar: tasks, events, special days, categories, field definitions, view settings | PostgreSQL `calendar_*` | yes | Old browser-only data is imported once from localStorage on first login |
| Habits and check-ins | PostgreSQL `habits`, `habit_checkins` | yes | Local dev without `DATABASE_URL`: `.data/habits.db` (SQLite) |
| Receipt images | Cloudinary private assets, indexed by PostgreSQL `receipt_assets` | yes | Backups can include the images (`--include-receipts`) |
| Login session | Signed cookie (`FLASK_SECRET_KEY`) | no | Lost sessions just mean logging in again |
| Browser localStorage | Calendar id mapping, import marker, legacy pre-server data | no | No primary data. The standalone `Calendar/app.py` demo keeps everything in the browser, so don't deploy it. |
| Diary | none | n/a | This app has no Diary module |
| Render filesystem | nothing | n/a | On Render the app refuses JSON or SQLite fallbacks and writes no user files |

Old data that predates the migration lives in the repository at `Finance/data/*.json`. These three files were committed long ago and are therefore **public on GitHub**. See the final report for options.

## Architecture

```
Browser ──► Render Web Service: gunicorn app:app
              ├─ /finance/...        Finance blueprint   (Finance/finance_routes.py)
              ├─ /calendar/          Calendar front end  (Calendar/)
              └─ /calendar/api/...   Habits + Calendar API (Calendar/habits.py)
                       │
              Finance/database.py ── SQLAlchemy 2 + psycopg 3 ──► PostgreSQL (DATABASE_URL)
              receipt images ─────────────────────────────────► Cloudinary (private)
```

- **One database.** `Finance/database.py` owns the engine, the models, the `postgres://` URL handling and the per-request transaction.
- **Stores become rows.** The Finance routes work on whole "stores", as they always have. `Finance/finance_tables.py` converts each store to relational rows (one row per record, typed columns, child tables) and back. `Finance/calendar_tables.py` does the same for Calendar. Pages, forms and calculations are unchanged.
- **Lossless.** Each payload is converted in memory and compared with the original before saving. Fields of unexpected types go into an `extra` JSON column. A payload that can't round-trip is kept whole in `finance_documents`.
- **Schema creation.** Missing tables are created on the first connection, under a PostgreSQL advisory lock, so gunicorn workers booting together cannot race.
- **Production only uses PostgreSQL.** With `RENDER` set, startup fails unless `DATABASE_URL` is PostgreSQL and both `FLASK_SECRET_KEY` and `CLOUDINARY_URL` are set. There is no JSON or SQLite fallback.

## Tables and relationships

A **Profile is the user account**. Every user-owned row carries the owner's profile id, as `profile_id` or, for Habits, `owner`.

| Table | Contents | Key / indexes |
| --- | --- | --- |
| `finance_profiles` | `id`, `name`, `created_at`, `password_hash` | `position`; unique `id` |
| `finance_stores` | which stores exist per profile, plus their envelope | `(profile_id, store)` |
| `finance_transactions` | `id`, `date`, `type`, `category`, `account`, `item`, `amount`, `tags`, `transfer_id`, `split_id`, `goal_id`, `recurring_id`, `debt_id`, `deleted_at`, ... | `(profile_id, position)`; index `(profile_id, date)` |
| `finance_budgets`, `finance_accounts`, `finance_goals`, `finance_categories`, `finance_recurring_rules`, `finance_networth_snapshots`, `finance_insights`, `finance_currency_rates` | one row per budget, account, goal, ... | `(profile_id, position)` |
| `finance_debts` + `finance_debt_payments` | debts, and their payments as child rows | child key `(profile_id, parent_position, position)` |
| `finance_shopping_items` + `finance_shopping_price_checks` | shopping list, and price history as child rows | same pattern |
| `finance_documents` | any store that doesn't fit its table, as JSONB | `key` = `profiles/<id>/<store>` |
| `calendar_states` | view settings, plus a `version` that rejects stale saves | `profile_id` |
| `calendar_state_lists` | marks profiles whose lists are stored as rows | `profile_id` |
| `calendar_categories`, `calendar_category_colors`, `calendar_tasks`, `calendar_events`, `calendar_special_days`, `calendar_field_defs` | Calendar items, one row each | `(profile_id, position)`; date indexes |
| `calendar_imports` | browser imports already applied, so none runs twice | `(profile_id, source_id)` |
| `habits` | `id` (serial), `owner`, `name`, `emoji`, `color`, `sort`, `archived`, `created_at` | index `owner` |
| `habit_checkins` | `(habit_id, date)`, `note` | FK `habits.id` with `ON DELETE CASCADE` |
| `receipt_assets` | `(profile_id, filename)` mapped to the Cloudinary key and format | unique `storage_key` |

```
finance_profiles.id ─┬─< finance_*.profile_id       (Finance)
                     ├─< calendar_*.profile_id       (Calendar)
                     ├─< habits.owner ──< habit_checkins.habit_id   (FK, cascade)
                     └─< receipt_assets.profile_id ──► Cloudinary
```

**Account deletion** removes, for that profile only:

- the Finance stores and their rows;
- Habits, check-ins and Calendar rows;
- receipt images and their index rows.

Other accounts are untouched. This is verified by tests and by the recovery drill.

## User isolation

- **Login.** `_activate_profile` runs before every Finance page. It accepts only a session created after a password check. The profile id always comes from that signed session, never from a URL, form or JSON body.
- **Per-request paths.** Store paths are `ProfileStorePath` objects that resolve to the current request's `g.profile_id`. Concurrent requests from different users in one process can't cross. Before this change, module globals were rewritten per request and could leak between threads.
- **Scoped queries.** Every query and key is scoped by that id (`WHERE profile_id = …`, `WHERE owner = …`). A record id copied from another account's URL simply isn't found. Edit, delete and check-in requests then do nothing, or return 404.
- **Calendar API.** It additionally requires the page's `profile_id` to match the session, and returns 403 otherwise.
- **Exports.** Backup download, CSV export and restore cover only the logged-in profile. The account list is never exported.
- **Static files.** The Calendar route serves only front-end file types. Python files, `_*` files and dotfiles return 404.

## Transactions and concurrency

- **One transaction per request.** Every Finance store read and write in a request shares one database session. It commits when the request finishes and rolls back if the request crashes. A multi-store change is therefore all-or-nothing: renaming an account rewrites its transactions and the account list together. A deliberate error response, such as a 503 "AI not configured", still commits.
- **Commit failures.** If the commit itself fails, the user gets a 500 page. A "saved" page is never shown for data that wasn't saved.
- **Shared profile list.** Sign-up, rename, password changes and deletion each hold a PostgreSQL advisory lock for the whole read-modify-write. Simultaneous sign-ups can't erase each other, and two accounts can't take one name.
- **Per-store row locks.** `SELECT … FOR UPDATE` on `finance_stores` serialises writers to the same store.
- **Calendar saves.** These carry a `version`. A stale save from another device gets a 409, and the browser merges.
- **Workers and threads.** Several gunicorn workers and threads are safe.
- **Local JSON mode.** Writes there are atomic per file, not per request. It is for local development only.

## Passwords

- **Storage.** Passwords are stored only as salted Werkzeug hashes (`generate_password_hash`, scrypt), in `finance_profiles.password_hash`, and checked with `check_password_hash`.
- **Old profiles.** Profiles created before passwords existed have no hash. The first login with that name sets one. The claim is atomic, so only the first person succeeds.
- **Migration.** The migration script hashes any plaintext `password` found in old JSON. The same password then still works.
- **Forgotten password.** With `DATABASE_URL` set, run:

```
python -m Finance.profile_admin reset-password --name "<name>"
```

## Environment variables

| Variable | Where | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Render web service (required) | Internal Database URL of the Render PostgreSQL. `postgres://` is converted automatically. |
| `FLASK_SECRET_KEY` | Render (required) | At least 32 random characters, kept stable. Generate one with `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `CLOUDINARY_URL` | Render (required); GitHub secret (optional) | Receipt images |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | optional | AI features |
| `BACKUP_DATABASE_URL` | GitHub repository secret | **External** Database URL, used by the daily backup job |
| `BACKUP_PASSPHRASE` | GitHub secret, `backup/backup.env`, password manager | Encrypts and decrypts backups. At least 20 characters. **Losing it makes every backup unreadable.** |
| `RENDER` | set by Render | Enables the production checks |

Credentials exist only in Render, in GitHub secrets, and in git-ignored local files (`.env`, `backup/backup.env`). They are never in code. `.env.example` and `docker-compose.yml` hold only a password for the local Docker database.

## Local development

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -r Finance/requirements-dev.txt
python app.py                         # JSON + SQLite mode, no database needed
# or with PostgreSQL (Docker Desktop):
docker compose up -d postgres
Copy-Item .env.example .env           # points at the local container
python app.py                         # http://127.0.0.1:5050
```

Never put the production URL in `.env`, because `python app.py` would then write to production. Production credentials for backups belong in `backup/backup.env`.

## Production deployment on Render

Flow: GitHub `main` → Render Web Service (auto-deploy) → Render PostgreSQL.

| Setting | Value |
| --- | --- |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn app:app --workers 2 --threads 4 --timeout 60` |
| Environment | `DATABASE_URL` (**Internal** URL), `FLASK_SECRET_KEY`, `CLOUDINARY_URL`, optional `GEMINI_API_KEY` |
| Migration command | none at deploy time. Tables are created on startup. The one-off data migration is below. |

There is deliberately no `render.yaml` here. The service is configured in the dashboard, and a Blueprint sync could create new or paid resources.

## Render Free PostgreSQL limitations

These are from Render's documentation, checked in October 2026:

| Limit | Consequence |
| --- | --- |
| **Expires 30 days after creation.** Render deletes it, with all data, 14 days later unless it is upgraded. | The free database is not permanent storage. Plan to either upgrade, or recreate it and restore from backup. |
| **No backups of any kind** (no point-in-time recovery, no dashboard exports) | The independent backup below is the *only* copy outside the database |
| 1 GB storage, one free database per workspace, maintenance restarts at any time | Restarts are harmless because data is committed. Watch the size. |

Paid Render PostgreSQL adds point-in-time recovery (3 days on Hobby workspaces, 7 days on Pro and higher) and downloadable daily logical backups. Keep the independent backup anyway, because it also protects against account-level problems.

To stay on the free plan: before day 30, create a new free database (after deleting the old one, since only one is allowed) and run the [emergency procedure](#emergency-data-recovery) with the latest backup. Take a fresh backup right before deleting the old database.

## JSON migration

`scripts/migrate_json_to_postgres.py` moves the following into the database in `DATABASE_URL`:

- local Finance JSON, including pre-profile flat files;
- `.data/habits.db`;
- any store or Calendar state still held as a whole JSON blob in the database.

```powershell
python scripts/backup_local_files.py                 # 1. snapshot local files (manifest + SHA-256)
$env:DATABASE_URL = "<External Database URL>"
python scripts/backup_database.py backup --encrypt   # 2. back up the target database
python scripts/migrate_json_to_postgres.py           # 3. preview, writes nothing
python scripts/migrate_json_to_postgres.py --apply   # 4. migrate + validate
```

- **Validates first.** All files are checked before anything is written, and any problem aborts. Source files are only read.
- **Idempotent.** Profiles merge by id, and stores or Habits already present are skipped and counted as "Duplicates skipped". Running it twice never doubles anything.
- **Never merges accounts by name.** A profile whose name belongs to a different account is reported, not merged.
- **Preserves everything.** Original ids, record order, timestamps, transfer, goal and debt links, and Habit check-ins all carry over.
- **Validates the result automatically.** Every imported store is read back and compared with its JSON. For transactions, the script also uses SQL to check count, ids, income, expense, per-account balances and ownership against the JSON. Any difference stops it with an error.

The output looks like this:

```
Users migrated: 1
Profiles migrated: 1
Expenses migrated: 14
Income migrated: 8
Diary entries migrated: 0
Calendar records migrated: 0
Habits migrated: 0
Other records migrated: 17
Duplicates skipped: 0
...
Validation (JSON source vs PostgreSQL, computed with SQL):
  My Finance: 22 records, income 1544.14, expense 320.74, 2 account balance(s): JSON = PostgreSQL
```

## Backup

`scripts/backup_database.py` takes a logical backup of every table in one consistent snapshot. It needs no `pg_dump`.

| Property | How |
| --- | --- |
| Encryption | `--encrypt`: scrypt-derived key plus Fernet (AES-128-CBC with an HMAC-SHA256 integrity tag), using `BACKUP_PASSPHRASE` |
| Integrity | A `<file>.sha256` sidecar on every backup. The file is re-read after writing. `manifest.json` lists every file with its size and hash. Restore refuses on a checksum mismatch or a wrong passphrase. |
| Contents | All tables (accounts, password hashes, Finance, Calendar, Habits), plus receipt images with `--include-receipts` |

**Automatic backup 1, GitHub Actions.** Defined in `.github/workflows/database-backup.yml`. Runs daily at 02:30 Malaysia time.

| Item | Detail |
| --- | --- |
| Location | GitHub artifact storage, outside Render. Free for public repositories. |
| Retention | Daily backups kept 7 days; Sunday backups 28 days (4 weekly); backups from the 1st of a month 90 days (3 monthly) |
| Privacy | The repository is public, so artifacts can be downloaded by other GitHub users and logs are public. Backups are encrypted before upload, and the job prints only totals. |
| Alerting | A failed run, for example a database that is unreachable or gone, makes GitHub email you |
| Caveat | GitHub disables scheduled workflows in public repositories after 60 days without repository activity. It emails a warning first. Click "Enable workflow" in the Actions tab, or push any commit. |
| Run now | Actions → Database backup → Run workflow |

**Automatic backup 2, your PC.** Optional, but recommended as a second copy.

```powershell
# backup\backup.env (git-ignored):
#   DATABASE_URL=<External Database URL>
#   BACKUP_PASSPHRASE=<same passphrase>
powershell -ExecutionPolicy Bypass -File scripts\schedule_local_backup.ps1 -Time "21:00"
# optional: -BackupDir "D:\OneDrive\DailyPlannerBackups"  (a private synced folder = off-site copy)
Start-ScheduledTask -TaskName "Daily Planner database backup"   # run once now
```

The task runs `backup --encrypt --rotate`. It keeps the newest backup of each of the last 7 days, the last 4 weeks and the last 3 months, which is at most 14 files. It runs at the next opportunity if the PC was off, and logs to `backup.log` in the backup folder.

**Manual backup** (before migrations or risky changes):

```powershell
python scripts/backup_database.py backup --encrypt
python scripts/backup_database.py verify backup\database\<file>.json.gz.enc
```

## Restore

```powershell
$env:DATABASE_URL = "<External URL of a NEW, EMPTY database>"
$env:BACKUP_PASSPHRASE = "<passphrase>"
python scripts/backup_database.py verify  <file>.json.gz.enc
python scripts/backup_database.py restore <file>.json.gz.enc            # add --restore-receipts if Cloudinary was lost
python scripts/verify_database.py --against <file>.json.gz.enc          # MATCH = every account's counts and totals equal
```

Restore is safe by design:

- It verifies the checksum and the encryption tag before writing.
- It refuses any database that already holds data, so it can never overwrite anything.
- It writes in one transaction and checks row counts, rolling back on any difference.
- It resets id sequences, so new rows work immediately.

## Disaster recovery

Each scenario below was exercised against a real PostgreSQL 16, with 42 automated checks:

| Scenario | Result |
| --- | --- |
| Web service restart | data present |
| Redeploy: fresh code copy, empty filesystem | data present; no user files written to disk |
| Two workers booting together on an empty database, then one restarting | no schema race; both see identical data |
| Hard crash (process killed) right after a write | the committed write survives |
| Database deleted → new database → restore latest encrypted backup | the site shows identical records, totals, Calendar and Habits; login works; a wrong password is rejected; new writes work |
| Account A deleted | all of A's rows gone; B completely intact |

## EMERGENCY DATA RECOVERY

Use this when the Render PostgreSQL is unavailable, expired or deleted. Run everything from the repository folder on your PC, in PowerShell.

**1. Create a new PostgreSQL.** Render Dashboard → New → PostgreSQL. Use the same region as the web service. Once it's "Available", copy the **Internal Database URL** and the **External Database URL**.

**2. Get the latest backup.**

```powershell
# From GitHub Actions (newest successful run):
gh run list --workflow database-backup.yml --status success -L 3
gh run download <run-id> -D restore-download
# Or from this PC: newest file listed in backup\database\manifest.json
```

**3. Verify the backup file.**

```powershell
$env:BACKUP_PASSPHRASE = "<passphrase from your password manager>"
python scripts/backup_database.py verify restore-download\<artifact>\daily-planner-<stamp>.json.gz.enc
```

**4. Restore into the new database.**

```powershell
$env:DATABASE_URL = "<NEW External Database URL>"
python scripts/backup_database.py restore restore-download\<artifact>\daily-planner-<stamp>.json.gz.enc
#   only if the Cloudinary account was also lost: add --restore-receipts and set $env:CLOUDINARY_URL
```

**5. Verify users.**

```powershell
python -m Finance.profile_admin list
```

**6. Verify Finance and all other data against the backup.**

```powershell
python scripts/verify_database.py --against restore-download\<artifact>\daily-planner-<stamp>.json.gz.enc
# must end with: MATCH: every profile's counts and totals equal the backup
```

**7. Point the website at the new database.** Render → Web Service → Environment → set `DATABASE_URL` to the **NEW Internal Database URL** → Save. Then Manual Deploy → "Deploy latest commit".

**8. Test authentication.** Open the site, log in with your account, and check that a wrong password is rejected.

**9. Test user isolation.** Log in as a second account in a private window. Each account must see only its own records, Calendar and Habits.

**10. Re-arm backups.** GitHub → Settings → Secrets → update `BACKUP_DATABASE_URL` to the new External URL. Update `backup\backup.env` too. Then Actions → Database backup → Run workflow, and confirm it succeeds.

## Data retention

- **User data** is kept until the user deletes their account. Deleting an account removes that account's rows and receipt images. A record deleted in Finance first goes to Trash, which is a soft delete, until purged.
- **Backups** follow the retention above, kept at most about 90 days. Data deleted by a user therefore disappears from backups within 90 days.
- **The pre-migration local snapshot** in `backup/json-before-postgresql/` stays on your PC until you delete it.

## Schema changes

- **New tables** are created automatically on the next start.
- **New optional fields** need no schema change: they land in `extra` and round-trip unchanged.
- **Altering existing columns** is not done by `create_all`. Write an explicit SQL migration and test it on a restored copy first. Adopt Alembic, with a baseline revision of the current schema, once such changes become regular.

## Tests

```powershell
python -m pytest Finance/tests                                   # JSON-file mode
$env:FINANCE_TEST_DB = "1"; python -m pytest Finance/tests       # SQL tables on SQLite
$env:FINANCE_TEST_POSTGRES_URL = "postgresql://user@localhost:5432/daily_planner_test"
python -m pytest Finance/tests                                   # real PostgreSQL (DB name must contain "test")
```

Destructive tests run only against databases whose name contains `test`. Nothing in the test suite or the scripts drops, truncates or deletes production data. Restore refuses non-empty databases.

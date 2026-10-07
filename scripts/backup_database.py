"""Independent, encrypted, verifiable backup / restore of the Daily Planner database.

Needs only Python and the app's own dependencies (no pg_dump), so it runs
anywhere that can reach the database: your PC, or the scheduled GitHub
Actions job in .github/workflows/database-backup.yml.

    # Encrypted backup (recommended). BACKUP_PASSPHRASE must be set.
    python scripts/backup_database.py backup --encrypt --rotate
        -> backup/database/daily-planner-YYYYmmdd-HHMMSS.json.gz.enc
           + .sha256 checksum, + manifest.json updated

    # Check a backup without touching any database (checksum, decrypt, parse)
    python scripts/backup_database.py verify <file>

    # Restore into an EMPTY database (e.g. a brand-new Render PostgreSQL)
    python scripts/backup_database.py restore <file> [--restore-receipts]

Every command reads DATABASE_URL (and BACKUP_PASSPHRASE for encrypted files)
from the environment, or from backup/backup.env (git-ignored). Keep the
production URL there rather than in .env, so local development
(`python app.py`, which reads .env) can never write to production.

Safety:

* Restore verifies the SHA-256 checksum and the encryption's integrity tag
  first, and refuses to write into a database that already holds any data,
  so it can never overwrite or mix with existing data.
* Backups contain personal data and password hashes. backup/ is git-ignored.
  Store copies only privately, and always encrypted when off your own PC.
* Without the passphrase an encrypted backup cannot be restored. Keep the
  passphrase in a password manager, separate from the backup files.

Retention (``--rotate``, in the output folder): keeps the newest backup of
each of the last 7 days, the last 4 ISO weeks and the last 3 months. Older
backup files in that folder are deleted; nothing else is touched.
"""
import argparse
import base64
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sqlalchemy import Date, DateTime, LargeBinary, func, insert, select, text  # noqa: E402

from Finance import database  # noqa: E402

FORMAT = "daily-planner-backup"
FORMAT_VERSION = 1
DEFAULT_DIR = os.path.join(ROOT, "backup", "database")
BACKUP_ENV_FILE = os.path.join(ROOT, "backup", "backup.env")
ENCRYPTED_MAGIC = b"DPBACKUP-ENC1\n"
_NAME_RE = re.compile(r"^daily-planner-(\d{8}-\d{6})\.json\.gz(\.enc)?$")
RETENTION = {"daily": 7, "weekly": 4, "monthly": 3}


class BackupError(Exception):
    pass


def _metadata():
    database._tables()
    database.calendar_tables()
    return database.Base.metadata


def _engine():
    return database._engine(database.database_url())


# ---------- encoding ----------

def _encode(value):
    if isinstance(value, (dt.datetime, dt.date)):
        return {"$dt": value.isoformat()}
    if isinstance(value, bytes):
        return {"$b64": base64.b64encode(value).decode("ascii")}
    return value


def _decode(column, value):
    if isinstance(value, dict) and set(value) == {"$dt"}:
        if isinstance(column.type, DateTime):
            return dt.datetime.fromisoformat(value["$dt"])
        if isinstance(column.type, Date):
            return dt.date.fromisoformat(value["$dt"])
    if isinstance(value, dict) and set(value) == {"$b64"} and isinstance(column.type, LargeBinary):
        return base64.b64decode(value["$b64"])
    return value


# ---------- encryption (AES-128-CBC + HMAC-SHA256 via Fernet, scrypt KDF) ----------

def _passphrase():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
    except ImportError:
        pass
    value = os.environ.get("BACKUP_PASSPHRASE", "")
    if len(value) < 16:
        raise BackupError("Set BACKUP_PASSPHRASE (at least 16 characters) to encrypt or decrypt backups.")
    return value.encode("utf-8")


def _fernet(passphrase, salt):
    from cryptography.fernet import Fernet

    key = hashlib.scrypt(passphrase, salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_bytes(data, passphrase):
    salt = secrets.token_bytes(16)
    return ENCRYPTED_MAGIC + salt + _fernet(passphrase, salt).encrypt(data)


def decrypt_bytes(blob, passphrase):
    from cryptography.fernet import InvalidToken

    salt = blob[len(ENCRYPTED_MAGIC):len(ENCRYPTED_MAGIC) + 16]
    try:
        return _fernet(passphrase, salt).decrypt(blob[len(ENCRYPTED_MAGIC) + 16:])
    except InvalidToken as error:
        raise BackupError("Cannot decrypt: wrong BACKUP_PASSPHRASE, or the file was modified.") from error


# ---------- checksums ----------

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_checksum(path):
    value = sha256_file(path)
    with open(path + ".sha256", "w", encoding="ascii", newline="\n") as stream:
        stream.write(f"{value}  {os.path.basename(path)}\n")
    return value


def verify_checksum(path, required=True):
    sidecar = path + ".sha256"
    if not os.path.isfile(sidecar):
        if required:
            raise BackupError(f"Missing checksum file {os.path.basename(sidecar)}; pass --skip-checksum to override.")
        return None
    with open(sidecar, "r", encoding="ascii") as stream:
        expected = stream.read().split()[0].lower()
    actual = sha256_file(path)
    if actual != expected:
        raise BackupError(f"Checksum mismatch for {os.path.basename(path)}: the file is damaged or was altered.")
    return actual


# ---------- backup ----------

def _download_receipts(connection):
    """{"<profile_id>/<filename>": base64 bytes} for every receipt in Cloudinary."""
    if not os.environ.get("CLOUDINARY_URL", "").strip():
        raise BackupError("--include-receipts needs CLOUDINARY_URL.")
    from Finance import receipt_storage

    files = {}
    rows = connection.execute(select(database.ReceiptAsset)).mappings().all()
    for row in rows:
        content, _ = receipt_storage.load(row["profile_id"], row["filename"])
        if content is not None:
            files[f"{row['profile_id']}/{row['filename']}"] = base64.b64encode(content).decode("ascii")
    return files


def backup(out_path=None, encrypt=False, include_receipts=False, folder=DEFAULT_DIR):
    """Write one backup file (+ .sha256). Returns (path, {table: rows})."""
    metadata = _metadata()
    engine = _engine()
    passphrase = _passphrase() if encrypt else None
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = out_path or os.path.join(folder, f"daily-planner-{stamp}.json.gz" + (".enc" if encrypt else ""))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tables = {}
    receipts = {}
    # One REPEATABLE READ transaction = one consistent snapshot of all tables.
    with engine.connect() as connection:
        if engine.dialect.name == "postgresql":
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
        with connection.begin():
            for table in metadata.sorted_tables:
                order = list(table.primary_key.columns) or list(table.columns)
                rows = connection.execute(select(table).order_by(*order)).mappings().all()
                tables[table.name] = [{key: _encode(value) for key, value in row.items()} for row in rows]
            if include_receipts:
                receipts = _download_receipts(connection)
    document = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dialect": engine.dialect.name,
        "row_counts": {name: len(rows) for name, rows in tables.items()},
        "tables": tables,
        "receipt_files": receipts,
    }
    payload = gzip.compress(json.dumps(document, ensure_ascii=False).encode("utf-8"))
    if encrypt:
        payload = encrypt_bytes(payload, passphrase)
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "wb") as stream:
        stream.write(payload)
    os.replace(tmp_path, out_path)
    _write_checksum(out_path)
    read_backup(out_path)  # prove the file just written can be read back
    return out_path, document["row_counts"]


def read_backup(path, verify=True):
    if verify:
        verify_checksum(path)
    with open(path, "rb") as stream:
        blob = stream.read()
    if blob.startswith(ENCRYPTED_MAGIC):
        blob = decrypt_bytes(blob, _passphrase())
    try:
        document = json.loads(gzip.decompress(blob).decode("utf-8"))
    except (OSError, ValueError) as error:
        raise BackupError(f"{path} is not a readable Daily Planner backup.") from error
    if document.get("format") != FORMAT or document.get("version") != FORMAT_VERSION:
        raise BackupError(f"{path} is not a Daily Planner backup (format {FORMAT} v{FORMAT_VERSION}).")
    counts = {name: len(rows) for name, rows in document["tables"].items()}
    if document.get("row_counts", counts) != counts:
        raise BackupError(f"{path}: row counts don't match the backup's own record.")
    return document


# ---------- manifest + retention ----------

def _backup_files(folder):
    found = []
    for name in os.listdir(folder) if os.path.isdir(folder) else []:
        match = _NAME_RE.match(name)
        if match:
            stamp = dt.datetime.strptime(match.group(1), "%Y%m%d-%H%M%S")
            found.append((stamp, os.path.join(folder, name)))
    return sorted(found, reverse=True)


def select_retained(stamps, today=None, retention=RETENTION):
    """The stamps to keep: newest per day for the last N days, per ISO week
    for the last N weeks, per month for the last N months."""
    keep = set()
    buckets = {
        "daily": lambda s: s.date(),
        "weekly": lambda s: s.isocalendar()[:2],
        "monthly": lambda s: (s.year, s.month),
    }
    for kind, bucket in buckets.items():
        seen = []
        for stamp in sorted(stamps, reverse=True):
            key = bucket(stamp)
            if key not in seen:
                seen.append(key)
                if len(seen) <= retention[kind]:
                    keep.add(stamp)
    return keep


def rotate(folder):
    files = _backup_files(folder)
    keep = select_retained([stamp for stamp, _ in files])
    removed = []
    for stamp, path in files:
        if stamp not in keep:
            for victim in (path, path + ".sha256"):
                if os.path.exists(victim):
                    os.remove(victim)
            removed.append(os.path.basename(path))
    return removed


def write_manifest(folder):
    entries = []
    for stamp, path in _backup_files(folder):
        sidecar = path + ".sha256"
        checksum = open(sidecar, encoding="ascii").read().split()[0] if os.path.isfile(sidecar) else None
        entries.append({
            "file": os.path.basename(path),
            "created_utc": stamp.isoformat(),
            "bytes": os.path.getsize(path),
            "sha256": checksum,
            "encrypted": path.endswith(".enc"),
        })
    manifest = {"format": FORMAT + "-manifest", "updated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "retention": RETENTION, "backups": entries}
    with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2)
    return manifest


# ---------- restore ----------

def _restore_receipts(document):
    if not os.environ.get("CLOUDINARY_URL", "").strip():
        raise BackupError("--restore-receipts needs CLOUDINARY_URL.")
    import cloudinary
    import cloudinary.uploader

    cloudinary.config(secure=True)
    assets = {(r["profile_id"], r["filename"]): r for r in document["tables"].get("receipt_assets", [])}
    restored = 0
    for key, encoded in document.get("receipt_files", {}).items():
        profile_id, filename = key.split("/", 1)
        asset = assets.get((profile_id, filename))
        if not asset:
            continue
        try:
            cloudinary.uploader.upload(base64.b64decode(encoded), resource_type="image", type="private",
                                       public_id=asset["storage_key"], overwrite=False, format=asset["format"])
            restored += 1
        except Exception as error:  # an existing asset is fine; report anything else
            print(f"  receipt {key}: {error}", file=sys.stderr)
    return restored


def restore(path, skip_checksum=False, restore_receipts=False):
    if skip_checksum:
        document = read_backup(path, verify=False)
    else:
        document = read_backup(path)
    metadata = _metadata()
    engine = _engine()
    unknown = set(document["tables"]) - set(metadata.tables)
    if unknown:
        raise BackupError(f"Backup has tables this app version doesn't know: {sorted(unknown)}")
    with engine.begin() as connection:
        not_empty = [table.name for table in metadata.sorted_tables
                     if connection.execute(select(func.count()).select_from(table)).scalar()]
        if not_empty:
            raise BackupError("Refusing to restore: the target database already has data in "
                              f"{', '.join(not_empty)}. Restore into a new, empty database.")
        counts = {}
        for table in metadata.sorted_tables:  # parents before children (foreign keys)
            rows = document["tables"].get(table.name, [])
            if rows:
                connection.execute(insert(table), [
                    {key: _decode(table.c[key], value) for key, value in row.items()} for row in rows])
            counts[table.name] = len(rows)
        if engine.dialect.name == "postgresql":
            # Restored explicit ids; move each serial sequence past them.
            for table in metadata.sorted_tables:
                key_columns = list(table.primary_key.columns)
                if len(key_columns) == 1 and key_columns[0].type.python_type is int:
                    column = key_columns[0]
                    connection.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{table.name}', '{column.name}'), "
                        f"COALESCE((SELECT MAX({column.name}) FROM {table.name}), 0) + 1, false) "
                        f"WHERE pg_get_serial_sequence('{table.name}', '{column.name}') IS NOT NULL"))
        for table in metadata.sorted_tables:
            if connection.execute(select(func.count()).select_from(table)).scalar() != counts[table.name]:
                raise BackupError(f"Row count check failed for {table.name}; the restore was rolled back.")
    receipts = _restore_receipts(document) if restore_receipts else None
    return counts, receipts


# ---------- CLI ----------

def _print_counts(counts, quiet=False):
    if quiet:
        print(f"  tables: {len(counts)}, total rows: {sum(counts.values())}")
        return
    for name, count in sorted(counts.items()):
        if count:
            print(f"  {name}: {count}")
    print(f"  total rows: {sum(counts.values())}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Back up or restore the Daily Planner database.")
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("backup", help="write a backup of every table (+ .sha256)")
    make.add_argument("--out", help="output file (default: <dir>/daily-planner-<UTC timestamp>.json.gz[.enc])")
    make.add_argument("--dir", default=DEFAULT_DIR, help="backup folder, e.g. a private synced cloud folder (default: backup/database)")
    make.add_argument("--encrypt", action="store_true", help="encrypt with BACKUP_PASSPHRASE (recommended)")
    make.add_argument("--rotate", action="store_true", help="apply 7 daily / 4 weekly / 3 monthly retention")
    make.add_argument("--include-receipts", action="store_true", help="also store receipt images (needs CLOUDINARY_URL)")
    make.add_argument("--quiet", action="store_true", help="print totals only (for public CI logs)")
    load = commands.add_parser("restore", help="restore a backup into an EMPTY database")
    load.add_argument("path")
    load.add_argument("--skip-checksum", action="store_true", help="restore even without a .sha256 file")
    load.add_argument("--restore-receipts", action="store_true", help="re-upload receipt images to Cloudinary")
    check = commands.add_parser("verify", help="verify checksum + decryption + contents (no database needed)")
    check.add_argument("path")
    check.add_argument("--quiet", action="store_true")
    commands.add_parser("inspect", help="alias of verify").add_argument("path")
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(BACKUP_ENV_FILE)  # real environment variables still win
    except ImportError:
        pass

    try:
        if args.command in ("verify", "inspect"):
            document = read_backup(args.path)
            print(f"OK: checksum, decryption and contents verified. Backup from {document['created_at']}.")
            _print_counts({n: len(r) for n, r in document["tables"].items()}, getattr(args, "quiet", False))
            if document.get("receipt_files"):
                print(f"  receipt images: {len(document['receipt_files'])}")
            return 0
        if not database.configured():
            parser.error("DATABASE_URL is not set (environment or .env).")
        if args.command == "backup":
            path, counts = backup(args.out, encrypt=args.encrypt, include_receipts=args.include_receipts,
                                  folder=args.dir)
            print(f"Backup written and verified: {os.path.basename(path) if args.quiet else path}")
            _print_counts(counts, args.quiet)
            folder = os.path.dirname(os.path.abspath(path))
            if args.rotate:
                removed = rotate(folder)
                print(f"  retention: removed {len(removed)} old backup(s)")
            write_manifest(folder)
        else:
            counts, receipts = restore(args.path, args.skip_checksum, args.restore_receipts)
            print(f"Restored and row counts verified: {args.path}")
            _print_counts(counts)
            if receipts is not None:
                print(f"  receipt images re-uploaded: {receipts}")
    except BackupError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

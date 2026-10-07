"""Snapshot every local data file (JSON stores, SQLite Habits, receipt images,
Calendar migration bundle) before a migration, with an integrity manifest.

    python scripts/backup_local_files.py            # -> backup/json-before-postgresql/<YYYYmmdd-HHMMSS>/
    python scripts/backup_local_files.py verify backup/json-before-postgresql/<stamp>

Each snapshot folder contains the copied files plus:
  manifest.json      timestamp, source path, size and SHA-256 of every file
  checksums.sha256   the same hashes, checkable with `sha256sum -c checksums.sha256`

Sources are only read. backup/ is git-ignored: the copies hold personal data.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ROOT = os.path.join(ROOT, "backup", "json-before-postgresql")
SOURCES = [
    "Finance/data",                # profiles.json, profiles/<id>/*.json, legacy flat *.json
    ".data",                       # habits.db (local SQLite)
    "Finance/static/receipts",     # local receipt images
    "Calendar/_migrate.json",      # optional browser-export bundle
]


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files(source):
    full = os.path.join(ROOT, source)
    if os.path.isfile(full):
        yield source
    elif os.path.isdir(full):
        for folder, _, names in os.walk(full, followlinks=True):
            for name in sorted(names):
                if not name.endswith((".tmp", "-journal", "-wal", "-shm")):
                    yield os.path.relpath(os.path.join(folder, name), ROOT).replace(os.sep, "/")


def _copy(source_path, target_path):
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    if source_path.endswith(".db"):
        # SQLite's own backup API gives a consistent copy even if it is open.
        with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source, sqlite3.connect(target_path) as target:
            source.backup(target)
        source.close()
        target.close()
    else:
        shutil.copy2(source_path, target_path)


def snapshot(root=DEFAULT_ROOT):
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target_dir = os.path.join(root, stamp)
    entries = []
    for source in SOURCES:
        for relative in _files(source):
            source_path = os.path.join(ROOT, relative)
            target_path = os.path.join(target_dir, relative)
            _copy(source_path, target_path)
            entries.append({"path": relative, "bytes": os.path.getsize(target_path), "sha256": _sha256(target_path),
                            "source_sha256": _sha256(source_path)})
    manifest = {"created_local": dt.datetime.now().isoformat(timespec="seconds"),
                "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "source_root": ROOT, "files": entries}
    os.makedirs(target_dir, exist_ok=True)
    with open(os.path.join(target_dir, "manifest.json"), "w", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2)
    with open(os.path.join(target_dir, "checksums.sha256"), "w", encoding="ascii", newline="\n") as stream:
        for entry in entries:
            stream.write(f"{entry['sha256']}  {entry['path']}\n")
    return target_dir, manifest


def verify(target_dir):
    with open(os.path.join(target_dir, "manifest.json"), encoding="utf-8") as stream:
        manifest = json.load(stream)
    bad = [entry["path"] for entry in manifest["files"]
           if not os.path.isfile(os.path.join(target_dir, entry["path"]))
           or _sha256(os.path.join(target_dir, entry["path"])) != entry["sha256"]]
    return manifest, bad


def main(argv=None):
    parser = argparse.ArgumentParser(description="Snapshot local data files with a SHA-256 manifest.")
    parser.add_argument("command", nargs="?", default="snapshot", choices=["snapshot", "verify"])
    parser.add_argument("path", nargs="?", help="snapshot folder (verify)")
    args = parser.parse_args(argv)
    if args.command == "verify":
        manifest, bad = verify(args.path)
        if bad:
            print(f"DAMAGED: {', '.join(bad)}", file=sys.stderr)
            return 1
        print(f"OK: {len(manifest['files'])} files match manifest.json ({manifest['created_local']}).")
        return 0
    target_dir, manifest = snapshot()
    mismatched = [e["path"] for e in manifest["files"] if e["sha256"] != e["source_sha256"] and not e["path"].endswith(".db")]
    print(f"Snapshot: {target_dir}")
    print(f"  files: {len(manifest['files'])}, bytes: {sum(e['bytes'] for e in manifest['files'])}")
    if mismatched:
        print(f"  WARNING: copies differ from sources: {mismatched}", file=sys.stderr)
        return 1
    print("  every copy is byte-identical to its source (SQLite copied via its backup API)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

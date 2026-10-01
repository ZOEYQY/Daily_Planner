"""Upload legacy per-Profile receipt files to Cloudinary and store their keys in PostgreSQL."""
import argparse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from Finance import database, receipt_storage


def migrate(receipts_root):
    if not database.configured():
        raise RuntimeError("DATABASE_URL must point to the shared PostgreSQL database.")
    database.require_postgres()
    receipt_storage.require_cloudinary()
    profiles_document = database.load_document("profiles.json", {})
    profiles = profiles_document.get("profiles", []) if isinstance(profiles_document, dict) else []
    known_profiles = {p.get("id") for p in profiles if isinstance(p, dict) and p.get("id")}

    uploaded = 0
    skipped = 0
    if not os.path.isdir(receipts_root):
        return uploaded, skipped

    legacy_files = [
        filename for filename in os.listdir(receipts_root)
        if os.path.isfile(os.path.join(receipts_root, filename))
    ]
    if legacy_files:
        if len(known_profiles) != 1:
            raise RuntimeError("Flat legacy receipt files need exactly one Profile; move them into that Profile's folder first.")
        profile_id = next(iter(known_profiles))
        for filename in sorted(legacy_files):
            if database.get_receipt_asset(profile_id, filename):
                skipped += 1
                continue
            with open(os.path.join(receipts_root, filename), "rb") as source:
                receipt_storage.upload(profile_id, filename, source.read())
            uploaded += 1

    for profile_id in sorted(os.listdir(receipts_root)):
        profile_dir = os.path.join(receipts_root, profile_id)
        if not os.path.isdir(profile_dir):
            continue
        if profile_id not in known_profiles:
            raise RuntimeError(f"Receipt directory {profile_id} has no matching Profile in PostgreSQL.")
        for filename in sorted(os.listdir(profile_dir)):
            source_path = os.path.join(profile_dir, filename)
            if not os.path.isfile(source_path):
                continue
            if database.get_receipt_asset(profile_id, filename):
                skipped += 1
                continue
            with open(source_path, "rb") as source:
                receipt_storage.upload(profile_id, filename, source.read())
            uploaded += 1
    return uploaded, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--receipts",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "receipts"),
        help="Legacy Finance/static/receipts directory",
    )
    args = parser.parse_args()
    uploaded, skipped = migrate(os.path.abspath(args.receipts))
    print(f"Uploaded {uploaded} receipt images; skipped {skipped} already migrated. Source files were not changed.")


if __name__ == "__main__":
    main()

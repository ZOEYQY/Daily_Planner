"""Safely inspect or recover a Finance Profile from the configured database."""
import argparse
import getpass
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from Finance import database
from Finance.finance_helpers import check_profile_password, find_profile_by_name, load_profiles, set_profile_password


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List Profile IDs and names in the configured PostgreSQL database")
    check = commands.add_parser("check", help="Check a Profile name/password without changing it")
    check.add_argument("--name", required=True)
    reset = commands.add_parser("reset-password", help="Set a new password for a Profile")
    reset.add_argument("--name", required=True)
    args = parser.parse_args()

    if not database.configured():
        raise RuntimeError("DATABASE_URL is not configured; refusing to inspect local JSON fallback.")
    database.require_postgres()
    profiles = load_profiles()

    if args.command == "list":
        if not profiles:
            print("No Profiles found in this PostgreSQL database.")
            return 1
        for profile in profiles:
            password_state = "set" if profile.get("password_hash") else "not set"
            print(f"{profile['id']}\t{profile['name']}\tpassword={password_state}")
        return 0

    profile = find_profile_by_name(profiles, args.name)
    if not profile:
        print("Profile not found in this PostgreSQL database.")
        return 1

    if args.command == "check":
        password = getpass.getpass("Current password: ")
        if check_profile_password(profile, password):
            print(f"Profile found and password matches: {profile['name']}")
            return 0
        print(f"Profile found, but password does not match: {profile['name']}")
        return 1

    password = getpass.getpass("New password (at least 6 characters): ")
    confirmation = getpass.getpass("Repeat new password: ")
    if len(password) < 6:
        print("Password must be at least 6 characters.")
        return 1
    if password != confirmation:
        print("Passwords do not match.")
        return 1
    set_profile_password(profile["id"], password)
    print(f"Password reset successfully for Profile: {profile['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
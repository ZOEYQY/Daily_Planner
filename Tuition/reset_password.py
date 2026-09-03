"""Reset the teacher password without touching any data.

    python reset_password.py
"""
import sqlite3
from getpass import getpass

from tuition import resolve_data_dir
import os

from werkzeug.security import generate_password_hash


def main():
    db_path = os.path.join(resolve_data_dir(), "tuition.db")
    if not os.path.exists(db_path):
        print("No database yet — just open the app and create an account.")
        return
    print(f"Database: {db_path}")
    pw = getpass("New password: ")
    pw2 = getpass("Confirm: ")
    if pw != pw2 or len(pw) < 4:
        print("Passwords did not match or too short. Nothing changed.")
        return
    con = sqlite3.connect(db_path)
    con.execute("UPDATE settings SET password_hash = ? WHERE id = 1",
                (generate_password_hash(pw),))
    con.commit()
    con.close()
    print("Password updated. Your students, classes and payments are untouched.")


if __name__ == "__main__":
    main()

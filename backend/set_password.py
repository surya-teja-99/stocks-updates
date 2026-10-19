#!/usr/bin/env python3
"""Set the login password for the Stocks Updates web app.

Run:
    python backend/set_password.py

Stores a salted hash in backend/.app_password (gitignored, never committed).
The login gate activates on the next web app reload.
"""

import getpass
import os

from werkzeug.security import generate_password_hash

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".app_password")


def main():
    pw = getpass.getpass("New password: ")
    if not pw:
        print("Password cannot be empty.")
        raise SystemExit(1)
    if pw != getpass.getpass("Confirm password: "):
        print("Passwords do not match.")
        raise SystemExit(1)
    with open(PATH, "w") as f:
        f.write(generate_password_hash(pw))
    try:
        os.chmod(PATH, 0o600)
    except OSError:
        pass
    print("Password saved. Reload the web app to activate the login gate.")


if __name__ == "__main__":
    main()

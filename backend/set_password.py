#!/usr/bin/env python3
"""Set the owner login (email + password) for the Stocks Updates web app.

Run:
    python backend/set_password.py

Typing is visible (hidden prompts are unreliable in web consoles).
Stores email + salted password hash in backend/.app_password
(gitignored, never committed). The login gate activates on reload.
"""

import json
import os

from werkzeug.security import generate_password_hash

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".app_password")


def main():
    for _ in range(3):
        email = input("Login email: ").strip().lower()
        if not email or "@" not in email:
            print("Enter a valid email, try again.")
            continue
        pw = input("New password (typing is visible): ").strip()
        if not pw:
            print("Password cannot be empty, try again.")
            continue
        if pw != input("Confirm password: ").strip():
            print("Passwords do not match, try again.")
            continue
        break
    else:
        print("Too many tries - run the script again.")
        raise SystemExit(1)
    with open(PATH, "w") as f:
        json.dump({"email": email,
                   "password_hash": generate_password_hash(pw)}, f)
    try:
        os.chmod(PATH, 0o600)
    except OSError:
        pass
    print("Login saved. Reload the web app to activate the login gate.")


if __name__ == "__main__":
    main()

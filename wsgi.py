#!/usr/bin/env python3
"""WSGI entry point for PythonAnywhere (and any WSGI server).

PythonAnywhere's web app setup imports ``application`` from this file.
The SQLite database file (stocks.db) is created on first run next to it
and persists on PythonAnywhere's filesystem.
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.app import app as application  # noqa: E402
from backend.app import init_db  # noqa: E402

# Make sure the tables exist in the worker process (idempotent).
init_db()

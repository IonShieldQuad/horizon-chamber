"""pytest configuration for Horizon Chamber tests.

Automatically adds the project root to sys.path so that test files can
import app modules without the ``sys.path.insert(0, ...)`` boilerplate.
Test environment variables are also set here so individual test files
don't need to repeat them.
"""

import os
import sys
from pathlib import Path

# Add the project root to sys.path (parent of this tests/ directory)
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# ── Global test environment defaults ─────────────────────────────────────
# Individual test files may override these, but these defaults ensure
# imports don't fail due to missing environment variables.

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-placeholder")
os.environ.setdefault(
    "DATABASE_PATH",
    os.path.join(str(Path(__file__).resolve().parent), "test_horizon.db"),
)
os.environ.setdefault("FEED_API_KEY", "test-feed-key")
os.environ.setdefault("FEED_AUTO_FETCH", "false")

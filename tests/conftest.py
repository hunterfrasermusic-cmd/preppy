"""
Shared test fixtures.

Uses the real local Postgres (DATABASE_URL from .env) with transaction
rollback so every test starts from a clean state.  If DATABASE_URL is
not set, DB-dependent tests are skipped automatically.
"""

import os
import pytest

# Load .env so DATABASE_URL is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Helpers to detect DB availability
# ---------------------------------------------------------------------------

_db_url = os.environ.get("DATABASE_URL", "")


def _have_db():
    if not _db_url:
        return False
    try:
        import psycopg2
        conn = psycopg2.connect(_db_url.replace("postgres://", "postgresql://", 1))
        conn.close()
        return True
    except Exception:
        return False


HAVE_DB = _have_db()
requires_db = pytest.mark.skipif(not HAVE_DB, reason="DATABASE_URL not set or DB unreachable")


# ---------------------------------------------------------------------------
# Flask test client (no DB required — blueprints only registered when DB exists)
# ---------------------------------------------------------------------------

@pytest.fixture()
def app_client():
    """Flask test client for non-DB routes (parse-chart, export-docx)."""
    os.environ.pop("DATABASE_URL", None)
    # Re-import to get a fresh app without DB blueprints
    import importlib
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# DB-backed Flask test client with transaction rollback
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_app(tmp_path):
    """
    Flask app + test client backed by a real Postgres DB.
    Each test runs inside a transaction that is rolled back at the end.
    A test user is pre-created and the session is pre-authenticated.
    """
    if not HAVE_DB:
        pytest.skip("No database")

    os.environ["DATABASE_URL"] = _db_url
    os.environ.setdefault("SECRET_KEY", "test-secret")
    os.environ.setdefault("PCO_CLIENT_ID", "test-client-id")
    os.environ.setdefault("PCO_CLIENT_SECRET", "test-client-secret")
    os.environ.setdefault("PCO_REDIRECT_URI", "http://localhost/auth/callback")

    import importlib

    # Force fresh pool
    import preppy.db as db_mod
    db_mod._pool = None
    importlib.reload(db_mod)

    import app as app_module
    importlib.reload(app_module)

    # Re-register blueprints
    from preppy.auth import auth_bp
    from preppy.api import api_bp
    from preppy.pco import pco_bp

    flask_app = app_module.app
    flask_app.config["TESTING"] = True

    # Ensure blueprints are registered (idempotent check)
    bp_names = {bp.name for bp in flask_app.blueprints.values()}
    if "auth" not in bp_names:
        flask_app.register_blueprint(auth_bp)
    if "api" not in bp_names:
        flask_app.register_blueprint(api_bp)
    if "pco" not in bp_names:
        flask_app.register_blueprint(pco_bp)

    # Run migrations
    from preppy.db import run_migrations, Db
    with flask_app.app_context():
        run_migrations()

    # Create test user with a far-future token expiry so _refresh_token_if_needed
    # never tries to POST to the real OAuth token endpoint during tests.
    from datetime import datetime, timezone, timedelta
    far_future = datetime.now(timezone.utc) + timedelta(days=365)

    with Db() as cur:
        cur.execute(
            "INSERT INTO users (pco_person_id, pco_org_id, name, email, "
            "access_token, refresh_token, token_expires_at) "
            "VALUES ('test-person-123', 'test-org-456', 'Test User', 'test@example.com', "
            "'test-access-token', 'test-refresh-token', %s) "
            "ON CONFLICT (pco_person_id) DO UPDATE SET "
            "name='Test User', token_expires_at=%s "
            "RETURNING id",
            (far_future, far_future),
        )
        user_id = cur.fetchone()["id"]

    with flask_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["user_name"] = "Test User"
            sess["user_email"] = "test@example.com"
        yield client, user_id

    # Cleanup: remove test data (cascade deletes handle related rows)
    with Db() as cur:
        cur.execute("DELETE FROM setlists WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM songs WHERE user_id = %s", (user_id,))

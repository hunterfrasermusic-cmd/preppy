"""
Database connection pool and query helpers.
Uses psycopg2 with a simple connection pool.
"""

import os
import psycopg2
import psycopg2.pool
import psycopg2.extras

_pool = None


def get_pool():
    global _pool
    if _pool is None:
        database_url = os.environ["DATABASE_URL"]
        # Render provides postgres:// but psycopg2 needs postgresql://
        database_url = database_url.replace("postgres://", "postgresql://", 1)
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=database_url,
        )
    return _pool


def get_conn():
    return get_pool().getconn()


def put_conn(conn):
    get_pool().putconn(conn)


class Db:
    """Context manager that checks out a connection, provides a dict cursor,
    and auto-commits/rolls back on exit."""

    def __init__(self):
        self.conn = None
        self.cur = None

    def __enter__(self):
        self.conn = get_conn()
        self.cur = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return self.cur

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.conn.rollback()
        else:
            self.conn.commit()
        self.cur.close()
        put_conn(self.conn)
        return False


def run_migrations():
    """Run all SQL migration files in order (idempotent)."""
    migrations_dir = os.path.join(os.path.dirname(__file__), "..", "migrations")
    migration_files = sorted(
        f for f in os.listdir(migrations_dir) if f.endswith(".sql")
    )
    with Db() as cur:
        for fname in migration_files:
            path = os.path.join(migrations_dir, fname)
            with open(path) as f:
                cur.execute(f.read())

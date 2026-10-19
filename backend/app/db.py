"""SQLAlchemy engine + session. Works with SQLite (dev) or Postgres (prod)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


_url = settings.resolved_database_url
if _url.startswith("sqlite"):
    # No pool limit for SQLite (2026-10-03 outage). With a bounded pool the
    # site deadlocked: sync endpoints hold their connection while they wait for
    # a worker thread (to serialize the response), and the worker threads were
    # all waiting for a connection. A SQLite "connection" is a file handle, so
    # NullPool opens one per checkout and closes it on return — nothing to run
    # out of. timeout = SQLite busy wait for a write lock, instead of failing.
    engine = create_engine(_url, connect_args={"check_same_thread": False, "timeout": 30}, poolclass=NullPool)
else:
    engine = create_engine(_url, pool_pre_ping=True, pool_size=10, max_overflow=20, pool_timeout=15)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)
    from . import export_versions  # noqa: F401 (retain immutable exports on mutation)
    from . import backup

    Base.metadata.create_all(engine)
    # Automatically take a pre-migration snapshot before running additive migrations
    backup.create_pre_migration_backup()
    _migrate()


def _migrate() -> None:
    """Idempotent, additive schema changes create_all() can't make on an
    existing table. Each step checks first, so re-running is a no-op."""
    import logging

    from sqlalchemy import inspect, text

    insp = inspect(engine)
    tables = insp.get_table_names()
    # Legacy evidence stays readable, but unknown project identity is never
    # invented by a migration. Re-fetch/re-review before using it as support.
    evidence_columns = {
        "research_source_records": {"project_identity": "VARCHAR(64)"},
        "research_claim_reviews": {"project_identity": "VARCHAR(64)",
            "resolved_review_ids": "JSON", "resolution_rationale": "TEXT"},
    }
    for table, additions in evidence_columns.items():
        if table not in tables:
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        with engine.begin() as conn:
            for column, sql_type in additions.items():
                if column not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
            if engine.dialect.name == "sqlite":
                for action in ("UPDATE", "DELETE"):
                    conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} "
                        f"BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, "
                        "'Evidence journal is append-only'); END"))
            elif engine.dialect.name == "postgresql":
                conn.execute(text("CREATE OR REPLACE FUNCTION scrapper_journal_immutable() "
                    "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
                    "'Evidence journal is append-only'; END; $$"))
                exists = conn.execute(text("SELECT 1 FROM pg_trigger WHERE tgname=:name "
                    "AND tgrelid=to_regclass(:table)"),
                    {"name": f"{table}_immutable", "table": table}).first()
                if not exists:
                    conn.execute(text(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE "
                        f"ON {table} FOR EACH ROW EXECUTE FUNCTION scrapper_journal_immutable()"))
    if "compilations" in tables and "queue_item_id" not in {c["name"] for c in insp.get_columns("compilations")}:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE compilations ADD COLUMN queue_item_id INTEGER"))
    if "social_posts" in tables:
        sp_cols = {c["name"] for c in insp.get_columns("social_posts")}
        if "publish_requests" not in sp_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE social_posts ADD COLUMN publish_requests JSON"))
    if "studio_projects" in tables:
        st_cols = {c["name"] for c in insp.get_columns("studio_projects")}
        if "drive" not in st_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE studio_projects ADD COLUMN drive JSON"))
        if "archive" not in st_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE studio_projects ADD COLUMN archive JSON"))
        if "review" not in st_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE studio_projects ADD COLUMN review JSON"))
    if "resumable_jobs" in tables:
        rj_cols = {c["name"] for c in insp.get_columns("resumable_jobs")}
        if "owner" not in rj_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE resumable_jobs ADD COLUMN owner VARCHAR(40)"))
    if "queue_items" not in tables:
        return
    cols = {c["name"] for c in insp.get_columns("queue_items")}
    with engine.begin() as conn:  # one transaction: all or nothing
        if "media_kind" not in cols:
            conn.execute(text("ALTER TABLE queue_items ADD COLUMN media_kind VARCHAR(16) NOT NULL DEFAULT 'video'"))
        if "media_files" not in cols:
            conn.execute(text("ALTER TABLE queue_items ADD COLUMN media_files JSON NOT NULL DEFAULT '[]'"))
        if "publish_requests" not in cols:
            conn.execute(text("ALTER TABLE queue_items ADD COLUMN publish_requests JSON"))
        if "research" not in cols:
            conn.execute(text("ALTER TABLE queue_items ADD COLUMN research JSON"))
        if "media_url" not in cols:
            conn.execute(text("ALTER TABLE queue_items ADD COLUMN media_url TEXT"))
        if "pinned_at" not in cols:
            conn.execute(text("ALTER TABLE queue_items ADD COLUMN pinned_at TIMESTAMP WITH TIME ZONE"))
        if "post_next_at" not in cols:
            conn.execute(text("ALTER TABLE queue_items ADD COLUMN post_next_at TIMESTAMP WITH TIME ZONE"))
        if "catchup" not in cols:
            conn.execute(text("ALTER TABLE queue_items ADD COLUMN catchup JSON"))
        if "position" not in cols:
            conn.execute(text('ALTER TABLE queue_items ADD COLUMN "position" INTEGER'))
            conn.execute(text('CREATE INDEX IF NOT EXISTS ix_queue_items_position ON queue_items ("position")'))
        # Backfill: unordered items keep their import order.
        conn.execute(text('UPDATE queue_items SET "position" = id WHERE "position" IS NULL'))
    # One queue row per Drive file — stops concurrent syncs double-importing.
    # NULLs stay allowed (compilations/clips have no Drive link).
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_queue_items_drive_link ON queue_items (drive_link)"
            ))
    except Exception as exc:  # existing duplicates — leave the app up, say so
        logging.getLogger("scrapper.db").error("unique drive_link index not created: %s", exc)


async def get_session() -> AsyncIterator[Session]:
    """FastAPI dependency: one session per request.

    Async on purpose (2026-10-03 outage): FastAPI runs the clean-up of a *sync*
    generator dependency on the same 40-thread pool the endpoints use. Under
    load every worker thread sat waiting for a DB connection while the
    connections belonged to finished requests whose s.close() was queued for a
    worker thread — a deadlock that took the whole site down. As an async
    dependency the close runs on the event loop and never needs a worker
    thread. The session itself is still used only by the endpoint's thread
    (created here lazily — no connection until the first query)."""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()

"""
Database setup.

SQLite keeps local dev/demo simple (no server to install). The file lives
next to the `app/` folder by default, NOT in whatever directory uvicorn
happens to be started from, so every teammate sees the same database no
matter where they run the server. Override with the CRM_DB_PATH
environment variable (the tests do this to use a throwaway DB).

Swap the URL for a PostgreSQL one if ever needed; nothing above the
SQLAlchemy ORM layer would change.
"""
import os
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

_DEFAULT_DB = Path(__file__).resolve().parent.parent / "crm.db"
DB_PATH = Path(os.environ.get("CRM_DB_PATH", str(_DEFAULT_DB)))

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    # SQLite only: allow the connection to be shared across the threads
    # FastAPI uses for sync dependencies and websocket handling.
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def add_missing_columns() -> list[str]:
    """Add model columns that an older crm.db does not have yet.

    create_all() creates missing tables but never alters existing ones, so a
    teammate's local DB from before a new column was added would fail with
    "no such column". This handles the additive case only (no renames or
    drops); it returns the "table.column" names it added.
    """
    added = []
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type.compile(engine.dialect)}"
                if column.default is not None and column.default.is_scalar:
                    # literal_processor stores enums the same way the ORM does
                    to_sql = column.type.literal_processor(engine.dialect)
                    ddl += f" NOT NULL DEFAULT {to_sql(column.default.arg)}"
                conn.execute(text(ddl))
                added.append(f"{table.name}.{column.name}")
    return added


def get_db():
    """FastAPI dependency: one DB session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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

from sqlalchemy import create_engine
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


def get_db():
    """FastAPI dependency: one DB session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

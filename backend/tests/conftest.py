"""
Test setup: point the app at a throwaway SQLite file BEFORE it is
imported, so the tests never touch the real crm.db.
"""
import os
import tempfile

os.environ["CRM_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_crm.db")

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.database import Base, engine
    from app.main import app

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as test_client:
        yield test_client

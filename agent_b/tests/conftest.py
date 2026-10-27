"""
Shared fixtures. The CRM backend (Role 2) runs in-process against a
throwaway SQLite file, so the integration tests exercise the real REST
contract, consent check and audit log without a server and never touch
crm.db. No test needs Ollama: the LLM is replaced by a mock transport.
"""
import os
import tempfile

# must happen before anything imports the backend's app.database
os.environ["CRM_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "agent_b_test.db")

import httpx  # noqa: E402
import pytest  # noqa: E402

from followup.crm import RESTCRMClient  # noqa: E402
from followup.drafting import TemplateDrafter  # noqa: E402


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def backend_app():
    from app.database import Base, engine
    from app.main import app

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return app


@pytest.fixture
async def backend_http(backend_app):
    """An HTTP client talking to the in-process backend."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=backend_app), base_url="http://crm") as http:
        yield http


@pytest.fixture
async def rest_client(backend_app):
    client = RESTCRMClient("http://crm", transport=httpx.ASGITransport(app=backend_app))
    yield client
    await client.aclose()


@pytest.fixture
def template():
    return TemplateDrafter()

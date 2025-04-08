import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture(scope="session")
def client():
    """
    Create a TestClient for the FastAPI app
    """
    with TestClient(app) as test_client:
        yield test_client

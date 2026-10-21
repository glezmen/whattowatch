from fastapi.testclient import TestClient

from whattowatch.api.app import create_app
from whattowatch.config import Settings


def test_health() -> None:
    settings = Settings(_env_file=None, session_secret="s")  # type: ignore[call-arg]
    response = TestClient(create_app(settings)).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

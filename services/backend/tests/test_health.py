"""/health 健康检查单测（TestClient 无需真实启动 uvicorn）。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "synetra-backend"
    assert "version" in body


def test_health_version_matches_package():
    from app import __version__

    resp = client.get("/health")
    assert resp.json()["version"] == __version__
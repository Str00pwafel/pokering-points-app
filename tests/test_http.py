"""HTTP endpoint tests via fastapi.testclient: /create CSRF + rate limit,
/maintenance with broken config files, /health & /metrics auth, slash redirect."""

import pytest
from fastapi.testclient import TestClient

import app.routes as routes
from app.core import app
from app.state import sessions
from tests.conftest import SESSION_ID, make_session


@pytest.fixture
def client():
    with TestClient(app, follow_redirects=False) as c:
        yield c


class TestCreate:
    def test_create_redirects_to_session(self, client):
        res = client.post("/create")
        assert res.status_code == 303
        location = res.headers["location"]
        assert location.startswith("/session/")
        assert location.removeprefix("/session/") in sessions

    def test_create_rate_limited_per_ip(self, client):
        assert client.post("/create").status_code == 303
        res = client.post("/create")
        assert res.status_code == 429

    def test_csrf_rejects_foreign_origin(self, client, monkeypatch):
        monkeypatch.setattr(routes, "CORS_ORIGINS", ["https://poker.example.com"])
        res = client.post("/create", headers={"origin": "https://evil.example.com"})
        assert res.status_code == 403

    def test_csrf_accepts_allowed_origin(self, client, monkeypatch):
        monkeypatch.setattr(routes, "CORS_ORIGINS", ["https://poker.example.com"])
        res = client.post("/create", headers={"origin": "https://poker.example.com"})
        assert res.status_code == 303

    def test_server_full_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(routes, "MAX_ACTIVE_SESSIONS", 0)
        assert client.post("/create").status_code == 503


class TestSessionRoutes:
    def test_invalid_session_id_400(self, client):
        assert client.get("/session/short").status_code == 400

    def test_trailing_slash_redirects(self, client):
        # BUG-6: /session/<id>/ used to 404 from the static mount, making the
        # client create a brand-new session instead of joining the linked one.
        res = client.get(f"/session/{SESSION_ID}/")
        assert res.status_code == 308
        assert res.headers["location"] == f"/session/{SESSION_ID}"

    def test_exists_true_and_false(self, client):
        make_session()
        assert client.get(f"/session/{SESSION_ID}/exists").json() == {"exists": True}
        assert client.get(f"/session/{'B' * 16}/exists").json() == {"exists": False}


class TestMaintenance:
    def _set_file(self, monkeypatch, tmp_path, content: str):
        f = tmp_path / "maintenance.json"
        f.write_text(content)
        monkeypatch.setattr(routes, "MAINTENANCE_FILE", str(f))
        routes._maintenance_cache = None

    def test_malformed_json_falls_back_to_defaults(self, client, monkeypatch, tmp_path):
        self._set_file(monkeypatch, tmp_path, "{not json!!")
        res = client.get("/maintenance")
        assert res.status_code == 200
        assert res.json()["enabled"] is False

    def test_invalid_timezone_no_500(self, client, monkeypatch, tmp_path):
        # BUG-1 regression through the full endpoint: a typo'd timezone in the
        # operator-edited file must not turn /maintenance into a 500.
        self._set_file(
            monkeypatch,
            tmp_path,
            '{"enabled": true, "at": "21:00", "timezone": "Not/AZone"}',
        )
        res = client.get("/maintenance")
        assert res.status_code == 200
        assert res.json()["enabled"] is False

    def test_invalid_time_no_500(self, client, monkeypatch, tmp_path):
        self._set_file(monkeypatch, tmp_path, '{"enabled": true, "at": "25:99"}')
        res = client.get("/maintenance")
        assert res.status_code == 200
        assert res.json()["enabled"] is False

    def test_valid_schedule_enables_banner(self, client, monkeypatch, tmp_path):
        self._set_file(
            monkeypatch,
            tmp_path,
            '{"enabled": true, "at": "21:00", "timezone": "Europe/Amsterdam"}',
        )
        body = client.get("/maintenance").json()
        assert body["enabled"] is True
        assert "21:00" in body["message"]

    def test_file_cache_invalidated_by_mtime(self, client, monkeypatch, tmp_path):
        import os

        f = tmp_path / "maintenance.json"
        f.write_text('{"enabled": false}')
        monkeypatch.setattr(routes, "MAINTENANCE_FILE", str(f))
        routes._maintenance_cache = None
        assert client.get("/maintenance").json()["enabled"] is False

        f.write_text('{"enabled": true, "at": "21:00"}')
        os.utime(f, (os.path.getmtime(f) + 5, os.path.getmtime(f) + 5))
        assert client.get("/maintenance").json()["enabled"] is True


class TestMetricsAuth:
    def test_open_when_no_token(self, client, monkeypatch):
        monkeypatch.setattr(routes, "METRICS_TOKEN", "")
        assert client.get("/health").status_code == 200
        assert client.get("/metrics").status_code == 200

    def test_requires_token_when_set(self, client, monkeypatch):
        monkeypatch.setattr(routes, "METRICS_TOKEN", "sekret")
        assert client.get("/health").status_code == 401
        assert client.get("/metrics").status_code == 401
        assert client.get("/health", headers={"authorization": "Bearer wrong"}).status_code == 401

    def test_correct_token_accepted(self, client, monkeypatch):
        monkeypatch.setattr(routes, "METRICS_TOKEN", "sekret")
        headers = {"authorization": "Bearer sekret"}
        assert client.get("/health", headers=headers).status_code == 200
        assert client.get("/metrics", headers=headers).status_code == 200

    def test_healthz_always_open(self, client, monkeypatch):
        monkeypatch.setattr(routes, "METRICS_TOKEN", "sekret")
        assert client.get("/healthz").json() == {"status": "ok"}


class TestGlobalHttpRateLimit:
    def test_429_after_limit(self, client, monkeypatch):
        monkeypatch.setattr(routes, "HTTP_RATE_LIMIT_PER_MINUTE", 3)
        for _ in range(3):
            assert client.get("/version").status_code == 200
        res = client.get("/version")
        assert res.status_code == 429
        assert res.headers["retry-after"] == "60"

    def test_healthz_exempt(self, client, monkeypatch):
        monkeypatch.setattr(routes, "HTTP_RATE_LIMIT_PER_MINUTE", 1)
        for _ in range(5):
            assert client.get("/healthz").status_code == 200

    def test_disabled_when_zero(self, client, monkeypatch):
        monkeypatch.setattr(routes, "HTTP_RATE_LIMIT_PER_MINUTE", 0)
        for _ in range(5):
            assert client.get("/version").status_code == 200

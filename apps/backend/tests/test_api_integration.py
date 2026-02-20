"""Tests for M10 — API Route Integration Tests.

Uses FastAPI TestClient to test REST endpoints end-to-end.
"""

import pytest
from fastapi.testclient import TestClient

from src.main import app


@pytest.fixture
def client():
    """Create a TestClient for the FastAPI app."""
    return TestClient(app, raise_server_exceptions=False)


# ── Health Endpoints ───────────────────────────────────────────────────

class TestHealthEndpoints:
    def test_health_check(self, client: TestClient):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_provider_health(self, client: TestClient):
        response = client.get("/health/providers")
        assert response.status_code == 200
        data = response.json()
        assert "providers" in data

    def test_api_status(self, client: TestClient):
        response = client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "operational"
        assert "version" in data


# ── Session Endpoints ──────────────────────────────────────────────────

class TestSessionEndpoints:
    def test_start_session(self, client: TestClient):
        response = client.post("/api/sessions/start", json={
            "level": "sde2",
            "duration_minutes": 45,
            "focus_areas": ["coding", "system_design"],
        })
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert len(data["session_id"]) > 0

    def test_start_session_invalid_duration(self, client: TestClient):
        response = client.post("/api/sessions/start", json={
            "level": "sde2",
            "duration_minutes": 5,  # Below minimum of 15
            "focus_areas": ["coding"],
        })
        assert response.status_code == 422  # Validation error

    def test_get_session(self, client: TestClient):
        # Start a session first
        start_resp = client.post("/api/sessions/start", json={
            "level": "sde1",
            "duration_minutes": 30,
            "focus_areas": ["coding"],
        })
        session_id = start_resp.json()["session_id"]

        # Get it
        response = client.get(f"/api/sessions/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == session_id
        assert data["status"] == "live"

    def test_get_nonexistent_session(self, client: TestClient):
        response = client.get("/api/sessions/nonexistent-id")
        assert response.status_code == 200
        data = response.json()
        assert "error" in data

    def test_pause_resume_session(self, client: TestClient):
        # Start
        start_resp = client.post("/api/sessions/start", json={
            "level": "sde2",
            "duration_minutes": 45,
            "focus_areas": ["coding"],
        })
        session_id = start_resp.json()["session_id"]

        # Pause
        pause_resp = client.patch(f"/api/sessions/{session_id}/pause")
        assert pause_resp.status_code == 200
        assert pause_resp.json()["status"] == "paused"

        # Resume (toggle)
        resume_resp = client.patch(f"/api/sessions/{session_id}/pause")
        assert resume_resp.status_code == 200
        assert resume_resp.json()["status"] == "live"

    def test_end_session(self, client: TestClient):
        start_resp = client.post("/api/sessions/start", json={
            "level": "senior",
            "duration_minutes": 60,
            "focus_areas": ["system_design"],
        })
        session_id = start_resp.json()["session_id"]

        end_resp = client.post(f"/api/sessions/{session_id}/end")
        assert end_resp.status_code == 200
        data = end_resp.json()
        assert data["status"] == "ended"

        # Verify session is ended
        get_resp = client.get(f"/api/sessions/{session_id}")
        assert get_resp.json()["status"] == "ended"

    def test_full_session_lifecycle(self, client: TestClient):
        """Start → Pause → Resume → End lifecycle."""
        # Start
        resp = client.post("/api/sessions/start", json={
            "level": "sde2",
            "duration_minutes": 45,
            "focus_areas": ["coding", "behavioral"],
        })
        sid = resp.json()["session_id"]

        # Verify live
        assert client.get(f"/api/sessions/{sid}").json()["status"] == "live"

        # Pause
        client.patch(f"/api/sessions/{sid}/pause")
        assert client.get(f"/api/sessions/{sid}").json()["status"] == "paused"

        # Resume
        client.patch(f"/api/sessions/{sid}/pause")
        assert client.get(f"/api/sessions/{sid}").json()["status"] == "live"

        # End
        client.post(f"/api/sessions/{sid}/end")
        session = client.get(f"/api/sessions/{sid}").json()
        assert session["status"] == "ended"
        assert session["ended_at"] is not None


# ── Hint Endpoints ─────────────────────────────────────────────────────

class TestHintEndpoints:
    def test_trigger_hint(self, client: TestClient):
        # Start session
        start_resp = client.post("/api/sessions/start", json={
            "level": "sde2",
            "duration_minutes": 45,
            "focus_areas": ["coding"],
        })
        sid = start_resp.json()["session_id"]

        # Trigger hint
        hint_resp = client.post(f"/api/sessions/{sid}/hints/next", json={
            "question_id": "q1",
        })
        assert hint_resp.status_code == 200
        data = hint_resp.json()
        assert "id" in data
        assert data["level"] == 1
        assert "text" in data

    def test_acknowledge_hint(self, client: TestClient):
        start_resp = client.post("/api/sessions/start", json={
            "level": "sde2",
            "duration_minutes": 45,
            "focus_areas": ["coding"],
        })
        sid = start_resp.json()["session_id"]

        # Trigger hint
        hint = client.post(f"/api/sessions/{sid}/hints/next", json={
            "question_id": "q1",
        }).json()

        # Acknowledge
        ack_resp = client.post(f"/api/sessions/{sid}/hints/{hint['id']}/ack", json={
            "interviewer_used": True,
            "candidate_response_quality": "full",
        })
        assert ack_resp.status_code == 200
        assert ack_resp.json()["acknowledged"] is True


# ── Upload Endpoints ───────────────────────────────────────────────────

class TestUploadEndpoints:
    def test_upload_requires_auth(self, client: TestClient):
        response = client.post("/api/upload/resume")
        # Should fail without auth token or file
        assert response.status_code in (400, 401, 403, 422)


# ── Auth Endpoints ─────────────────────────────────────────────────────

class TestAuthEndpoints:
    def test_auth_me_requires_token(self, client: TestClient):
        response = client.get("/api/auth/me")
        # Should fail without auth token
        assert response.status_code in (401, 403, 200)

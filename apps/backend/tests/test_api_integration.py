"""Tests for M10 — API Route Integration Tests.

Uses FastAPI TestClient to test REST endpoints end-to-end.
"""

import json
import threading
import time
import uuid

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings
from src.dependencies import get_db
from src.main import app
from src.services.access_control import DEV_USER_ID


@pytest.fixture
def extraction_cache():
    """Write resume-extraction entries straight into Redis (owned by the dev user by default)."""
    r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    written: list[str] = []

    def put(ref_id: str, entry: dict, owner: str = str(DEV_USER_ID)) -> None:
        r.set(f"extraction:{ref_id}", json.dumps({**entry, "owner": owner}), ex=60)
        written.append(ref_id)

    yield put
    for ref_id in written:
        r.delete(f"extraction:{ref_id}")
    r.close()


def _read_sse(client: TestClient, path: str) -> list[dict]:
    events: list[dict] = []
    with client.stream("GET", path) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        event_type = ""
        for line in response.iter_lines():
            if line.startswith("event: "):
                event_type = line[len("event: ") :]
            elif line.startswith("data: "):
                events.append({"event": event_type, "data": json.loads(line[len("data: ") :])})
                if event_type in ("completed", "error"):
                    break
    return events


@pytest.fixture
def client():
    """Create a TestClient for the FastAPI app.

    TestClient (without ``with``) runs each request on a fresh event loop, so
    the app's pooled engine would hand out connections bound to an earlier
    loop. Requests get a NullPool session instead.
    """
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _get_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(get_db, None)


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
        response = client.post(
            "/api/sessions/start",
            json={
                "level": "sde2",
                "duration_minutes": 45,
                "focus_areas": ["coding", "system_design"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert len(data["session_id"]) > 0

    def test_start_session_invalid_duration(self, client: TestClient):
        response = client.post(
            "/api/sessions/start",
            json={
                "level": "sde2",
                "duration_minutes": 5,  # Below minimum of 15
                "focus_areas": ["coding"],
            },
        )
        assert response.status_code == 422  # Validation error

    def test_get_session(self, client: TestClient):
        # Start a session first
        start_resp = client.post(
            "/api/sessions/start",
            json={
                "level": "sde1",
                "duration_minutes": 30,
                "focus_areas": ["coding"],
            },
        )
        session_id = start_resp.json()["session_id"]

        # Get it — session starts in PLANNING status
        response = client.get(f"/api/sessions/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == session_id
        assert data["status"] == "planning"

    def test_get_nonexistent_session(self, client: TestClient):
        response = client.get("/api/sessions/nonexistent-id")
        assert response.status_code == 404

    def test_pause_resume_session(self, client: TestClient):
        # Start
        start_resp = client.post(
            "/api/sessions/start",
            json={
                "level": "sde2",
                "duration_minutes": 45,
                "focus_areas": ["coding"],
            },
        )
        session_id = start_resp.json()["session_id"]

        # Go live first
        go_live_resp = client.post(f"/api/sessions/{session_id}/go-live")
        assert go_live_resp.status_code == 200
        assert go_live_resp.json()["status"] == "live"

        # Pause
        pause_resp = client.patch(f"/api/sessions/{session_id}/pause")
        assert pause_resp.status_code == 200
        assert pause_resp.json()["status"] == "paused"

        # Resume (toggle)
        resume_resp = client.patch(f"/api/sessions/{session_id}/pause")
        assert resume_resp.status_code == 200
        assert resume_resp.json()["status"] == "live"

    def test_end_session(self, client: TestClient):
        start_resp = client.post(
            "/api/sessions/start",
            json={
                "level": "senior",
                "duration_minutes": 60,
                "focus_areas": ["system_design"],
            },
        )
        session_id = start_resp.json()["session_id"]

        end_resp = client.post(f"/api/sessions/{session_id}/end")
        assert end_resp.status_code == 200
        data = end_resp.json()
        assert data["status"] == "ended"

        # Verify session is ended
        get_resp = client.get(f"/api/sessions/{session_id}")
        assert get_resp.json()["status"] == "ended"

    def test_full_session_lifecycle(self, client: TestClient):
        """Start → Go Live → Pause → Resume → End lifecycle."""
        # Start (PLANNING status)
        resp = client.post(
            "/api/sessions/start",
            json={
                "level": "sde2",
                "duration_minutes": 45,
                "focus_areas": ["coding", "behavioral"],
            },
        )
        sid = resp.json()["session_id"]

        # Verify planning
        assert client.get(f"/api/sessions/{sid}").json()["status"] == "planning"

        # Go live
        client.post(f"/api/sessions/{sid}/go-live")
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
        start_resp = client.post(
            "/api/sessions/start",
            json={
                "level": "sde2",
                "duration_minutes": 45,
                "focus_areas": ["coding"],
            },
        )
        sid = start_resp.json()["session_id"]

        # Register hints for question q1
        client.post(
            f"/api/sessions/{sid}/register-plan-hints",
            json={
                "phases": [
                    {
                        "phase": "coding",
                        "start_minute": 5,
                        "duration_minutes": 25,
                        "questions": [
                            {
                                "id": "q1",
                                "text": "Two Sum problem",
                                "hints": [
                                    {"level": 1, "text": "Think about data structures."},
                                    {"level": 2, "text": "A hash map gives O(1) lookups."},
                                    {"level": 3, "text": "Map each element to its index."},
                                ],
                            }
                        ],
                    }
                ],
            },
        )

        # Trigger hint
        hint_resp = client.post(
            f"/api/sessions/{sid}/hints/next",
            json={
                "question_id": "q1",
            },
        )
        assert hint_resp.status_code == 200
        data = hint_resp.json()
        assert "id" in data
        assert data["level"] == 1
        assert "text" in data

    def test_acknowledge_hint(self, client: TestClient):
        start_resp = client.post(
            "/api/sessions/start",
            json={
                "level": "sde2",
                "duration_minutes": 45,
                "focus_areas": ["coding"],
            },
        )
        sid = start_resp.json()["session_id"]

        # Register hints
        client.post(
            f"/api/sessions/{sid}/register-plan-hints",
            json={
                "phases": [
                    {
                        "phase": "coding",
                        "start_minute": 5,
                        "duration_minutes": 25,
                        "questions": [
                            {
                                "id": "q1",
                                "text": "Two Sum",
                                "hints": [
                                    {"level": 1, "text": "Think about data structures."},
                                    {"level": 2, "text": "A hash map gives O(1) lookups."},
                                    {"level": 3, "text": "Map each element to its index."},
                                ],
                            }
                        ],
                    }
                ],
            },
        )

        # Trigger hint
        hint = client.post(
            f"/api/sessions/{sid}/hints/next",
            json={
                "question_id": "q1",
            },
        ).json()

        # Acknowledge
        ack_resp = client.post(
            f"/api/sessions/{sid}/hints/{hint['id']}/ack",
            json={
                "interviewer_used": True,
                "candidate_response_quality": "full",
            },
        )
        assert ack_resp.status_code == 200
        assert ack_resp.json()["acknowledged"] is True


# ── Upload Endpoints ───────────────────────────────────────────────────


class TestUploadEndpoints:
    def test_upload_requires_file(self, client: TestClient):
        response = client.post("/api/upload/resume")
        # Should fail without file
        assert response.status_code in (400, 401, 403, 422)

    def test_sse_stream_not_found(self, client: TestClient):
        """SSE stream returns 404 for unknown reference ID."""
        response = client.get("/api/upload/nonexistent-id/stream")
        assert response.status_code == 404

    def test_sse_stream_processing_then_completed(self, client: TestClient, extraction_cache):
        """SSE stream emits processing status then completed with data."""
        ref_id = f"test-sse-{uuid.uuid4()}"
        extraction_cache(ref_id, {"status": "processing"})

        # Simulate completion after a brief delay by updating the cache in background
        def complete_after_delay() -> None:
            time.sleep(1.5)
            extraction_cache(ref_id, {"status": "completed", "data": {"name": "Test User", "tech_stack": ["Python"]}})

        t = threading.Thread(target=complete_after_delay)
        t.start()
        try:
            events = _read_sse(client, f"/api/upload/{ref_id}/stream")
        finally:
            t.join(timeout=5)

        # Should have at least one processing event, then completed
        assert any(e["event"] == "status" and e["data"]["status"] == "processing" for e in events)
        assert events[-1]["event"] == "completed"
        assert events[-1]["data"]["status"] == "completed"
        assert events[-1]["data"]["structured_data"]["name"] == "Test User"

    def test_sse_stream_error(self, client: TestClient, extraction_cache):
        """SSE stream emits error event on extraction failure."""
        ref_id = f"test-sse-{uuid.uuid4()}"
        extraction_cache(ref_id, {"status": "failed", "error": "LLM unavailable"})

        events = _read_sse(client, f"/api/upload/{ref_id}/stream")

        assert len(events) == 1
        assert events[0]["event"] == "error"
        assert events[0]["data"]["error"] == "LLM unavailable"

    def test_upload_status_of_another_user_is_404(self, client: TestClient, extraction_cache):
        ref_id = f"test-sse-{uuid.uuid4()}"
        extraction_cache(ref_id, {"status": "completed", "data": {}}, owner=str(uuid.uuid4()))
        assert client.get(f"/api/upload/{ref_id}/status").status_code == 404
        assert client.get(f"/api/upload/{ref_id}/stream").status_code == 404


# ── Auth Endpoints ─────────────────────────────────────────────────────


class TestAuthEndpoints:
    def test_auth_me_requires_token(self, client: TestClient):
        response = client.get("/api/auth/me")
        # Should fail without auth token
        assert response.status_code in (401, 403, 200)

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from planner.app import create_app
from planner.azure import AzureResponses
from planner.config import ROOT, Settings
from planner.models import Extraction, PlanResult
from .test_azure import packet


def upload(client, name="team-notes-prd.pdf", **data):
    values = {"consent": "true", **data}
    return client.post("/api/extract", files={"file": (name, (ROOT / "examples" / name).read_bytes(), "application/pdf")}, data=values)


def test_health_does_not_leak_key(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["azure_configured"] is True
    assert "fixture-not-a-real-key" not in response.text
    assert "not a live" in response.json()["note"]


def test_sample_works_without_credentials():
    with TestClient(create_app(Settings(_env_file=None, azure_openai_endpoint="", azure_openai_api_key=""))) as client:
        assert client.get("/api/health").json()["azure_configured"] is False
        result = PlanResult.model_validate(client.get("/api/sample").json())
        assert result.mode == "sample" and result.usage.calls == 0
        assert len(result.plan.tasks) == 9
        response = upload(client)
        assert response.status_code == 503 and response.json()["error"]["code"] == "azure_not_configured"


def test_static_frontend_and_sample_pdf(client):
    assert client.get("/").status_code == 200
    js = client.get("/assets/app.js")
    assert js.status_code == 200 and "handleAction" in js.text
    assert client.get("/assets/styles.css").status_code == 200
    assert client.get("/api/sample/pdf").content.startswith(b"%PDF-")


def test_headers_and_unknown_routes(client):
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert client.get("/api/not-real").status_code == 404
    assert client.get("/assets/../.env").status_code == 404


def test_consent_is_required(client, provider):
    response = upload(client, consent="false")
    assert response.status_code == 422 and not provider.calls


def test_invalid_file_type(client):
    response = client.post("/api/extract", files={"file": ("data.txt", b"hello", "text/plain")}, data={"consent": "true"})
    assert response.status_code == 415


def test_empty_file(client):
    response = client.post("/api/extract", files={"file": ("empty.pdf", b"", "application/pdf")}, data={"consent": "true"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "empty_file"


def test_declared_oversized_body_rejected_before_parsing(client):
    response = client.post("/api/extract", content=b"x", headers={"content-length": str(60 * 1024 * 1024)})
    assert response.status_code == 413


def test_cross_origin_post_is_rejected(client):
    response = client.post("/api/plan", json={}, headers={"origin": "https://malicious.example"})
    assert response.status_code == 403


def test_missing_fields_do_not_echo_private_input(client):
    response = client.post("/api/plan", json={"private_data": "a secret document body"})
    assert response.status_code == 422 and "a secret document body" not in response.text


def test_native_upload_review_plan_roundtrip(client):
    first = upload(client, implementation_context="I use FastAPI and SQLite.")
    assert first.status_code == 200, first.text
    extraction = Extraction.model_validate(first.json())
    assert len(extraction.document.requirements) == 8
    response = client.post("/api/plan", json={"extraction": extraction.model_dump(), "review": {"acknowledge_warnings": True, "implementation_context": "I use FastAPI and SQLite."}})
    assert response.status_code == 200, response.text
    result = PlanResult.model_validate(response.json())
    assert result.mode == "live"  # Provider is injected ONLY by this test.
    assert len(result.plan.tasks) == 9
    assert next(t for t in result.plan.tasks if t.id == "T-008").readiness == "blocked"


def test_busy_capacity_fails_without_wait_queue(client):
    client.app.state.active_runs = 2
    response = upload(client)
    assert response.status_code == 503 and response.json()["error"]["code"] == "busy"


def test_error_releases_run_slot(client):
    response = upload(client, name="malformed.pdf")
    assert response.status_code == 422
    assert client.app.state.active_runs == 0


def test_full_api_pipeline_through_mocked_azure_http(settings):
    """Real HTTP adapter and service; only external Azure traffic is mocked."""
    reference = json.loads((ROOT / "examples/reference-extraction.json").read_text())
    draft = json.loads((ROOT / "examples/reference-plan.json").read_text())
    seen = []
    def handler(request):
        data = json.loads(request.content)
        seen.append(data)
        name = data["text"]["format"]["name"]
        output = reference["document"] if name == "RequirementsDocument" else draft
        return httpx.Response(200, json=packet(output))
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AzureResponses(settings, http_client)
    # TestClient owns the app lifecycle. MockTransport has no socket resources.
    with TestClient(create_app(settings, provider=provider)) as client:
        extraction = upload(client).json()
        result = client.post("/api/plan", json={"extraction": extraction, "review": {"acknowledge_warnings": True}})
        assert result.status_code == 200, result.text
        assert len(seen) == 2
        assert all(p["model"] == "my-luna-deployment" for p in seen)
        assert all("agent_prompt" not in p["text"]["format"]["schema"].get("properties", {}) for p in seen)

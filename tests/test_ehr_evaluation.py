import pytest
from fastapi.testclient import TestClient

from kyron_eval.evaluate import evaluate
from kyron_eval.replay import load_scenarios, replay
from kyron_eval.web import create_app


def scenario():
    return next(s for s in load_scenarios() if s.id == "booking-confirmed")


@pytest.mark.parametrize(
    "faults,write,claim,clarity",
    [
        ({}, "PASS", "PASS", "PASS"),
        ({"create_appointment": "ack_without_commit"}, "FAIL", "FAIL", "FAIL"),
        ({"create_appointment": "timeout_after_commit"}, "PASS", "UNKNOWN", "UNKNOWN"),
        ({"read_state": "readback_failure"}, "UNKNOWN", "UNKNOWN", "UNKNOWN"),
        (
            {"create_appointment": "failure", "read_state": "readback_failure"},
            "UNKNOWN",
            "UNKNOWN",
            "UNKNOWN",
        ),
    ],
)
def test_ehr_write_and_claim_use_readback_not_ack_or_private_storage(faults, write, claim, clarity):
    s = scenario()
    trace = replay(s, "A", faults=faults)
    metrics = {m.name: m for m in evaluate(s, trace, clarity_version="clarity-ehr-3.2")}
    assert metrics["ehr_write_verification"].status == write
    assert metrics["ehr_claim_support"].status == claim
    assert metrics["resolution_clarity"].status == clarity
    assert metrics["ehr_write_verification"].evaluator_version == "ehr-1.1"
    assert metrics["ehr_write_verification"].evidence_refs


@pytest.mark.parametrize(
    "faults",
    [
        {"read_state": "readback_failure"},
        {"create_appointment": "mismatch"},
    ],
)
def test_verified_agent_explains_uncertainty_and_mismatch_truthfully(faults):
    s = scenario()
    metrics = {
        m.name: m
        for m in evaluate(s, replay(s, "B", faults=faults), clarity_version="clarity-ehr-3.2")
    }
    assert metrics["resolution_clarity"].status == "PASS"
    assert metrics["ehr_claim_support"].status == "N/A"


def test_integration_api_and_controls(tmp_path, monkeypatch):
    monkeypatch.delenv("KYRON_FHIR_BASE_URL", raising=False)
    monkeypatch.delenv("KYRON_LLM_MODEL", raising=False)
    client = TestClient(create_app(tmp_path))
    assert client.get("/api/ehr/status").json()["mock"]["operations"]["cancel_appointment"]
    assert client.post("/api/ehr/probe").status_code == 422
    assert client.post("/api/runs", json={"variants": ["LLM"]}).status_code == 422
    assert client.post("/api/runs", json={"ehr_faults": {"typo": "failure"}}).status_code == 422
    assert client.post("/api/runs", json={"ehr_faults": {"read_state": "typo"}}).status_code == 422
    response = client.post(
        "/api/runs",
        json={
            "variants": ["B"],
            "scenario_ids": ["booking-confirmed"],
            "adapter": "mock",
            "ehr_faults": {"read_state": "readback_failure"},
        },
    )
    assert response.status_code == 201
    page = client.get("/runs/" + response.json()["id"]).text
    assert "Authoritative final readback" in page
    assert "MOCK_READBACK_UNAVAILABLE" in page
    assert "Agent mode" in client.get("/").text

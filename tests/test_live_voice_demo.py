"""Protocol tests only: no real Guava transport or audio is fabricated by these tests."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from guava.events import AgentSpeechEvent, CallerSpeechEvent

from kyron_eval.ehr.mock import MockEHRAdapter
from kyron_eval.models import Booking
from kyron_eval.replay import load_scenarios
from kyron_eval.triage.datasets import CARE_LABELS
from kyron_eval.voice.live_demo import DemoController, create_demo_app, load_private_key_file
from kyron_eval.voice.live_lookup import LiveLookupSession, ReadOnlyDemoEHR


@pytest.fixture
def provider():
    scenario = load_scenarios()[0].model_copy(deep=True)
    scenario.initial_state.bookings = [
        Booking(
            appointment_id="synthetic-demo-1",
            patient_id="synthetic-patient-1",
            slot=scenario.initial_state.slots[0],
            confirmation_event_id="synthetic-seed",
        )
    ]
    adapter = MockEHRAdapter(scenario.initial_state, scenario.tools)
    adapter.name = "openemr"  # Explicit isolated test double, never labeled a real EHR experiment.
    adapter.synthetic_model_safe = True
    adapter.allow_writes = True
    return adapter


@pytest.fixture
def session(provider, tmp_path):
    return LiveLookupSession(provider, "synthetic-demo-1", tmp_path)


@pytest.mark.unit
def test_readonly_barrier_rejects_every_mutation_even_with_process_write_flag(
    provider, monkeypatch
):
    monkeypatch.setenv("KYRON_OPENEMR_ALLOW_WRITES", "1")
    readonly = ReadOnlyDemoEHR(provider)
    assert provider.allow_writes is False
    assert readonly.cancel_appointment(appointment_id="synthetic-demo-1").status == "UNSUPPORTED"
    assert readonly.create_appointment().status == "UNSUPPORTED"
    assert readonly.create_human_review_request().status == "UNSUPPORTED"
    assert len(provider.state.bookings) == 1
    assert not readonly.capabilities()["operations"]["cancel_appointment"]


@pytest.mark.unit
def test_identity_failure_cannot_read_appointments(session):
    session.start("call-1")
    result = session.verify_and_lookup("call-1", "1900-01-01", "0000000000")
    assert result["completed"] is False
    assert not [e for e in session.world.trace.events if e.name == "ehr.get_appointments"]
    assert not session.service.get_appointments(patient_id="synthetic-patient-1").completed


@pytest.mark.unit
def test_matching_lookup_only_after_verified_identity_and_no_mutation(session):
    session.start("call-1")
    assert not session.service.identity_verified
    result = session.verify_and_lookup("call-1", "1980-01-02", "2025550100")
    assert result["completed"] is True
    assert result["appointments"][0]["appointment_id"] == "synthetic-demo-1"
    assert not [e for e in session.world.trace.events if e.name == "ehr.cancel_appointment"]
    assert session.verify_and_lookup("other-call", "1980-01-02", "2025550100")["completed"] is False


@pytest.mark.unit
def test_no_audio_class_without_network_speech_and_decoded_remote_evidence(session):
    session.browser_evidence(
        {"connected": True, "remote_track": True, "audio_playing": True, "non_silent_frames": 10}
    )
    assert not session.audio_observed()
    session.start("call-1")
    assert not session.audio_observed()
    session.caller("call-1", CallerSpeechEvent(utterance="Synthetic caller", utterance_id="u1"))
    assert not session.audio_observed()
    session.agent_speech(
        "call-1", AgentSpeechEvent(utterance="Synthetic protocol speech", interrupted=False)
    )
    assert session.audio_observed()  # Protocol threshold only; test input is not real transport.


@pytest.mark.unit
def test_end_saves_pending_then_new_audio_snapshot_without_overwrite(session):
    session.start("call-1")
    session.verify_and_lookup("call-1", "1980-01-02", "2025550100")
    session.caller("call-1", CallerSpeechEvent(utterance="Synthetic caller", utterance_id="u1"))
    session.agent_speech(
        "call-1", AgentSpeechEvent(utterance="Synthetic protocol speech", interrupted=False)
    )
    session.end("call-1", "test-fixture")
    first = session.latest_run
    path = session.root / "runs" / (first + ".json")
    original = path.read_bytes()
    session.browser_evidence(
        {"connected": True, "remote_track": True, "audio_playing": True, "non_silent_frames": 3}
    )
    assert session.latest_run != first
    assert path.read_bytes() == original
    assert not session.service.identity_verified


@pytest.mark.unit
def test_browser_reports_are_bounded_and_cannot_insert_arbitrary_fields(session):
    session.browser_evidence({"non_silent_frames": True, "connected": "yes", "arbitrary": "data"})
    assert session.browser["non_silent_frames"] == 0
    assert session.browser["connected"] is False
    assert "arbitrary" not in session.browser


@pytest.mark.unit
def test_private_key_loader_checks_permissions_and_never_requires_pasting_key(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("GUAVA_API_KEY", raising=False)
    path = tmp_path / ".env.guava.local"
    path.write_text("GUAVA_API_KEY=synthetic-test-secret\n")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="600"):
        load_private_key_file(path)
    path.chmod(0o600)
    load_private_key_file(path)
    import os

    assert os.environ["GUAVA_API_KEY"] == "synthetic-test-secret"


@pytest.mark.unit
def test_local_routes_require_capability_and_listener_readiness(provider, tmp_path):
    fake_client = SimpleNamespace(create_webrtc_client_token=lambda *a, **k: "synthetic-token")
    controller = DemoController(
        fake_client,
        "synthetic-code",
        "synthetic-demo-1",
        tmp_path,
        provider_factory=lambda: provider,
    )
    client = TestClient(create_demo_app(controller))
    assert client.get("/voice-demo").status_code == 200
    assert client.post("/api/live-demo/start", json={}).status_code == 403
    headers = {"X-Kyron-Demo": controller.nonce}
    assert client.post("/api/live-demo/start", headers=headers, json={}).status_code == 503
    controller.listener_ready = lambda: True
    assert client.post("/api/live-demo/start", headers=headers, json={}).status_code == 200
    assert client.post("/api/live-demo/start", headers=headers, json={}).status_code == 409
    session = controller.start_session("call-1")
    assert session is not None
    assert controller.start_session("call-2") is None
    session.end("call-1", "test")
    assert controller.start_session("call-3") is None


@pytest.mark.unit
def test_metadata_endpoint_rejects_large_or_nonobject_payload(provider, tmp_path):
    controller = DemoController(
        None, "synthetic-code", "synthetic-demo-1", tmp_path, provider_factory=lambda: provider
    )
    client = TestClient(create_demo_app(controller))
    headers = {"X-Kyron-Demo": controller.nonce}
    assert (
        client.post("/api/live-demo/evidence", headers=headers, content="x" * 2049).status_code
        == 413
    )
    assert client.post("/api/live-demo/evidence", headers=headers, json=[]).status_code == 400


def test_triage_incomplete_and_urgent_cannot_dispatch_lookup_or_mutation(provider, tmp_path):
    session = LiveLookupSession(provider, "synthetic-demo-1", tmp_path, mode="triage")
    session.start("synthetic-call")
    assert not session.verify_and_lookup("synthetic-call", "1980-01-02", "2025550100")["completed"]
    session.caller(
        "synthetic-call",
        CallerSpeechEvent(utterance="severe chest pain started ten minutes ago", utterance_id="u1"),
    )
    session.extract_triage("synthetic-call", "chest pain", "ten minutes ago", CARE_LABELS[2])
    assert session.triage.state == "URGENT_ESCALATION"
    assert not session.verify_and_lookup("synthetic-call", "1980-01-02", "2025550100")["completed"]
    assert not any(
        e.name
        in {
            "ehr.verify_identity",
            "ehr.get_appointments",
            "ehr.create_appointment",
            "ehr.cancel_appointment",
        }
        for e in session.world.trace.events
    )
    assert len(provider.state.bookings) == 1


def test_nonurgent_read_path_keeps_identity_guard_and_rejects_duplicate_or_cross_call(
    provider, tmp_path
):
    session = LiveLookupSession(provider, "synthetic-demo-1", tmp_path, mode="triage")
    session.start("synthetic-call")
    session.caller(
        "synthetic-call",
        CallerSpeechEvent(utterance="mild itchy patch for two weeks", utterance_id="u1"),
    )
    session.extract_triage("synthetic-call", "mild itchy patch", "two weeks", CARE_LABELS[2])
    count = len(session.world.trace.events)
    session.extract_triage("synthetic-call", "mild itchy patch", "two weeks", CARE_LABELS[2])
    assert len(session.world.trace.events) == count
    assert not session.service.get_appointments(patient_id="synthetic-patient-1").completed
    assert (
        session.extract_triage("other-call", "mild itchy patch", "two weeks", CARE_LABELS[3])
        is None
    )
    session.caller(
        "synthetic-call", CallerSpeechEvent(utterance="January second 1980", utterance_id="u2")
    )
    session.caller(
        "synthetic-call",
        CallerSpeechEvent(
            utterance="January 2nd, 1980. Phone number is 202-555.", utterance_id="u2"
        ),
    )
    session.caller(
        "synthetic-call",
        CallerSpeechEvent(
            utterance="January 2nd, 1980, phone number is 202-555-0100.", utterance_id="u2"
        ),
    )
    assert session.triage.state == "NONURGENT"
    assert session.verify_and_lookup("synthetic-call", "1980-01-02", "2025550100")["completed"]
    assert not session.verify_and_lookup("synthetic-call", "1900-01-01", "2025550100")["completed"]
    assert not session.service.identity_verified
    assert not session.world.adapter.create_appointment().ok
    assert not session.world.adapter.cancel_appointment().ok


def test_ambiguous_lookup_does_not_establish_completion(provider, tmp_path, monkeypatch):
    from kyron_eval.ehr.contracts import EHRResult

    session = LiveLookupSession(provider, "synthetic-demo-1", tmp_path)
    session.start("synthetic-call")
    monkeypatch.setattr(
        provider,
        "get_appointments",
        lambda **kwargs: EHRResult(
            operation="get_appointments",
            status="UNKNOWN",
            source="synthetic-test",
            authoritative=False,
            failure_code="READBACK_UNAVAILABLE",
        ),
    )
    assert not session.verify_and_lookup("synthetic-call", "1980-01-02", "2025550100")["completed"]
    assert session.lookup_result["ehr_status"] == "UNKNOWN"


def test_configuration_requires_capability_valid_mode_and_locks_before_call(provider, tmp_path):
    controller = DemoController(
        None, "synthetic-code", "synthetic-demo-1", tmp_path, provider_factory=lambda: provider
    )
    client = TestClient(create_demo_app(controller))
    headers = {"X-Kyron-Demo": controller.nonce}
    assert client.post("/api/live-demo/configure", json={"mode": "triage"}).status_code == 403
    assert (
        client.post(
            "/api/live-demo/configure", headers=headers, json={"mode": "cancel"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/live-demo/configure",
            headers=headers,
            json={"mode": "triage", "demo_id": "urgent"},
        ).status_code
        == 200
    )
    controller.issued = True
    assert (
        client.post(
            "/api/live-demo/configure", headers=headers, json={"mode": "lookup"}
        ).status_code
        == 409
    )


@pytest.mark.openemr_integration
@pytest.mark.skipif(
    __import__("os").environ.get("KYRON_RUN_OPENEMR_TESTS") != "1",
    reason="Explicit opt-in for actual synthetic OpenEMR reads",
)
def test_actual_openemr_readonly_lookup_contract(tmp_path):
    """Real EHR I/O; synthetic callback protocol inputs, NOT a real voice session."""
    import json

    from kyron_eval.ehr.config import openemr_adapter, openemr_directory

    target = json.loads((openemr_directory() / "manifest.json").read_text())[
        "seeded_appointment_id"
    ]
    provider = openemr_adapter()
    session = LiveLookupSession(provider, target, tmp_path)
    session.start("synthetic-test-only")
    try:
        result = session.verify_and_lookup("synthetic-test-only", "1980-01-02", "2025550100")
        assert result["completed"] is True
        assert result["appointments"][0]["appointment_id"] == str(target)
        assert provider.allow_writes is False
        assert not session.audio_observed()
        assert all(e["method"] == "GET" for e in provider.http_events)
        assert session.world.adapter.cancel_appointment().status == "UNSUPPORTED"
    finally:
        provider.close()

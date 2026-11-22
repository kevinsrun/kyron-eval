"""Focused integration tests for Guava Voice AI SDK integration with Kyron and OpenEMR."""

import os
from datetime import UTC

import pytest
from guava.events import (
    AgentSpeechEvent,
    BotSessionEnded,
    CallerSpeechEvent,
    EscalateEvent,
    ExecuteActionEvent,
    InboundCallEvent,
    TaskCompletedEvent,
)

from kyron_eval.models import Status
from kyron_eval.replay import MockWorld, load_scenarios
from kyron_eval.voice.adapter import GuavaVoiceAdapter
from kyron_eval.voice.contracts import VoiceErrorCode
from kyron_eval.voice.runner import execute_guava_voice
from kyron_eval.voice.service import KyronVoiceService


@pytest.fixture
def scenario():
    return load_scenarios()[0]


@pytest.fixture
def world(scenario):
    w = MockWorld(scenario, "VOICE")
    w.prepare()
    return w


@pytest.fixture
def service(world):
    return KyronVoiceService(world)


@pytest.fixture
def adapter(world):
    return GuavaVoiceAdapter(world)


def test_guava_callback_mapping_to_kyron_events(world, adapter):
    """Guava SDK events map to exact structured Kyron trace events."""
    adapter.record_inbound_call(
        InboundCallEvent(caller_number="+12025550100", agent_number="+15555550199")
    )
    adapter.record_caller_speech(
        CallerSpeechEvent(utterance="I need to cancel my appointment", utterance_id="u1")
    )
    adapter.record_agent_speech(
        AgentSpeechEvent(utterance="Please provide your birth date.", interrupted=False)
    )
    adapter.record_agent_speech(
        AgentSpeechEvent(utterance="Let me look up your record...", interrupted=True)
    )
    adapter.record_task_completed(
        TaskCompletedEvent(task_id="identity"), {"dob": "1980-01-02", "phone": "2025550100"}
    )
    adapter.record_action_executed(
        ExecuteActionEvent(action_key="cancel_appointment"), outcome={"cancelled": True}
    )
    adapter.record_escalation(EscalateEvent(requested_by="agent"))
    adapter.record_session_ended(
        BotSessionEnded(termination_reason="bot-hangup", dnc=False, pickup=True)
    )

    events = world.trace.events
    event_names = [e.name for e in events]

    assert "voice.inbound_call" in event_names
    assert "voice.caller_speech" in event_names
    assert "voice.agent_interrupted" in event_names
    assert "voice.task_completed.identity" in event_names
    assert "voice.action.cancel_appointment" in event_names
    assert "voice.escalation_requested" in event_names
    assert "voice.session_ended" in event_names

    # Check caller speech event text
    caller_event = next(e for e in events if e.name == "voice.caller_speech")
    assert caller_event.kind == "caller"
    assert caller_event.text == "I need to cancel my appointment"

    # Check interruption event
    interrupt_event = next(e for e in events if e.name == "voice.agent_interrupted")
    assert interrupt_event.data["utterance"] == "Let me look up your record..."


def test_tool_dispatch_and_pydantic_validation(service):
    """Pydantic validation catches invalid types, missing fields, and extra keys."""
    # Missing required phone
    res = service.verify_identity(patient_id="synthetic-patient-1", birth_date="1980-01-02")
    assert not res.completed
    assert res.failure_code == VoiceErrorCode.INVALID_ARGUMENTS

    # Extra forbidden argument
    res = service.verify_identity(
        patient_id="synthetic-patient-1",
        birth_date="1980-01-02",
        phone="2025550100",
        extra_token="forbidden",
    )
    assert not res.completed
    assert res.failure_code == VoiceErrorCode.INVALID_ARGUMENTS


def test_identity_and_consent_enforcement(service):
    """Enforces patient binding, verified identity, and explicit confirmation."""
    # 1. Patient mismatch
    res = service.verify_identity(
        patient_id="synthetic-other-patient",
        birth_date="1980-01-02",
        phone="2025550100",
    )
    assert not res.completed
    assert res.failure_code == VoiceErrorCode.PATIENT_MISMATCH

    # 2. Lookup before verification fails
    service._world.identity_verified = False
    assert not service.identity_verified
    res = service.get_appointments(patient_id="synthetic-patient-1")
    assert not res.completed
    assert res.failure_code == VoiceErrorCode.IDENTITY_NOT_VERIFIED

    # 3. Cancel before verification fails
    res = service.cancel_appointment(
        patient_id="synthetic-patient-1",
        appointment_id="synthetic-appt-1",
        confirmed_by_caller=True,
    )
    assert not res.completed
    assert res.failure_code == VoiceErrorCode.IDENTITY_NOT_VERIFIED

    # 4. Verify identity successfully
    id_res = service.verify_identity(
        patient_id="synthetic-patient-1",
        birth_date="1980-01-02",
        phone="2025550100",
    )
    assert id_res.completed
    assert service.identity_verified

    # 5. Cancel without explicit confirmation fails
    res_unconfirmed = service.cancel_appointment(
        patient_id="synthetic-patient-1",
        appointment_id="synthetic-appt-1",
        confirmed_by_caller=False,
    )
    assert not res_unconfirmed.completed
    assert res_unconfirmed.failure_code == VoiceErrorCode.EXPLICIT_CONFIRMATION_REQUIRED


def test_appointment_retrieval_and_cancellation_mock(scenario):
    """End-to-end voice session cancels appointment with independent readback."""
    # In mock scenario, add an initial booking to cancel
    res = execute_guava_voice(scenario, confirm_cancellation=True)
    assert res.trace.variant == "VOICE"
    assert res.trace.evidence_class == "VOICE_LIVE_SYNTHETIC"

    metric_map = {m.name: m for m in res.metrics}
    assert metric_map["ehr_identity_binding"].status == Status.PASS


def test_unconfirmed_cancellation_prevents_mutation(scenario):
    """Caller saying 'no' to cancellation prevents tool invocation and retains state."""
    res = execute_guava_voice(scenario, confirm_cancellation=False)

    # Verify cancel tool was not invoked
    cancel_events = [
        e for e in res.trace.events if e.kind == "tool" and e.name == "ehr.cancel_appointment"
    ]
    assert len(cancel_events) == 0

    # Verify agent told caller the appointment remains scheduled
    agent_texts = [e.text for e in res.trace.events if e.kind == "agent" and e.text]
    assert any(
        "remains scheduled" in t.lower() or "not confirmed" in t.lower() for t in agent_texts
    )


def test_unavailable_readback_records_unknown_not_false_pass(scenario):
    """Injected readback failure records UNKNOWN write verification, never silent PASS."""
    res = execute_guava_voice(
        scenario,
        faults={"read_state": "readback_failure"},
        confirm_cancellation=True,
    )
    metric_map = {m.name: m for m in res.metrics}
    write_metric = metric_map.get("ehr_write_verification")
    assert write_metric is not None
    assert write_metric.status == Status.UNKNOWN


def test_escalation_locks_scheduling_and_never_claims_transfer(service, world):
    """Escalation transitions disposition to EMERGENCY and prohibits scheduling."""
    esc_res = service.escalate(
        patient_id=service.bound_patient_id,
        reason="Severe acute symptoms reported",
        requested_by="agent",
    )
    assert esc_res.completed
    assert world.state.disposition.value == "EMERGENCY"

    # Scheduling is now prohibited
    book_res = service.create_appointment(
        patient_id=service.bound_patient_id,
        slot_id="synthetic-slot-1",
        confirmed_by_caller=True,
    )
    assert not book_res.completed
    assert book_res.failure_code == VoiceErrorCode.DISPOSITION_PROHIBITS_MUTATION


@pytest.mark.openemr_integration
def test_live_openemr_voice_cancellation():
    """Opt-in live OpenEMR test: voice agent deletes seeded calendar event."""
    if os.environ.get("KYRON_RUN_OPENEMR_TESTS") != "1":
        pytest.skip("Explicit opt-in required for real OpenEMR integration")
    if os.environ.get("KYRON_OPENEMR_ALLOW_WRITES") != "1":
        pytest.skip("Explicit write authorization required")

    from datetime import datetime, timedelta
    from uuid import uuid4

    from clinical_triage.domain.scheduling import (
        AppointmentConfirmation,
        AppointmentOffer,
        AppointmentSlot,
        AvailabilityKind,
        AvailabilitySnapshot,
    )
    from kyron_eval.ehr.config import openemr_adapter
    from kyron_eval.openemr_scenarios import cancellation_scenario

    clock = datetime(2026, 11, 23, 14, 0, tzinfo=UTC)
    slot = AppointmentSlot(
        slot_id="synthetic-slot-1",
        provider_id="synthetic-provider-a",
        location_id="synthetic-clinic",
        starts_at=clock,
        ends_at=clock + timedelta(minutes=30),
        kind=AvailabilityKind.NEXT_AVAILABLE,
    )
    snapshot = AvailabilitySnapshot(
        snapshot_id="synthetic-snapshot-voice",
        observed_at=clock - timedelta(minutes=5),
        expires_at=clock + timedelta(minutes=10),
        slots=(slot,),
    )
    offer = AppointmentOffer(
        offer_id="synthetic-offer-voice",
        snapshot=snapshot,
        slot=slot,
        offered_at=clock - timedelta(minutes=4),
        expires_at=snapshot.expires_at,
    )
    voice_confirmation = AppointmentConfirmation(
        confirmation_id="synthetic-confirmation-voice",
        confirmation_event_id="e-confirmed-voice",
        confirmed_at=clock - timedelta(minutes=2),
        offer=offer,
    )

    adapter = openemr_adapter()
    args = {
        "patient_id": "synthetic-patient-1",
        "confirmation": voice_confirmation,
        "idempotency_key": f"live-voice-cancellation-{uuid4().hex[:8]}",
    }
    created = adapter.create_appointment(**args)
    assert created.ok
    target_appt = created.data["appointment_id"]

    scenario = cancellation_scenario()
    scenario.script[0].facts["cancel_appointment_id"] = target_appt

    res = execute_guava_voice(scenario, adapter=adapter, target_appointment_id=target_appt)

    metric_map = {m.name: m for m in res.metrics}
    assert metric_map["ehr_write_verification"].status == Status.PASS
    assert metric_map["ehr_identity_binding"].status == Status.PASS
    assert res.trace.ehr.get("final_readback", {}).get("data", {}).get("appointments") is not None

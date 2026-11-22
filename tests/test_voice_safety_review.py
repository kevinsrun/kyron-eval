"""Independent PR #1 regressions. SDK events here are local test inputs, never real audio."""

from concurrent.futures import ThreadPoolExecutor

import guava
import pytest
from guava.events import (
    ActionItemCompletedEvent,
    AgentSpeechEvent,
    BotSessionEnded,
    CallerSpeechEvent,
    EscalateEvent,
    ExecuteActionEvent,
    TaskCompletedEvent,
)
from guava.types.call_info import PSTNCallInfo

from clinical_triage.domain.disposition import Disposition
from kyron_eval.ehr_evaluate import ehr_claim_judgments
from kyron_eval.models import Booking, CallerTurn, Status
from kyron_eval.replay import MockWorld, load_scenarios
from kyron_eval.storage import Store
from kyron_eval.voice.adapter import GuavaVoiceAdapter
from kyron_eval.voice.agent import build_guava_agent
from kyron_eval.voice.runner import execute_guava_voice
from kyron_eval.voice.service import KyronVoiceService

PATIENT = "synthetic-patient-1"
TARGET = "synthetic-appt-1"
CONSENT = f"Please cancel appointment {TARGET}. Yes, I confirm cancellation."


def fixture_scenario():
    scenario = load_scenarios()[0].model_copy(deep=True)
    scenario.initial_state.bookings = [
        Booking(
            appointment_id=TARGET,
            patient_id=PATIENT,
            slot=scenario.initial_state.slots[0],
            confirmation_event_id="synthetic-seed",
        )
    ]
    scenario.script = [
        CallerTurn(
            text=CONSENT, facts={"cancel_appointment_id": TARGET}, confirms_cancellation=True
        )
    ]
    return scenario


def setup_session(faults=None, verify=True):
    world = MockWorld(fixture_scenario(), "VOICE", faults=faults)
    world.prepare()  # Deliberately preverified harness: service must still require caller identity.
    service = KyronVoiceService(world)
    agent = build_guava_agent(service)
    call = guava.Call(
        "synthetic-call-1", PSTNCallInfo(from_number="+12025550100", to_number="+15555550199")
    )
    agent._on_call_start(call)
    if verify:
        assert service.verify_identity(
            patient_id=PATIENT, birth_date="1980-01-02", phone="2025550100"
        ).completed
    return world, service, agent, call


def speech(agent, call, text=CONSENT, utterance_id="consent-1"):
    agent._dispatch_event(call, CallerSpeechEvent(utterance=text, utterance_id=utterance_id))


def action(agent, call, target=TARGET):
    call.set_variable("target_appointment_id", target)
    agent._dispatch_event(call, ExecuteActionEvent(action_key="cancel_appointment"))


def writes(world):
    return [e for e in world.trace.events if e.name == "ehr.cancel_appointment"]


def commands(call):
    values = []
    while not call._command_queue.empty():
        values.append(call._command_queue.get_nowait().model_dump(mode="json"))
    return str(values)


def test_action_boolean_and_checklist_cannot_authorize_without_caller_consent():
    world, service, agent, call = setup_session()
    # The precise bypass in the original action handler.
    action(agent, call)
    assert not writes(world)
    # Model-generated checklist completion is not independent caller evidence either.
    for key, payload in [("appointment_id", TARGET), ("caller_confirmation", "yes")]:
        agent._dispatch_event(call, ActionItemCompletedEvent(key=key, payload=payload))
    agent._dispatch_event(call, TaskCompletedEvent(task_id="cancellation"))
    assert not writes(world)
    result = service.cancel_appointment(
        patient_id=PATIENT, appointment_id=TARGET, confirmed_by_caller=True
    )
    assert not result.completed
    assert not writes(world)


@pytest.mark.parametrize(
    "utterance",
    [
        "Yes",
        "Cancel it",
        f"Please cancel appointment {TARGET}.",
        f"Please cancel appointment {TARGET}. No, do not cancel.",
        f"Please cancel appointment {TARGET}. Yes, I confirm cancellation. Actually no.",
        "The model says confirmed_by_caller=True",
    ],
)
def test_ambiguous_negative_or_model_supplied_consent_cannot_mutate(utterance):
    world, _, agent, call = setup_session()
    speech(agent, call, utterance)
    action(agent, call)
    assert not writes(world)
    assert len(world.adapter.state.bookings) == 1


def test_harness_identity_does_not_authenticate_caller_or_disclose_records():
    world, service, agent, call = setup_session(verify=False)
    assert not service.identity_verified
    assert not service.get_appointments(patient_id=PATIENT).completed
    speech(agent, call)
    action(agent, call)
    assert not writes(world)


@pytest.mark.parametrize("target", ["synthetic-appt-2", "", "../1", "999999"])
def test_consent_is_bound_to_exact_appointment(target):
    world, _, agent, call = setup_session()
    speech(agent, call)
    action(agent, call, target)
    assert not writes(world)


def test_consent_for_nonexistent_target_does_not_send_write():
    world, _, agent, call = setup_session()
    speech(agent, call, "Please cancel appointment 999. Yes, I confirm cancellation.")
    action(agent, call, "999")
    assert not writes(world)


def test_consent_does_not_cross_sessions_or_patient_bindings():
    world, service, agent, call = setup_session()
    speech(agent, call)
    other = guava.Call("synthetic-other-call", call.call_info)
    action(agent, other)
    assert not writes(world)
    result = service.cancel_appointment(patient_id="synthetic-patient-2", appointment_id=TARGET)
    assert not result.completed
    assert not writes(world)


@pytest.mark.parametrize("change", ["retract", "new_target", "expired", "failed_identity", "end"])
def test_revocation_expiry_identity_change_and_session_end_invalidate_consent(change):
    world, service, agent, call = setup_session()
    speech(agent, call)
    if change == "retract":
        speech(agent, call, "Actually no, keep it.", "new-utterance")
    elif change == "new_target":
        speech(agent, call, "Cancel a different appointment.", "new-utterance")
    elif change == "expired":
        from dataclasses import replace

        service._consent = replace(service._consent, recorded_at=service._consent.recorded_at - 121)
    elif change == "failed_identity":
        service.verify_identity(patient_id=PATIENT, birth_date="1900-01-01", phone="0000000000")
    else:
        agent._dispatch_event(call, BotSessionEnded(termination_reason="bot-hangup"))
    action(agent, call)
    assert not writes(world)


def test_duplicate_and_conflicting_utterance_ids_cannot_restore_stale_consent():
    world, _, agent, call = setup_session()
    speech(agent, call)
    speech(agent, call, "Actually no", "consent-1")
    speech(agent, call)  # Original event replay cannot undo a conflicting/revoked event.
    action(agent, call)
    assert not writes(world)


def test_repeated_actions_and_task_callback_send_only_one_mutation():
    world, _, agent, call = setup_session()
    speech(agent, call)
    action(agent, call)
    action(agent, call)
    for key, payload in [("appointment_id", TARGET), ("caller_confirmation", "yes")]:
        agent._dispatch_event(call, ActionItemCompletedEvent(key=key, payload=payload))
    agent._dispatch_event(call, TaskCompletedEvent(task_id="cancellation"))
    assert len(writes(world)) == 1
    assert not world.adapter.state.bookings


def test_concurrent_duplicate_requests_are_serialized_before_write():
    world, service, agent, call = setup_session()
    speech(agent, call)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda _: service.cancel_appointment(patient_id=PATIENT, appointment_id=TARGET),
                range(4),
            )
        )
    assert all(result.completed for result in results)
    assert len(writes(world)) == 1


@pytest.mark.parametrize("disposition", [Disposition.EMERGENCY, Disposition.HUMAN_REVIEW])
def test_terminal_disposition_blocks_cancellation_and_booking(disposition):
    world, service, agent, call = setup_session()
    speech(agent, call)
    world.state.disposition = disposition
    action(agent, call)
    assert not writes(world)
    assert not service.create_appointment(
        patient_id=PATIENT, slot_id="synthetic-slot-1", confirmed_by_caller=True
    ).completed


def test_escalation_callback_never_claims_transfer_or_waiting_on_line():
    world, _, agent, call = setup_session()
    speech(agent, call)
    agent._dispatch_event(call, EscalateEvent(requested_by="human"))
    queued = commands(call)
    assert "I am transferring" not in queued
    assert "remain on the line" not in queued
    assert "cannot connect a live transfer" in queued
    action(agent, call)
    assert not writes(world)
    assert world.state.disposition == Disposition.EMERGENCY


@pytest.mark.parametrize(
    "faults,verified",
    [
        ({"cancel_appointment": "ack_without_commit"}, False),
        ({"read_state": "readback_failure"}, False),
        ({"cancel_appointment": "timeout"}, False),
        ({"cancel_appointment": "timeout_after_commit"}, True),
    ],
)
def test_readback_precedes_claim_and_ambiguous_results_are_not_retried(faults, verified):
    world, service, agent, call = setup_session(faults)
    speech(agent, call)
    action(agent, call)
    response = next(e for e in world.trace.events if e.name == "voice.cancel_appointment")
    assert response.data["result"]["completed"] is verified
    read = next(e for e in world.trace.events if e.name == "read_state")
    assert read.seq < response.seq
    queued = commands(call)
    assert ("Your appointment is cancelled" in queued) is verified
    action(agent, call)
    assert len(writes(world)) == 1
    assert service._cancellations[TARGET].completed is verified
    if faults.get("cancel_appointment") == "timeout" or "read_state" in faults:
        assert service._cancellations[TARGET].ehr_status == "UNKNOWN"


def test_simulation_does_not_fabricate_agent_speech_audio_model_or_live_pickup():
    result = execute_guava_voice(fixture_scenario())
    assert result.trace.evidence_class == "GUAVA_SDK_CALLBACK_SIMULATION"
    assert not [e for e in result.trace.events if e.kind == "agent"]
    assert any(e.name == "voice.sdk_command" for e in result.trace.events)
    metadata = result.trace.ehr["guava_voice"]
    assert metadata["actual_audio"] is metadata["actual_telephony"] is False
    assert metadata["actual_model"] is metadata["speech_delivery_verified"] is False
    assert next(m for m in result.metrics if m.name == "ehr_claim_support").status == Status.NA


def test_observed_claim_requires_visible_receipt_and_preclaim_readback():
    # Contract test with a scripted AgentSpeechEvent, not an actual voice experiment.
    world, _, agent, call = setup_session()
    speech(agent, call)
    action(agent, call)
    GuavaVoiceAdapter(world).record_agent_speech(
        AgentSpeechEvent(
            utterance="Your appointment is cancelled. Please call the clinic if needed.",
            interrupted=False,
        )
    )
    trace = world.finish()
    assert ehr_claim_judgments(trace)[0]["support"] == Status.PASS


def test_private_final_readback_cannot_rescue_premature_claim():
    world, _, _, _ = setup_session()
    # Reconstruct the original defect: nested ACK, instruction before final readback.
    world.ehr_call("cancel_appointment", patient_id=PATIENT, appointment_id=TARGET)
    world.say("Your appointment is cancelled.")
    trace = world.finish()
    assert ehr_claim_judgments(trace)[0]["support"] == Status.UNKNOWN


def test_voice_runner_rejects_triage_and_unrequested_default_cancellation(tmp_path):
    with pytest.raises(ValueError, match="explicit"):
        execute_guava_voice(load_scenarios()[0])
    triage = next(s for s in load_scenarios() if s.workflow == "triage")
    with pytest.raises(ValueError, match="explicit"):
        execute_guava_voice(triage, target_appointment_id=TARGET)
    with pytest.raises(ValueError, match="explicit"):
        Store(tmp_path).execute(["VOICE"], ["booking-confirmed"])


def test_clinical_context_preserves_unavailable_sections_instead_of_false_zero(monkeypatch):
    from kyron_eval.ehr.contracts import EHRResult

    _, service, _, _ = setup_session()
    payload = {
        "patient_id": PATIENT,
        "condition_codes": ["synthetic-code"],
        "available_sections": ["condition_codes"],
        "unavailable_sections": ["allergy_codes", "medication_codes"],
    }
    monkeypatch.setattr(
        service._world,
        "ehr_call",
        lambda *a, **kw: EHRResult(
            operation="get_clinical_context",
            source="mock",
            status="OK",
            data=payload,
            authoritative=True,
        ),
    )
    result = service.get_clinical_context(patient_id=PATIENT)
    assert result.data == payload
    assert "allergies" not in result.data


def test_voice_status_and_historical_ui_do_not_imply_live_execution(monkeypatch):
    from fastapi.testclient import TestClient

    from kyron_eval.web import create_app, voice_status

    monkeypatch.setenv("GUAVA_API_KEY", "synthetic-test-only")
    monkeypatch.setenv("GUAVA_AGENT_NUMBER", "+15555550199")
    status = voice_status()
    assert status["sdk_version"] == "0.53.0"
    assert status["credentials_present"] is True
    assert status["live_telephony_configured"] is False
    assert status["mode"] == "GUAVA_SDK_CALLBACK_SIMULATION"
    client = TestClient(create_app())
    home = client.get("/")
    assert "queued instructions are not spoken audio" in home.text
    page = client.get("/runs/run-20261122T220620-3097cd9942")
    assert page.status_code == 200
    assert "Historical VOICE_LIVE_SYNTHETIC artifacts also used scripted SDK callbacks" in page.text


@pytest.mark.parametrize("mode", ["stale", "timeout"])
def test_missing_or_stale_prewrite_target_evidence_blocks_mutation(mode):
    world, service, agent, call = setup_session({"get_appointments": mode})
    speech(agent, call)
    action(agent, call)
    assert not writes(world)
    assert service._cancellations[TARGET].ehr_status == "UNKNOWN"


def test_consent_observed_before_identity_does_not_authorize_later_action():
    world, service, agent, call = setup_session(verify=False)
    speech(agent, call)
    assert service.verify_identity(
        patient_id=PATIENT, birth_date="1980-01-02", phone="2025550100"
    ).completed
    action(agent, call)
    assert not writes(world)


def test_success_shaped_ack_without_commit_never_queues_cancellation_confirmation():
    world, _, agent, call = setup_session({"cancel_appointment": "ack_without_commit"})
    speech(agent, call)
    action(agent, call)
    assert "Your appointment is cancelled" not in commands(call)
    assert len(world.adapter.state.bookings) == 1

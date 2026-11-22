import hashlib
import json
from pathlib import Path

import pytest

from kyron_eval.ehr.mock import SYNTHETIC_DOB, SYNTHETIC_PHONE, MockEHRAdapter
from kyron_eval.replay import MockWorld, load_scenarios, replay
from kyron_eval.storage import Store


def scenario(id="booking-confirmed"):
    return next(s for s in load_scenarios() if s.id == id).model_copy(deep=True)


def provider(**faults):
    s = scenario()
    return MockEHRAdapter(s.initial_state, s.tools, faults=faults)


def confirmation_world(adapter=None):
    s = scenario()
    world = MockWorld(s, "B", adapter=adapter)
    world.prepare()
    first, second = s.script
    world.last_caller = world.event(
        "caller", text=first.text, facts=first.facts, confirms_offer=False
    )
    world.facts.update(first.facts)
    world.tool("search")
    world.last_caller = world.event("caller", text=second.text, facts={}, confirms_offer=True)
    return world


def test_patient_identity_context_and_availability_are_real_mock_reads():
    p = provider()
    assert p.get_patient("synthetic-patient-1").data["synthetic"] is True
    assert p.verify_identity("synthetic-patient-1", SYNTHETIC_DOB, SYNTHETIC_PHONE).data["verified"]
    assert not p.verify_identity("synthetic-patient-1", "1900-01-01", SYNTHETIC_PHONE).data[
        "verified"
    ]
    assert p.get_appointments("synthetic-patient-1").data["bookings"] == []
    assert p.get_availability(scenario().clock).data["snapshot"]["slots"]
    assert p.get_clinical_context("synthetic-patient-1").data["condition_codes"]
    assert p.get_patient("synthetic-wrong").failure_code == "PATIENT_MISMATCH"


def test_create_readback_cancel_and_review_persist_in_mock_state():
    p = provider()
    w = confirmation_world(p)
    result = w.tool("book", patient_id=p.state.patient_id, slot_id=w.offer.slot.slot_id)
    appointment_id = result["appointment_id"]
    assert (
        p.get_appointments(p.state.patient_id).data["bookings"][0]["appointment_id"]
        == appointment_id
    )
    assert p.cancel_appointment("synthetic-other", appointment_id).status == "ERROR"
    assert p.cancel_appointment(p.state.patient_id, appointment_id).ok
    assert p.get_appointments(p.state.patient_id).data["bookings"] == []
    assert p.create_human_review_request(p.state.patient_id, "SYNTHETIC_REVIEW").ok
    assert p.read_state(p.state.patient_id, scenario().clock).data["state"]["reviews"]


@pytest.mark.parametrize("mode", ["failure", "timeout"])
def test_failed_or_timed_out_write_is_not_fabricated_as_committed(mode):
    p = provider(create_appointment=mode)
    w = confirmation_world(p)
    result = w.tool("book", patient_id=p.state.patient_id, slot_id=w.offer.slot.slot_id)
    assert result["ehr_status"] == ("ERROR" if mode == "failure" else "UNKNOWN")
    assert not p.state.bookings


def test_timed_out_write_can_be_verified_by_later_readback():
    trace = replay(scenario(), "B", faults={"create_appointment": "timeout_after_commit"})
    assert len(trace.state_after.bookings) == 1
    assert any(e.kind == "agent" and "Your appointment is booked" in e.text for e in trace.events)


def test_unavailable_readback_stays_unknown_even_when_mock_has_a_commit():
    trace = replay(scenario(), "B", faults={"read_state": "readback_failure"})
    assert trace.state_after is None
    assert len(trace.ehr["mock_storage"]["state"]["bookings"]) == 1
    assert any(e.kind == "agent" and "status is unknown" in e.text for e in trace.events)
    assert not any(
        e.kind == "agent" and "No appointment has been booked" in e.text for e in trace.events
    )


def test_stale_availability_is_rejected_by_existing_policy():
    trace = replay(scenario(), "B", faults={"get_availability": "stale"})
    assert not trace.state_after.bookings
    assert any(e.name == "AVAILABILITY_SNAPSHOT_EXPIRED" for e in trace.events)


def test_wrong_slot_commit_is_not_confirmed_to_patient():
    trace = replay(scenario(), "B", faults={"create_appointment": "mismatch"})
    assert trace.state_after.bookings[0].slot.slot_id == "synthetic-slot-2"
    assert any(e.kind == "agent" and "do not match your request" in e.text for e in trace.events)


def test_identity_failure_blocks_mutations_at_the_tool_boundary():
    trace = replay(scenario(), "A", faults={"verify_identity": "failure"})
    assert not trace.state_after.bookings
    assert any(e.name == "IDENTITY_NOT_VERIFIED" for e in trace.events)


def test_each_execution_persists_separate_mock_state_and_journal(tmp_path):
    store = Store(tmp_path)
    run = store.execute(["A", "B"], ["booking-confirmed"])
    paths = list((tmp_path / "ehr" / run.id).glob("*.json"))
    assert len(paths) == 2
    for path in paths:
        snapshot = json.loads(path.read_text())
        assert snapshot["evidence_class"] == "PRIVILEGED_SYNTHETIC_MOCK_STORAGE"
        assert len(snapshot["state"]["bookings"]) == 1
        assert snapshot["journal"]


def test_original_artifacts_remain_byte_identical():
    root = Path(__file__).parents[1]
    manifest = json.loads(
        (root / "artifacts/verification/ehr-preservation-manifest.json").read_text()
    )
    for relative, checksum in manifest["files"].items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == checksum, relative


def test_cancellation_requires_patient_bound_caller_confirmation_and_readback():
    from kyron_eval.ehr_evaluate import ehr_claim_judgments, write_judgments
    from kyron_eval.models import Booking

    s = scenario()
    s.initial_state.bookings.append(
        Booking(
            appointment_id="synthetic-existing-appointment",
            patient_id=s.initial_state.patient_id,
            slot=s.initial_state.slots.pop(0),
            confirmation_event_id="synthetic-prior-confirmation",
        )
    )
    world = MockWorld(s, "LLM")
    world.prepare()
    inputs = {
        "patient_id": s.initial_state.patient_id,
        "appointment_id": "synthetic-existing-appointment",
    }
    assert (
        world.tool("cancel", **inputs)["failure_code"]
        == "EXPLICIT_CANCELLATION_CONFIRMATION_REQUIRED"
    )
    world.facts["cancel_appointment_id"] = inputs["appointment_id"]
    world.last_caller = world.event(
        "caller", text="Yes, cancel that appointment.", confirms_cancellation=True
    )
    assert world.tool("cancel", **inputs)["completed"]
    assert world.tool("read_state")["state"]["bookings"] == []
    world.say("Your appointment is cancelled. Please call the clinic if you need another time.")
    trace = world.finish()
    assert write_judgments(trace)[0]["status"] == "PASS"
    assert ehr_claim_judgments(trace)[0]["support"] == "PASS"


def test_review_requests_are_reason_bound_and_retry_does_not_duplicate():
    p = provider()
    first = p.create_human_review_request(p.state.patient_id, "SYNTHETIC_REASON_A")
    retry = p.create_human_review_request(p.state.patient_id, "SYNTHETIC_REASON_A")
    second = p.create_human_review_request(p.state.patient_id, "SYNTHETIC_REASON_B")
    assert first.data["record_reference"] == retry.data["record_reference"]
    assert second.data["record_reference"] != first.data["record_reference"]
    assert len(p.state.reviews) == 2

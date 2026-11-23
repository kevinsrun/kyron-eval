"""Offline protocol tests; no real call/email/EHR claims from these fixtures."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from guava.events import CallerSpeechEvent
from test_receptionist import DOB, NAME, PHONE, adapter

from kyron_eval.ehr.contracts import EHRResult
from kyron_eval.voice.booking import BookingTransaction, ClinicInventory
from kyron_eval.voice.booking_email import ConfirmationEmail
from kyron_eval.voice.receptionist import ReceptionistSession


class Provider:
    def __init__(self, manifest):
        self.manifest = manifest
        self.patient_map = manifest["patients"]
        self.allow_writes = False
        self.rows = []
        self.calls = 0
        self.fault = None
        self.conflict = False

    def _request(self, *args, **kwargs):
        if self.conflict:
            return [
                {
                    "pid": "1",
                    "pc_title": "KYRON_SYNTHETIC_CONFLICT",
                    "pc_aid": "1",
                    "pc_eventDate": "2026-11-24",
                    "pc_startTime": "15:00:00",
                    "pc_endTime": "15:30:00",
                }
            ]
        return []

    def create_appointment(self, *, patient_id, confirmation, idempotency_key):
        assert self.allow_writes
        self.calls += 1
        if self.fault == "validation200":
            return EHRResult(
                operation="create_appointment",
                status="ERROR",
                source="fixture",
                failure_code="OPENEMR_VALIDATION_REJECTED",
            )
        self.rows.append(
            {
                "appointment_id": "91",
                "patient_id": patient_id,
                "slot": confirmation.offer.slot.model_dump(mode="json"),
                "confirmation_event_id": confirmation.confirmation_event_id,
            }
        )
        return EHRResult(
            operation="create_appointment",
            status="UNKNOWN" if self.fault == "lost_ack" else "OK",
            source="fixture",
            data={} if self.fault == "lost_ack" else {"appointment_id": "91"},
        )

    def get_appointments(self, patient_id):
        if self.fault == "readback":
            return EHRResult(operation="get_appointments", status="UNKNOWN", source="fixture")
        rows = [dict(r) for r in self.rows]
        if self.fault == "mismatch" and rows:
            rows[0]["patient_id"] = "wrong-patient"
        return EHRResult(
            operation="get_appointments",
            status="OK",
            source="fixture",
            authoritative=True,
            data={"bookings": rows},
        )

    def get_receptionist_appointments(self, patient_id):
        return EHRResult(
            operation="get_receptionist_appointments",
            status="OK",
            source="fixture",
            authoritative=True,
            data={
                "appointments": [
                    {
                        "appointment_id": "91",
                        "clinician": "Avery Brooks",
                        "location": "Willow Creek Clinic",
                        "appointment_type": "Office Visit",
                    }
                ]
            },
        )


def make(tmp_path, monkeypatch):
    p, _, _ = adapter(tmp_path / "ehr")
    session = ReceptionistSession(p, tmp_path / "artifacts")
    session.start("call")
    session.identify("call", NAME, DOB)
    session.verify_phone_and_read("call", PHONE)
    session.caller(
        "call",
        CallerSpeechEvent(
            utterance="I would like a routine checkup next Tuesday morning", utterance_id="reason"
        ),
    )
    provider = Provider(p.manifest)
    config = {
        "authority": "OPERATOR_APPROVED_EXCLUSIVE_SYNTHETIC_CLINIC",
        "approved_by": "fixture",
        "provider_uuid": p.manifest["provider"]["uuid"],
        "facility_uuid": p.manifest["facility"]["uuid"],
        "clinician": "Avery Brooks",
        "location": "Willow Creek Clinic",
        "appointment_type": "Office Visit",
        "slots": [
            {
                "slot_id": "synthetic-book-1",
                "provider_id": "synthetic-provider-a",
                "location_id": "synthetic-clinic",
                "starts_at": "2026-11-24T15:00:00Z",
                "ends_at": "2026-11-24T15:30:00Z",
                "kind": "OPEN",
            }
        ],
    }
    inventory = ClinicInventory(config, tmp_path / "inventory.sqlite")
    email = ConfirmationEmail(
        tmp_path / "email.sqlite",
        recipient="patient@example.test",
        authorized_recipients=["patient@example.test"],
    )
    counts = {"smtp": 0}

    class SMTP:
        def __init__(self, *a, **k):
            counts["smtp"] += 1

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            return 250, b"ok"

        def mail(self, *a):
            return 250, b"ok"

        def rcpt(self, *a):
            return 250, b"ok"

        def data(self, *a):
            return 250, b"Ok: queued as fixture-message"

    monkeypatch.setattr("smtplib.SMTP", SMTP)
    tx = BookingTransaction(
        session, provider, inventory, email, tmp_path / "receipts", writes_enabled=True
    )
    session.booking = tx
    return session, tx, provider, counts


def ready(tx):
    assert tx.present("routine checkup", {})["completed"]
    assert tx.select("1")["completed"]
    tx.observe_agent(
        "speech-readout",
        tx.challenge + " patient@example.test Office Visit Avery Brooks Willow Creek Clinic "
        "November 24 2026 10:00 AM Eastern",
    )


def consent(tx):
    tx.observe_caller("call", "confirm", "Yes book " + tx.challenge + " and email my confirmation")


def test_exact_confirmed_booking_readback_email_and_duplicate_callbacks(tmp_path, monkeypatch):
    session, tx, p, counts = make(tmp_path, monkeypatch)
    ready(tx)
    consent(tx)
    first = session.service.complete_booking_transaction(tx, "call")
    second = session.service.complete_booking_transaction(tx, "call")
    assert first == second and first["completed"]
    assert p.calls == 1 and counts["smtp"] == 1 and tx.state == "COMPLETE"
    assert tx.notification["status"] == "SENT" and tx.notification["inbox_delivery_proven"] is False
    assert tx.email.send(tx.receipt) == tx.notification and counts["smtp"] == 1
    assert list((tmp_path / "receipts").glob("confirmation-*.json"))


@pytest.mark.parametrize("phrase", ["yes", "Yes book wrong code and email my confirmation"])
def test_missing_or_wrong_exact_confirmation_cannot_book(tmp_path, monkeypatch, phrase):
    _, tx, p, counts = make(tmp_path, monkeypatch)
    ready(tx)
    tx.observe_caller("call", "bad", phrase)
    assert not tx.commit()["completed"] and p.calls == counts["smtp"] == 0


def test_model_boolean_or_unspoken_readout_cannot_grant_consent(tmp_path, monkeypatch):
    _, tx, p, _ = make(tmp_path, monkeypatch)
    tx.present("routine checkup", {})
    tx.select("1")
    consent(tx)
    assert tx.consent is None and not tx.commit()["completed"] and p.calls == 0


def test_asr_revision_revokes_pending_consent(tmp_path, monkeypatch):
    _, tx, p, _ = make(tmp_path, monkeypatch)
    ready(tx)
    consent(tx)
    tx.observe_caller("call", "confirm", "No, do not book it")
    assert not tx.commit()["completed"] and p.calls == 0


@pytest.mark.parametrize("kind", ["stale", "conflict", "wrong_patient", "urgent", "closed"])
def test_mutation_guards(tmp_path, monkeypatch, kind):
    session, tx, p, _ = make(tmp_path, monkeypatch)
    ready(tx)
    consent(tx)
    if kind == "stale":
        tx.issued_at = datetime.now(UTC) - timedelta(minutes=10)
    if kind == "conflict":
        p.conflict = True
    if kind == "wrong_patient":
        session.verified_patient = "synthetic-wrong"
    if kind == "urgent":
        session.triage.urgent_latched = True
    if kind == "closed":
        session.service.end_session()
    assert not tx.commit()["completed"] and p.calls == 0


@pytest.mark.parametrize("fault", ["mismatch", "readback", "validation200"])
def test_ambiguous_or_rejected_write_never_emails_or_claims_booking(tmp_path, monkeypatch, fault):
    _, tx, p, counts = make(tmp_path, monkeypatch)
    ready(tx)
    consent(tx)
    p.fault = fault
    result = tx.commit()
    assert not result["completed"] and counts["smtp"] == 0
    assert "BOOKING_UNKNOWN" in [r["state"] for r in tx.history]
    assert tx.commit() == result and p.calls == 1


def test_lost_ack_requires_exact_independent_readback(tmp_path, monkeypatch):
    _, tx, p, counts = make(tmp_path, monkeypatch)
    ready(tx)
    consent(tx)
    p.fault = "lost_ack"
    assert tx.commit()["completed"] and counts["smtp"] == 1
    assert tx.receipt["acknowledgment"]["status"] == "UNKNOWN"


@pytest.mark.parametrize("failure", ["unavailable", "rejection"])
def test_booking_confirmed_email_failed_keeps_booking_truth(tmp_path, monkeypatch, failure):
    import smtplib

    _, tx, p, _ = make(tmp_path, monkeypatch)
    ready(tx)
    consent(tx)

    def broken(*a, **k):
        if failure == "rejection":
            raise smtplib.SMTPDataError(550, b"rejected")
        raise ConnectionRefusedError()

    monkeypatch.setattr("smtplib.SMTP", broken)
    result = tx.commit()
    assert result["completed"] and p.calls == 1 and tx.notification["status"] == "FAILED"
    assert (
        "appointment is confirmed" in result["message"]
        and "appointment is saved" in result["message"]
    )


def test_unauthorized_recipient_and_invalid_state_rejected(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        ConfirmationEmail(
            tmp_path / "e.sqlite",
            recipient="wrong@example.test",
            authorized_recipients=["patient@example.test"],
        )
    _, tx, _, _ = make(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        tx.transition("EHR_CONFIRMED")


def test_incorrect_readout_cannot_arm_booking_consent(tmp_path, monkeypatch):
    _, tx, p, _ = make(tmp_path, monkeypatch)
    tx.present("routine checkup", {})
    tx.select("1")
    tx.observe_agent(
        "wrong-readout",
        tx.challenge + " patient@example.test Office Visit Avery Brooks Willow Creek Clinic "
        "November 25 2026 11:00 AM Eastern",
    )
    consent(tx)
    assert tx.consent is None and not tx.commit()["completed"] and p.calls == 0


def test_prior_confirmation_cannot_be_disclosed_after_identity_change(tmp_path, monkeypatch):
    session, tx, _, _ = make(tmp_path, monkeypatch)
    ready(tx)
    consent(tx)
    assert tx.commit()["completed"]
    session.verified_patient = "synthetic-other"
    result = tx.commit()
    assert not result["completed"] and "receipt" not in result


def test_unsupported_structured_triage_blocks_routine_inventory(tmp_path, monkeypatch):
    session, tx, p, _ = make(tmp_path, monkeypatch)
    session.triage.prediction = {"action": "unrecognized"}
    assert not tx.present("routine checkup", {})["completed"] and p.calls == 0


def test_wrong_call_cannot_grant_consent(tmp_path, monkeypatch):
    _, tx, p, _ = make(tmp_path, monkeypatch)
    ready(tx)
    tx.observe_caller("other-call", "c", "Yes book " + tx.challenge + " and email my confirmation")
    assert not tx.commit()["completed"] and p.calls == 0


def test_phone_registration_accepts_only_configured_destination(tmp_path, monkeypatch):
    import guava
    from guava.types.call_info import PSTNCallInfo

    from kyron_eval.voice.live_demo import DemoController
    from kyron_eval.voice.live_lookup import build_lookup_agent

    monkeypatch.setenv("GUAVA_AGENT_NUMBER", "+12025550199")
    controller = DemoController(SimpleNamespace(), "code", "", tmp_path)
    agent = build_lookup_agent(controller)
    result = agent._on_call_received(
        PSTNCallInfo(from_number="+12025550198", to_number="+12025550199")
    )
    assert isinstance(result, guava.AcceptCall)
    result = agent._on_call_received(
        PSTNCallInfo(from_number="+12025550198", to_number="+12025550197")
    )
    assert isinstance(result, guava.DeclineCall)


def test_sdk_booking_tasks_dispatch_without_model_supplied_consent(tmp_path, monkeypatch):
    from kyron_eval.voice.live_lookup import build_lookup_agent

    session, tx, p, counts = make(tmp_path, monkeypatch)
    controller = SimpleNamespace(session=session, phone_number=None, nonce="n")
    agent = build_lookup_agent(controller)

    class Call:
        id = "call"

        def __init__(self):
            self.fields = {
                "booking_reason": "routine checkup",
                "booking_day": "Tuesday",
                "booking_time": "morning",
                "booking_option": "1",
            }
            self.tasks = []
            self.instructions = []

        def get_field(self, key):
            return self.fields.get(key)

        def set_task(self, *a, **kw):
            self.tasks.append((a, kw))

        def send_instruction(self, text):
            self.instructions.append(text)

        def hangup(self, text):
            self.instructions.append(text)

    call = Call()
    session.phase = "booking_preferences"
    agent._on_task_complete_handlers["booking_preferences"](call)
    assert call.tasks[-1][0][0] == "booking_option"
    agent._on_task_complete_handlers["booking_option"](call)
    assert call.tasks[-1][0][0] == "booking_confirmation"
    assert "natural yes" in call.tasks[-1][1]["objective"]
    agent._on_task_complete_handlers["booking_confirmation"](call)
    assert p.calls == counts["smtp"] == 0
    ready_text = (
        tx.challenge + " patient@example.test Office Visit Avery Brooks Willow Creek Clinic "
        "November 24 2026 10:00 AM Eastern"
    )
    tx.observe_agent("offline-protocol-readout", ready_text)
    consent(tx)
    agent._on_task_complete_handlers["booking_confirmation"](call)
    agent._on_task_complete_handlers["booking_confirmation"](call)
    assert p.calls == counts["smtp"] == 1


@pytest.mark.parametrize(
    "answer", ["Yes", "Yes please", "Yes, book, please email my confirmation."]
)
def test_natural_confirmation_binds_to_exact_actual_readback(tmp_path, monkeypatch, answer):
    _, tx, p, _ = make(tmp_path, monkeypatch)
    tx.present("routine checkup", {})
    tx.select("1")
    tx.observe_agent(
        "readout",
        "Office Visit Avery Brooks Willow Creek Clinic "
        "November 24 2026 10:00 AM Eastern. Email patient@example.test. "
        "May I book this appointment and email the confirmation?",
    )
    tx.observe_caller("call", "natural-consent", answer)
    assert tx.commit()["completed"] and p.calls == 1


def test_yes_to_unrelated_question_does_not_authorize_booking(tmp_path, monkeypatch):
    _, tx, p, _ = make(tmp_path, monkeypatch)
    tx.present("routine checkup", {})
    tx.select("1")
    tx.observe_agent(
        "readout",
        "Office Visit Avery Brooks Willow Creek Clinic "
        "November 24 2026 10:00 AM Eastern. Email patient@example.test. "
        "May I book this appointment and email the confirmation?",
    )
    tx.observe_agent("unrelated", "Can you hear me?")
    tx.observe_caller("call", "yes", "Yes")
    assert not tx.commit()["completed"] and p.calls == 0


def test_connected_email_waits_for_matching_provider_receipt_and_deduplicates(
    tmp_path, monkeypatch
):
    import json
    import threading
    import time

    from kyron_eval.storage import atomic_json
    from kyron_eval.voice.booking_email import ConnectorEmail

    _, tx, _, _ = make(tmp_path, monkeypatch)
    ready(tx)
    consent(tx)
    assert tx.commit()["completed"]
    bridge = tmp_path / "bridge"
    email = ConnectorEmail(
        tmp_path / "connector.sqlite",
        bridge,
        recipient="patient@example.test",
        authorized_recipients=["patient@example.test"],
    )

    def provider_ack():
        for _ in range(100):
            files = list(bridge.glob("*.request.json"))
            if files:
                request = json.loads(files[0].read_text())
                atomic_json(
                    bridge / (request["attempt_id"] + ".result.json"),
                    {
                        "attempt_id": request["attempt_id"],
                        "appointment_id": "91",
                        "recipient": "patient@example.test",
                        "status": "SENT",
                        "provider_message_id": "fixture-provider-id",
                        "inbox_observed": True,
                    },
                )
                return
            time.sleep(0.01)

    worker = threading.Thread(target=provider_ack)
    worker.start()
    first = email.send(tx.receipt)
    second = email.send(tx.receipt)
    worker.join()
    assert first == second and first["status"] == "SENT"
    assert first["delivery_status"] == "DELIVERED" and first["inbox_delivery_proven"]
    assert len(list(bridge.glob("*.request.json"))) == 1

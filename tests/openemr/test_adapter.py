"""Unit tests use synthetic OpenEMR-shaped responses, not live-server evidence."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from clinical_triage.domain.scheduling import (
    AppointmentConfirmation,
    AppointmentOffer,
    AvailabilitySnapshot,
)
from kyron_eval.ehr.openemr import OpenEMRAdapter
from kyron_eval.replay import load_scenarios

pytestmark = pytest.mark.unit


def confirmation():
    s = load_scenarios()[0]
    snapshot = AvailabilitySnapshot(
        snapshot_id="synthetic-operator-request",
        observed_at=s.clock,
        expires_at=s.clock + timedelta(minutes=5),
        slots=(s.initial_state.slots[0],),
    )
    offer = AppointmentOffer(
        offer_id="synthetic-operator-offer",
        snapshot=snapshot,
        slot=snapshot.slots[0],
        offered_at=s.clock,
        expires_at=snapshot.expires_at,
    )
    return AppointmentConfirmation(
        confirmation_id="synthetic-confirmation",
        confirmation_event_id="e-confirmed",
        offer=offer,
        confirmed_at=s.clock,
    )


def fixture(
    tmp_path,
    *,
    write_status=200,
    lost_ack=False,
    read_timeout=False,
    patient_marker=True,
    validation_200=False,
):
    manifest = {
        "instance": "kyron-openemr-synthetic",
        "synthetic_only": True,
        "openemr_version": "7.0.4",
        "base_url": "https://localhost:9310",
        "patients": {
            "synthetic-patient-1": {
                "pid": "1",
                "uuid": "patient-uuid",
                "pubpid": "KYRON-SYNTHETIC-001",
                "fname": "KyronSynthetic",
                "lname": "PatientOne",
            }
        },
        "provider": {"id": "1", "uuid": "provider-uuid"},
        "facility": {"id": "3", "uuid": "facility-uuid"},
    }
    requests = []
    records = []

    def handle(request):
        requests.append(request)
        path = request.url.path.removeprefix("/apis/default/")
        if path == "fhir/metadata":
            return httpx.Response(
                200,
                json={
                    "resourceType": "CapabilityStatement",
                    "fhirVersion": "4.0.1",
                    "software": {"name": "OpenEMR", "version": "7.0.4"},
                    "rest": [
                        {
                            "mode": "server",
                            "resource": [
                                {
                                    "type": kind,
                                    "interaction": [{"code": "read"}, {"code": "search-type"}],
                                }
                                for kind in [
                                    "Patient",
                                    "Appointment",
                                    "Condition",
                                    "MedicationRequest",
                                    "Observation",
                                ]
                            ],
                        }
                    ],
                },
            )
        if path == "fhir/Patient/patient-uuid":
            return httpx.Response(
                200,
                json={
                    "resourceType": "Patient",
                    "id": "patient-uuid",
                    "identifier": [
                        {"value": "KYRON-SYNTHETIC-001" if patient_marker else "NOT_SYNTHETIC"}
                    ],
                    "name": [{"family": "PatientOne", "given": ["KyronSynthetic"]}],
                    "birthDate": "1980-01-02",
                    "telecom": [{"system": "phone", "value": "2025550100"}],
                },
            )
        if request.method == "POST":
            if validation_200:
                return httpx.Response(
                    200, json={"pc_catid": {"Numeric::NOT_NUMERIC": "Synthetic invalid category"}}
                )
            if write_status != 200:
                return httpx.Response(
                    write_status, json={"validationErrors": ["synthetic invalid value"]}
                )
            body = json.loads(request.content)
            records.append(
                {
                    "pc_eid": str(len(records) + 1),
                    "pc_uuid": "appointment-" + str(len(records) + 1),
                    "pid": 1,
                    "puuid": "patient-uuid",
                    "pc_title": body["pc_title"],
                    "pc_aid": "1",
                    "pce_aid_uuid": "provider-uuid",
                    "pc_facility": "3",
                    "facility_uuid": "location-uuid",
                    "pc_eventDate": body["pc_eventDate"],
                    "pc_startTime": body["pc_startTime"] + ":00",
                    "pc_endTime": "14:30:00",
                }
            )
            if lost_ack:
                raise httpx.ReadTimeout("injected lost ACK")
            return httpx.Response(200, json={"id": int(records[-1]["pc_eid"])})
        if path == "fhir/Appointment":
            if read_timeout:
                raise httpx.ReadTimeout("injected read failure")
            entries = [
                {
                    "resource": {
                        "resourceType": "Appointment",
                        "id": r["pc_uuid"],
                        "status": "booked",
                        "start": r["pc_eventDate"] + "T" + r["pc_startTime"] + "+00:00",
                        "end": r["pc_eventDate"] + "T" + r["pc_endTime"] + "+00:00",
                        "participant": [
                            {"actor": {"reference": ref}}
                            for ref in [
                                "Patient/patient-uuid",
                                "Person/provider-uuid",
                                "Location/location-uuid",
                            ]
                        ],
                    }
                }
                for r in records
            ]
            return httpx.Response(
                200,
                json={
                    "resourceType": "Bundle",
                    "type": "collection",
                    "total": len(entries),
                    "entry": entries,
                },
            )
        if path == "api/patient/1/appointment":
            return httpx.Response(200, json=records)
        return httpx.Response(
            200, json={"resourceType": "Bundle", "type": "collection", "total": 0}
        )

    p = OpenEMRAdapter(
        manifest,
        token="synthetic-unit-token",
        verify_tls=True,
        ledger_path=tmp_path / "ledger.sqlite",
        allow_writes=True,
        client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    return p, requests, records


def test_normalized_patient_identity_and_actual_empty_collection(tmp_path):
    p, requests, _ = fixture(tmp_path)
    assert p.get_patient("synthetic-patient-1").data["marker_verified"]
    assert p.verify_identity("synthetic-patient-1", "1980-01-02", "2025550100").data["verified"]
    assert p.get_appointments("synthetic-patient-1").data["bookings"] == []
    assert not p.capabilities()["operations"]["get_availability"]
    assert p.get_availability(None).status == "UNSUPPORTED"
    assert not any(r.url.path.endswith("Slot") for r in requests)
    assert "synthetic-unit-token" not in json.dumps(p.http_events)


def test_successful_write_requires_independent_readback_and_idempotency(tmp_path):
    p, requests, _ = fixture(tmp_path)
    inputs = {
        "patient_id": "synthetic-patient-1",
        "confirmation": confirmation(),
        "idempotency_key": "test-request",
    }
    first = p.create_appointment(**inputs)
    assert first.ok and not first.authoritative
    assert p.create_appointment(**inputs) == first
    assert sum(r.method == "POST" for r in requests) == 1
    records = p.get_appointments("synthetic-patient-1")
    assert records.ok and records.authoritative
    assert records.data["bookings"][0]["confirmation_event_id"] == "e-confirmed"
    assert records.data["bookings"][0]["slot"]["slot_id"] == "synthetic-slot-1"
    changed = confirmation().model_copy(update={"confirmation_event_id": "different-confirmation"})
    assert (
        p.create_appointment(**{**inputs, "confirmation": changed}).failure_code
        == "OPENEMR_IDEMPOTENCY_CONFLICT"
    )


def test_lost_ack_is_unknown_and_never_automatically_retried(tmp_path):
    p, requests, records = fixture(tmp_path, lost_ack=True)
    inputs = {
        "patient_id": "synthetic-patient-1",
        "confirmation": confirmation(),
        "idempotency_key": "lost",
    }
    assert p.create_appointment(**inputs).status == "UNKNOWN"
    assert p.create_appointment(**inputs).status == "UNKNOWN"
    assert len(records) == 1 and sum(r.method == "POST" for r in requests) == 1
    assert p.get_appointments("synthetic-patient-1").data["bookings"]


def test_readback_timeout_remains_unknown_and_safe_reads_are_bounded(tmp_path):
    p, requests, _ = fixture(tmp_path, read_timeout=True)
    result = p.get_appointments("synthetic-patient-1")
    assert result.status == "UNKNOWN" and not result.authoritative
    assert sum(r.url.path.endswith("/Appointment") for r in requests) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
def test_http_rejections_are_explicit_not_committed_success(tmp_path, status):
    p, _, _ = fixture(tmp_path, write_status=status)
    result = p.create_appointment(
        patient_id="synthetic-patient-1", confirmation=confirmation(), idempotency_key="rejected"
    )
    assert result.status == (
        "UNSUPPORTED" if status == 404 else "UNKNOWN" if status == 500 else "ERROR"
    )
    assert not result.ok


def test_wrong_patient_and_unmarked_patient_are_blocked_before_mutation(tmp_path):
    p, requests, _ = fixture(tmp_path, patient_marker=False)
    assert p.get_patient("synthetic-patient-1").failure_code == "OPENEMR_SYNTHETIC_BINDING_REJECTED"
    assert p.get_patient("synthetic-other").failure_code == "OPENEMR_PATIENT_NOT_ALLOWLISTED"
    assert p.cancel_appointment("synthetic-patient-1", "7").status == "ERROR"
    assert not any(r.method in {"POST", "DELETE"} for r in requests)


def test_all_pre_openemr_evidence_is_byte_identical():
    root = Path(__file__).parents[2]
    manifest = json.loads(
        (root / "artifacts/verification/openemr-preservation-manifest.json").read_text()
    )
    for path, sha in manifest["files"].items():
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == sha, path


def test_optional_text_agent_can_use_isolated_openemr_normalized_tools(tmp_path):
    from kyron_eval.llm_agent import execute_llm

    p, requests, _ = fixture(tmp_path)

    class ScriptedModel:
        def __init__(self):
            self.calls = 0
            self.observed = False

        def public_config(self):
            return {"model": "scripted-unit-test", "not_model_performance": True}

        def complete(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "synthetic-tool",
                                "type": "function",
                                "function": {
                                    "name": "get_appointments",
                                    "arguments": '{"patient_id":"synthetic-patient-1"}',
                                },
                            }
                        ],
                    }
                }
            self.observed = any(
                m["role"] == "tool" and json.loads(m["content"]).get("status") == "OK"
                for m in messages
            )
            return {
                "message": {
                    "role": "assistant",
                    "content": "I cannot verify availability. Please call the clinic.",
                }
            }

    model = ScriptedModel()
    trace = execute_llm(load_scenarios()[0], adapter=p, model_client=model)
    assert model.observed
    assert any(e.kind == "model" for e in trace.events)
    assert any(e.name == "ehr.get_appointments" for e in trace.events)
    assert not any(r.method in {"POST", "DELETE"} for r in requests)


@pytest.mark.parametrize(
    "url", ["https://example.com", "http://localhost:9310", "https://localhost:9310/?secret=test"]
)
def test_openemr_never_accepts_production_or_unverified_targets(tmp_path, url):
    p, _, _ = fixture(tmp_path)
    manifest = {**p.manifest, "base_url": url}
    with pytest.raises(ValueError, match="loopback"):
        OpenEMRAdapter(
            manifest, token="synthetic", verify_tls=True, ledger_path=tmp_path / "another.sqlite"
        )


def test_durable_namespace_prevents_reposting_after_client_restart(tmp_path):
    first, requests, _ = fixture(tmp_path)
    args = {
        "patient_id": "synthetic-patient-1",
        "confirmation": confirmation(),
        "idempotency_key": "durable-request",
    }
    result = first.create_appointment(**args)
    restarted = OpenEMRAdapter(
        first.manifest,
        token="synthetic-unit-token",
        verify_tls=True,
        ledger_path=first.ledger_path,
        operation_namespace=first.namespace,
        allow_writes=True,
        client=first._client,
        auth_context={"access_token": "MUST_NOT_BE_RETAINED", "method": "OAuth2 test"},
    )
    assert restarted.create_appointment(**args) == result
    assert sum(r.method == "POST" for r in requests) == 1
    assert "MUST_NOT_BE_RETAINED" not in json.dumps(restarted.capabilities())


def test_http_200_legacy_validation_body_is_rejected_not_acknowledged(tmp_path):
    p, requests, records = fixture(tmp_path, validation_200=True)
    result = p.create_appointment(
        patient_id="synthetic-patient-1",
        confirmation=confirmation(),
        idempotency_key="legacy-invalid",
    )
    assert result.status == "ERROR" and result.failure_code == "OPENEMR_VALIDATION_REJECTED"
    assert p.get_appointments("synthetic-patient-1").data["bookings"] == []
    assert records == [] and sum(r.method == "POST" for r in requests) == 1

"""Opt-in real-server tests. No production targets or existing installations accepted."""

import os

import httpx
import pytest
from test_adapter import confirmation

from kyron_eval.ehr.config import openemr_adapter

pytestmark = [
    pytest.mark.openemr_integration,
    pytest.mark.skipif(
        os.environ.get("KYRON_RUN_OPENEMR_TESTS") != "1",
        reason="Explicit opt-in required for isolated real OpenEMR integration",
    ),
]


@pytest.fixture
def provider():
    p = openemr_adapter()
    yield p
    p.close()


def test_live_patient_search_identity_and_source_version(provider):
    caps = provider.capabilities()
    assert caps["connection"] == "connected" and caps["openemr_version"] == "7.0.4"
    patient = provider.get_patient("synthetic-patient-1")
    assert patient.ok and patient.data["marker_verified"]
    assert provider.search_patients("synthetic-patient-1").ok
    identity = provider.verify_identity("synthetic-patient-1", "1980-01-02", "2025550100")
    assert identity.data["verified"]
    assert not provider.verify_identity("synthetic-patient-1", "1900-01-01", "2025550100").data[
        "verified"
    ]


def test_live_complete_appointment_readback_and_unavailable_review_state(provider):
    appointments = provider.get_appointments("synthetic-patient-1")
    assert appointments.ok and appointments.authoritative
    assert all(b["patient_id"] == "synthetic-patient-1" for b in appointments.data["bookings"])
    state = provider.read_state("synthetic-patient-1", None)
    assert state.status == "UNKNOWN"
    assert state.data["appointments"]["authoritative"]
    assert state.data["unavailable_collections"] == ["reviews"]


def test_live_create_readback_idempotency_and_cancel_only_own_record(provider):
    if not provider.allow_writes:
        pytest.skip("Explicit synthetic write authorization required")
    before = provider.get_appointments("synthetic-patient-1")
    assert before.ok
    prior = {r["appointment_id"] for r in before.data["bookings"]}
    args = {
        "patient_id": "synthetic-patient-1",
        "confirmation": confirmation(),
        "idempotency_key": "live-test-create",
    }
    created = provider.create_appointment(**args)
    assert created.ok
    target = created.data["appointment_id"]
    assert target not in prior
    assert provider.create_appointment(**args) == created
    independent = openemr_adapter()
    try:
        observed = independent.get_appointments("synthetic-patient-1")
        assert observed.ok and observed.authoritative
        record = next(r for r in observed.data["bookings"] if r["appointment_id"] == target)
        assert record["slot"] == confirmation().offer.slot.model_dump(mode="json")
        assert record["confirmation_event_id"] == "e-confirmed"
        assert (
            independent.cancel_appointment("synthetic-patient-2", target).failure_code
            == "OPENEMR_APPOINTMENT_NOT_BOUND"
        )
        assert provider.cancel_appointment("synthetic-patient-1", target).ok
        after = independent.get_appointments("synthetic-patient-1")
        assert after.ok and not any(r["appointment_id"] == target for r in after.data["bookings"])
    finally:
        independent.close()
    assert len([e for e in provider.http_events if e["method"] == "POST"]) == 1


def test_live_unsupported_availability_and_non_allowlisted_patient(provider):
    assert provider.get_availability(None).status == "UNSUPPORTED"
    assert (
        provider.get_patient("synthetic-not-seeded").failure_code
        == "OPENEMR_PATIENT_NOT_ALLOWLISTED"
    )
    assert (
        provider.create_human_review_request("synthetic-patient-1", "SYNTHETIC_REVIEW").status
        == "UNSUPPORTED"
    )


def test_live_selected_clinical_sections_have_explicit_missing_semantics(provider):
    result = provider.get_clinical_context("synthetic-patient-1")
    assert result.ok
    assert "allergy_codes" not in result.data
    assert "allergy_codes" in result.data["unavailable_sections"]


def test_live_invalid_token_is_rejected_without_exposing_response_body(provider):
    provider._token = "invalid-synthetic-test-token"
    result = provider.get_patient("synthetic-patient-1")
    assert result.status == "ERROR"
    assert result.failure_code == "OPENEMR_AUTHORIZATION_REJECTED"
    assert "invalid-synthetic-test-token" not in result.model_dump_json()


def test_live_transport_readback_fault_is_unknown(provider):
    """Real discovery/patient reads; injected client-side Appointment readback failure."""
    original = provider._client

    def transport(request):
        if request.url.path.endswith("/fhir/Appointment"):
            raise httpx.ReadTimeout("Injected client-side readback failure")
        return original.send(request)

    provider._client = httpx.Client(transport=httpx.MockTransport(transport))
    try:
        readback = provider.get_appointments("synthetic-patient-1")
        assert readback.status == "UNKNOWN" and not readback.authoritative
        assert len([e for e in provider.http_events if e["path"].endswith("/Appointment")]) == 2
    finally:
        provider._client.close()
        provider._client = original


def test_live_http_200_validation_failure_does_not_establish_a_mutation(provider):
    before = provider.get_appointments("synthetic-patient-1")
    assert before.ok
    patient = provider.patient_map["synthetic-patient-1"]
    result = provider._run(
        "create_appointment",
        lambda: provider._request(
            "POST",
            f"api/patient/{patient['pid']}/appointment",
            body={"pc_catid": "INVALID_SYNTHETIC_CATEGORY"},
        ),
    )
    assert result.status == "ERROR" and result.failure_code == "OPENEMR_VALIDATION_REJECTED"
    assert (
        next(e for e in reversed(provider.http_events) if e["method"] == "POST")["http_status"]
        == 200
    )
    after = provider.get_appointments("synthetic-patient-1")
    assert after.ok and before.data["bookings"] == after.data["bookings"]

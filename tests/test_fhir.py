from copy import deepcopy

import httpx
import pytest

from kyron_eval.ehr.fhir import FHIRAdapter
from kyron_eval.ehr.fixture_server import bundle, capability_statement, fixture_resources
from kyron_eval.ehr.mock import SYNTHETIC_DOB, SYNTHETIC_PHONE
from kyron_eval.replay import load_scenarios


def adapter(*, override=None, metadata=None, response_status=200, headers=None):
    requests = []

    def handler(request):
        requests.append(request)
        path = request.url.path.removeprefix("/fhir/")
        if path == "metadata":
            payload = metadata or capability_statement()
        elif override:
            payload = override(path)
        elif "/" in path:
            kind, id = path.split("/", 1)
            payload = next(r for r in fixture_resources()[kind] if r["id"] == id)
        else:
            payload = bundle(fixture_resources()[path])
        return httpx.Response(response_status, json=payload, headers=headers)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return FHIRAdapter(
        "https://localhost/fhir", bearer_token="synthetic-test-token", client=client
    ), requests


def test_discovery_identity_reads_and_normalized_resources():
    p, requests = adapter()
    caps = p.capabilities()
    assert caps["connection"] == "connected"
    assert caps["operations"]["get_appointments"]
    assert not caps["operations"]["create_appointment"]
    assert p.get_patient("synthetic-patient-1").data == {
        "patient_id": "synthetic-patient-1",
        "synthetic": True,
    }
    assert p.verify_identity("synthetic-patient-1", SYNTHETIC_DOB, SYNTHETIC_PHONE).data["verified"]
    appointments = p.get_appointments("synthetic-patient-1")
    assert appointments.ok and appointments.authoritative
    assert appointments.data["bookings"][0]["appointment_id"] == "synthetic-existing-appointment"
    now = load_scenarios()[0].clock
    assert len(p.get_availability(now).data["snapshot"]["slots"]) == 2
    assert p.get_clinical_context("synthetic-patient-1").data["condition_codes"] == [
        "synthetic-condition"
    ]
    assert all(r.headers["Authorization"] == "Bearer synthetic-test-token" for r in requests)
    assert "synthetic-test-token" not in str(caps)


def test_writes_never_issue_http_even_if_server_advertises_create():
    metadata = capability_statement()
    metadata["rest"][0]["resource"][1]["interaction"].append({"code": "create"})
    p, requests = adapter(metadata=metadata)
    p.capabilities()
    count = len(requests)
    assert (
        p.create_appointment(
            patient_id="synthetic-patient-1", confirmation=None, idempotency_key="synthetic"
        ).status
        == "UNSUPPORTED"
    )
    assert (
        p.cancel_appointment("synthetic-patient-1", "synthetic-appointment").status == "UNSUPPORTED"
    )
    assert p.create_human_review_request("synthetic-patient-1", "SYNTHETIC").status == "UNSUPPORTED"
    assert len(requests) == count
    assert all(r.method == "GET" for r in requests)


def test_unknown_state_preserves_available_appointment_readback():
    p, _ = adapter()
    result = p.read_state("synthetic-patient-1", load_scenarios()[0].clock)
    assert result.status == "UNKNOWN"
    assert result.data["appointments"]["authoritative"] is True
    assert result.data["unavailable_collections"] == ["reviews"]


def test_patient_allowlist_blocks_request_before_data_access():
    p, requests = adapter()
    result = p.get_patient("real-patient-id")
    assert result.failure_code == "FHIR_PATIENT_NOT_ALLOWLISTED"
    assert not requests


def test_untagged_resources_are_rejected_without_retaining_body():
    patient = deepcopy(fixture_resources()["Patient"][0])
    patient.pop("meta")
    patient["name"] = [{"text": "DO-NOT-RETAIN"}]
    p, _ = adapter(override=lambda path: patient)
    result = p.get_patient("synthetic-patient-1")
    assert result.failure_code == "FHIR_SYNTHETIC_MARKER_REQUIRED"
    assert "DO-NOT-RETAIN" not in result.model_dump_json()


def test_capability_gaps_are_explicit_unsupported():
    metadata = capability_statement()
    metadata["rest"][0]["resource"] = [{"type": "Patient", "interaction": [{"code": "read"}]}]
    p, requests = adapter(metadata=metadata)
    assert p.get_appointments("synthetic-patient-1").status == "UNSUPPORTED"
    assert len(requests) == 1


def test_incomplete_search_results_and_stale_responses_are_unknown():
    partial = bundle(fixture_resources()["Appointment"])
    partial["link"] = [{"relation": "next", "url": "https://external.invalid/steal"}]
    p, requests = adapter(override=lambda path: partial)
    assert p.get_appointments("synthetic-patient-1").failure_code == "FHIR_INCOMPLETE_BUNDLE"
    assert len(requests) == 2
    p, _ = adapter(headers={"Age": "120"})
    assert p.get_patient("synthetic-patient-1").status == "UNKNOWN"


def test_wrong_patient_appointment_is_rejected():
    appointment = deepcopy(fixture_resources()["Appointment"][0])
    appointment["participant"][0]["actor"]["reference"] = "Patient/synthetic-other"
    p, _ = adapter(override=lambda path: bundle([appointment]))
    assert (
        p.get_appointments("synthetic-patient-1").failure_code
        == "FHIR_APPOINTMENT_PATIENT_MISMATCH"
    )


@pytest.mark.parametrize(
    "status,expected", [(401, "ERROR"), (500, "ERROR"), (404, "UNSUPPORTED"), (302, "ERROR")]
)
def test_http_rejections_are_sanitized_and_never_follow_redirects(status, expected):
    p, _ = adapter(response_status=status)
    assert p.get_patient("synthetic-patient-1").status == expected


def test_timeout_is_unknown_not_an_empty_successful_read():
    def handler(request):
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    p = FHIRAdapter(
        "http://127.0.0.1/fhir", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = p.get_patient("synthetic-patient-1")
    assert result.status == "UNKNOWN" and not result.authoritative
    assert not result.data


@pytest.mark.parametrize(
    "url",
    [
        "https://production.example/fhir",
        "http://sandbox.example/fhir",
        "https://token@localhost/fhir",
        "https://localhost/fhir?token=abc",
    ],
)
def test_unsafe_endpoint_configuration_is_rejected(url):
    with pytest.raises(ValueError):
        FHIRAdapter(url)


def test_tls_cannot_be_silently_disabled():
    with pytest.raises(ValueError, match="TLS verification"):
        FHIRAdapter("https://localhost/fhir", verify_tls=False)


def test_verified_agent_uses_partial_appointments_without_fabricating_review_state():
    from kyron_eval.evaluate import evaluate
    from kyron_eval.replay import replay

    s = next(s for s in load_scenarios() if s.id == "booking-confirmed")
    p, requests = adapter()
    trace = replay(s, "B", adapter=p)
    assert trace.state_after is None
    assert trace.ehr["final_readback"]["data"]["appointments"]["authoritative"]
    final = [e.text for e in trace.events if e.kind == "agent"][-1]
    assert "does not support booking" in final
    assert "not saved" in final
    assert all(r.method == "GET" for r in requests)
    scores = {m.name: m.status for m in evaluate(s, trace, clarity_version="clarity-ehr-3.2")}
    assert scores["ehr_write_verification"] == "FAIL"
    assert scores["resolution_clarity"] == "PASS"
    assert scores["ehr_claim_support"] == "N/A"


def test_malformed_resource_metadata_is_unknown_not_a_crash():
    patient = deepcopy(fixture_resources()["Patient"][0])
    patient["meta"] = []
    p, _ = adapter(override=lambda path: patient)
    assert p.get_patient("synthetic-patient-1").status == "UNKNOWN"


def test_unread_allergies_are_not_normalized_into_a_false_empty_collection():
    p, _ = adapter()
    context = p.get_clinical_context("synthetic-patient-1")
    assert context.status == "OK"
    assert "allergy_codes" not in context.data
    assert context.data["unavailable_sections"] == ["allergy_codes"]

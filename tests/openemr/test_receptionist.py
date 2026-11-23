"""Synthetic protocol regressions; optional marked local OpenEMR reads are distinct."""

import os
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from guava.events import CallerSpeechEvent
from test_adapter import confirmation, fixture

from kyron_eval.voice.live_demo import DemoController, create_demo_app
from kyron_eval.voice.receptionist import ReceptionistSession

NAME = "KyronSynthetic PatientOne"
DOB = "1980-01-02"
PHONE = "2025550100"


def adapter(tmp_path, *, rows=None, next_page=False, appointments=True):
    provider, requests, records = fixture(tmp_path)
    if appointments:
        assert provider.create_appointment(
            patient_id="synthetic-patient-1",
            confirmation=confirmation(),
            idempotency_key="operator-setup",
        ).ok
        records[0].update(facility_name="Willow Creek Clinic", pc_catid="5", pc_apptstatus="EMAIL")
    original = provider._client
    native = {
        "pid": "1",
        "uuid": "patient-uuid",
        "pubpid": "KYRON-SYNTHETIC-001",
        "fname": "KyronSynthetic",
        "lname": "PatientOne",
        "mname": "",
        "DOB": DOB,
    }
    source_rows = [native] if rows is None else rows

    def handle(request):
        if request.url.path == "/apis/default/api/patient":
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "data": source_rows,
                    "links": {"next": "https://localhost:9310/next"}
                    if next_page
                    else {"first": "local"},
                },
            )
        if request.url.path == "/apis/default/api/user/provider-uuid":
            requests.append(request)
            return httpx.Response(
                200, json={"data": [{"uuid": "provider-uuid", "fname": "Avery", "lname": "Brooks"}]}
            )
        return original.send(request)

    provider._client = httpx.Client(transport=httpx.MockTransport(handle))
    requests.clear()
    return provider, requests, records


def test_name_dob_search_uses_actual_native_api_and_requires_additional_factor(tmp_path):
    p, requests, _ = adapter(tmp_path)
    result = p.match_name_and_dob(NAME, DOB)
    assert result.ok and result.authoritative
    assert result.data["candidate_id"] == "synthetic-patient-1"
    assert result.data["disclosure_authorized"] is False
    assert result.data["additional_factor_required"] == "PHONE"
    request = next(r for r in requests if r.url.path.endswith("/api/patient"))
    assert request.url.params["DOB"] == DOB and request.url.params["lname"] == "patientone"


@pytest.mark.parametrize("name,dob", [("Nobody Here", DOB), (NAME, "1900-01-01"), ("Onlyone", DOB)])
def test_wrong_or_incomplete_identity_does_not_return_patient_details(tmp_path, name, dob):
    p, _, _ = adapter(tmp_path)
    result = p.match_name_and_dob(name, dob)
    assert not result.ok and not result.data


def test_ambiguous_duplicate_names_and_pagination_fail_closed(tmp_path):
    p, _, _ = adapter(tmp_path)
    raw = {
        "pid": "1",
        "uuid": "patient-uuid",
        "pubpid": "KYRON-SYNTHETIC-001",
        "fname": "KyronSynthetic",
        "lname": "PatientOne",
        "DOB": DOB,
    }
    p._client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": [raw, {**raw, "pid": "2"}]})
        )
    )
    assert not p.match_name_and_dob(NAME, DOB).ok
    p, _, _ = adapter(tmp_path / "paging", next_page=True)
    assert p.match_name_and_dob(NAME, DOB).status == "UNKNOWN"


def test_unallowlisted_native_candidate_cannot_bind_patient(tmp_path):
    raw = {
        "pid": "999",
        "uuid": "wrong-patient",
        "pubpid": "KYRON-SYNTHETIC-999",
        "fname": "KyronSynthetic",
        "lname": "PatientOne",
        "DOB": DOB,
    }
    p, _, _ = adapter(tmp_path, rows=[raw])
    assert not p.match_name_and_dob(NAME, DOB).ok


def test_session_binds_only_after_unique_name_dob_and_approved_phone_factor(tmp_path):
    p, requests, _ = adapter(tmp_path)
    session = ReceptionistSession(p, tmp_path / "artifacts")
    session.start("protocol-call")
    assert session.service.bound_patient_id == "synthetic-unmatched"
    assert session.snapshot()["patient_id"] is None
    assert not session.refresh("protocol-call")["completed"]
    assert session.identify("protocol-call", NAME, DOB)["completed"]
    assert session.snapshot()["patient_id"] is None and session.snapshot()["appointments"] == []
    assert session.verify_phone_and_read("protocol-call", PHONE)["completed"]
    snapshot = session.snapshot()
    assert snapshot["patient_id"] == "synthetic-patient-1"
    assert snapshot["appointments"][0]["clinician"] == "Avery Brooks"
    assert snapshot["synchronization"] == "SYNCED"
    assert all(r.method == "GET" for r in requests)


def test_failed_reverification_revokes_disclosure_and_previous_selection(tmp_path):
    p, _, _ = adapter(tmp_path)
    session = ReceptionistSession(p, tmp_path)
    session.start("protocol-call")
    session.identify("protocol-call", NAME, DOB)
    session.verify_phone_and_read("protocol-call", PHONE)
    assert session.select("1")["completed"]
    assert not session.identify("protocol-call", NAME, "1900-01-01")["completed"]
    snapshot = session.snapshot()
    assert snapshot["patient_id"] is None and snapshot["appointments"] == []
    assert snapshot["selected_appointment"] is None
    assert not session.refresh("protocol-call")["completed"]


def test_empty_appointment_list_is_truthful_not_placeholder(tmp_path):
    p, _, _ = adapter(tmp_path, appointments=False)
    session = ReceptionistSession(p, tmp_path)
    session.start("protocol-call")
    session.identify("protocol-call", NAME, DOB)
    result = session.verify_phone_and_read("protocol-call", PHONE)
    assert result["completed"] and result["appointments"] == []
    assert session.snapshot()["synchronization"] == "EMPTY"


def test_failed_phone_and_duplicate_callback_cannot_disclose_or_retry_candidate(tmp_path):
    p, requests, _ = adapter(tmp_path)
    session = ReceptionistSession(p, tmp_path)
    session.start("protocol-call")
    session.identify("protocol-call", NAME, DOB)
    assert not session.verify_phone_and_read("protocol-call", "0000000000")["completed"]
    count = len(requests)
    assert not session.verify_phone_and_read("protocol-call", PHONE)["completed"]
    assert len(requests) == count and session.snapshot()["patient_id"] is None
    session.end("protocol-call", "failed identity")
    assert session.latest_run is not None  # Failed verification evidence must still finalize.


@pytest.mark.parametrize("operation", ["NEW_APPOINTMENT", "RESCHEDULE", "CANCEL"])
def test_unsupported_availability_never_mutates_and_reschedule_preserves_original(
    tmp_path, operation
):
    p, requests, records = adapter(tmp_path)
    session = ReceptionistSession(p, tmp_path)
    session.start("protocol-call")
    session.identify("protocol-call", NAME, DOB)
    session.verify_phone_and_read("protocol-call", PHONE)
    assert not session.select("wrong-patient-appointment")["completed"]
    assert session.select("1")["completed"]
    before = list(records)
    first = session.request("protocol-call", operation, {"preferred_date": "Tuesday"})
    second = session.request("protocol-call", operation)
    assert not first["completed"] and not second["completed"]
    assert records == before and all(r.method == "GET" for r in requests)
    assert not p.allow_writes
    assert session.world.adapter.create_appointment().status == "UNSUPPORTED"
    assert session.world.adapter.cancel_appointment().status == "UNSUPPORTED"


def test_urgent_caller_language_blocks_all_routine_requests(tmp_path):
    p, _, _ = adapter(tmp_path)
    session = ReceptionistSession(p, tmp_path)
    session.start("protocol-call")
    session.identify("protocol-call", NAME, DOB)
    session.verify_phone_and_read("protocol-call", PHONE)
    session.caller(
        "protocol-call", CallerSpeechEvent(utterance="new chest pain", utterance_id="u1")
    )
    assert session.triage.state == "URGENT_ESCALATION"
    assert not session.request("protocol-call", "NEW_APPOINTMENT")["completed"]


def test_appointment_tab_and_status_use_the_same_authoritative_encounter(tmp_path):
    p, _, _ = adapter(tmp_path)
    controller = DemoController(
        SimpleNamespace(), "code", "unused", tmp_path, provider_factory=lambda: p
    )
    client = TestClient(create_demo_app(controller))
    session = controller.start_session("protocol-call")
    assert client.get("/appointments").status_code == 200
    initial = client.get("/api/live-demo/status").json()["encounter"]
    assert initial["patient_id"] is None
    session.identify("protocol-call", NAME, DOB)
    session.verify_phone_and_read("protocol-call", PHONE)
    verified = client.get("/api/live-demo/status").json()["encounter"]
    assert initial["encounter_id"] == verified["encounter_id"]
    assert verified["appointments"][0]["appointment_id"] == "1"
    assert client.post("/api/receptionist/select", json={"appointment_id": "1"}).status_code == 403
    headers = {"X-Kyron-Demo": controller.nonce}
    assert not client.post(
        "/api/receptionist/select", headers=headers, json={"appointment_id": "wrong"}
    ).json()["completed"]
    assert client.post(
        "/api/receptionist/select", headers=headers, json={"appointment_id": "1"}
    ).json()["completed"]


def test_dynamic_identity_judgment_checks_receipts_and_unknown_readback(tmp_path):
    from kyron_eval.voice.receptionist_evaluate import evaluate_receptionist

    p, _, _ = adapter(tmp_path)
    session = ReceptionistSession(p, tmp_path)
    session.start("protocol-call")
    session.identify("protocol-call", NAME, DOB)
    session.verify_phone_and_read("protocol-call", PHONE)
    session.end("protocol-call", "protocol fixture")
    metrics = evaluate_receptionist(session.world.trace)
    assert [m.status for m in metrics] == ["PASS", "PASS"]
    corrupt = session.world.trace.model_copy(deep=True)
    read = next(e for e in corrupt.events if e.name == "receptionist.appointment_read")
    read.data["result"]["appointments"][0]["patient_id"] = "other-patient"
    assert evaluate_receptionist(corrupt)[0].status == "FAIL"
    corrupt = session.world.trace.model_copy(deep=True)
    corrupt.ehr["final_readback"] = {}
    assert evaluate_receptionist(corrupt)[1].status == "UNKNOWN"


@pytest.mark.openemr_integration
@pytest.mark.skipif(
    os.environ.get("KYRON_RUN_OPENEMR_TESTS") != "1", reason="Opt-in real local EHR"
)
def test_actual_name_search_additional_factor_and_appointment_display_contract(tmp_path):
    """Actual marked EHR I/O; direct protocol calls are NOT a real audio session."""
    from kyron_eval.ehr.config import openemr_adapter

    p = openemr_adapter()
    session = ReceptionistSession(p, tmp_path)
    session.start("protocol-only-not-live")
    try:
        assert session.identify("protocol-only-not-live", "Jordan Morgan", "1990-04-12")[
            "completed"
        ]
        assert session.snapshot()["appointments"] == []
        result = session.verify_phone_and_read("protocol-only-not-live", "2025550122")
        assert result["completed"]
        assert all(r["patient_name"] == "Jordan Morgan" for r in result["appointments"])
        assert all(e["method"] == "GET" for e in p.http_events)
        assert not session.audio_observed()
    finally:
        p.close()

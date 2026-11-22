"""Actual OpenEMR observations. Creation probes request a time; they do not prove availability."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx

from clinical_triage.domain.scheduling import (
    AppointmentConfirmation,
    AppointmentOffer,
    AvailabilitySnapshot,
)
from kyron_eval.ehr.config import openemr_adapter
from kyron_eval.replay import load_scenarios
from kyron_eval.storage import Store, atomic_json, implementation_digest


def requested_confirmation():
    s = load_scenarios()[0]
    slot = s.initial_state.slots[0]
    snapshot = AvailabilitySnapshot(
        snapshot_id="synthetic-operator-specified-time",
        observed_at=s.clock,
        expires_at=s.clock + timedelta(minutes=5),
        slots=(slot,),
    )
    return AppointmentConfirmation(
        confirmation_id="synthetic-operator-confirmation",
        confirmation_event_id="synthetic-operator-explicit-consent",
        confirmed_at=s.clock,
        offer=AppointmentOffer(
            offer_id="synthetic-operator-request",
            snapshot=snapshot,
            slot=slot,
            offered_at=s.clock,
            expires_at=snapshot.expires_at,
        ),
    )


def compare_created(before, result, after, confirmation, provider):
    if not after.ok or not after.authoritative or not before.ok:
        return {"status": "UNKNOWN", "reason": "Authoritative before/readback evidence unavailable"}
    initial = {r["appointment_id"] for r in before.data["bookings"]}
    marker = next(
        (e["payload"]["pc_title"] for e in provider.http_events if e["method"] == "POST"), None
    )
    matches = [
        r
        for r in after.data["bookings"]
        if r["appointment_id"] not in initial
        and r["patient_id"] == "synthetic-patient-1"
        and r["slot"] == confirmation.offer.slot.model_dump(mode="json")
        and r["confirmation_event_id"] == confirmation.confirmation_event_id
        and (
            not result.data.get("appointment_id")
            or r["appointment_id"] == result.data["appointment_id"]
        )
    ]
    return {
        "status": "PASS" if len(matches) == 1 and marker else "FAIL",
        "reason": (
            "Independent FHIR and standard readback bind a new native ID, "
            "patient, exact time/provider/location and confirmation marker"
        ),
        "matching_native_appointment_ids": [r["appointment_id"] for r in matches],
        "agent_completed_booking": False,
        "availability_verified": False,
    }


def creation_probe(*, lose_ack=False, missing_readback=False):
    provider = openemr_adapter()
    observer = openemr_adapter()
    try:
        before = provider.get_appointments("synthetic-patient-1")
        assert before.ok
        original = provider._client
        if lose_ack:

            def transport(request):
                response = original.send(request)
                if request.method == "POST":
                    response.read()
                    response.close()
                    raise httpx.ReadTimeout("Injected lost response after real OpenEMR operation")
                return response

            provider._client = httpx.Client(transport=httpx.MockTransport(transport))
        confirmation = requested_confirmation()
        result = provider.create_appointment(
            patient_id="synthetic-patient-1",
            confirmation=confirmation,
            idempotency_key="operator-" + uuid4().hex,
        )
        if lose_ack:
            provider._client.close()
            provider._client = original
        if missing_readback:
            original_observer = observer._client

            def blocked(request):
                if request.url.path.endswith("/fhir/Appointment"):
                    raise httpx.ReadTimeout("Injected client-side readback failure")
                return original_observer.send(request)

            observer._client = httpx.Client(transport=httpx.MockTransport(blocked))
        after = observer.get_appointments("synthetic-patient-1")
        verification = compare_created(before, result, after, confirmation, provider)
        evidence = {
            "evidence_class": "SYNTHETIC_REAL_OPENEMR_PROVIDER_EXPERIMENT",
            "execution_mode": (
                "operator API probe; not agent availability search or LLM performance"
            ),
            "injected_fault": "discarded real mutation response"
            if lose_ack
            else "client-side readback unavailable"
            if missing_readback
            else None,
            "before": before.model_dump(mode="json"),
            "intended_confirmation": confirmation.model_dump(mode="json"),
            "tool_response": result.model_dump(mode="json"),
            "independent_readback": after.model_dump(mode="json"),
            "verification": verification,
            "mutation_http_evidence": list(provider.http_events),
            "independent_readback_http_evidence": list(observer.http_events),
        }
        if missing_readback:
            observer._client.close()
            observer._client = original_observer
            recovered = observer.get_appointments("synthetic-patient-1")
            evidence["later_recovery"] = {
                "readback": recovered.model_dump(mode="json"),
                "verification": compare_created(before, result, recovered, confirmation, provider),
                "note": "Later evidence does not overwrite the original UNKNOWN judgment",
            }
        return evidence
    finally:
        provider.close()
        observer.close()


def rejected_creation_probe():
    provider = openemr_adapter()
    try:
        before = provider.get_appointments("synthetic-patient-1")
        patient = provider.patient_map["synthetic-patient-1"]
        body = {
            "pc_catid": "INVALID_SYNTHETIC_CATEGORY",
            "pc_title": "KYRON_SYNTHETIC_REJECTED_CONTROL",
        }
        result = provider._run(
            "create_appointment",
            lambda: provider._request(
                "POST", f"api/patient/{patient['pid']}/appointment", body=body
            ),
        )
        after = provider.get_appointments("synthetic-patient-1")
        known = before.ok and after.ok
        unchanged = known and before.data["bookings"] == after.data["bookings"]
        return {
            "tool_response": result.model_dump(mode="json"),
            "verification": {
                "status": "FAIL" if unchanged and not result.ok else "UNKNOWN",
                "control_expectation_met": bool(unchanged and result.status == "ERROR"),
                "reason": "Rejected request; readback establishes no new record",
            },
            "before": before.model_dump(mode="json"),
            "independent_readback": after.model_dump(mode="json"),
            "http_evidence": list(provider.http_events),
            "injected_fault": None,
        }
    finally:
        provider.close()


def wrong_target_probe():
    provider = openemr_adapter()
    try:
        before = provider.get_appointments("synthetic-patient-1")
        target = before.data["bookings"][0]["appointment_id"]
        result = provider.cancel_appointment("synthetic-patient-2", target)
        after = provider.get_appointments("synthetic-patient-1")
        return {
            "intended_target": {"patient_id": "synthetic-patient-2", "appointment_id": target},
            "tool_response": result.model_dump(mode="json"),
            "before": before.model_dump(mode="json"),
            "after": after.model_dump(mode="json"),
            "delete_requests_sent": sum(e["method"] == "DELETE" for e in provider.http_events),
            "control_expectation_met": result.failure_code == "OPENEMR_APPOINTMENT_NOT_BOUND"
            and before.ok
            and after.ok
            and before.data["bookings"] == after.data["bookings"],
            "http_evidence": list(provider.http_events),
        }
    finally:
        provider.close()


def main():
    if os.environ.get("KYRON_OPENEMR_ALLOW_WRITES") != "1":
        raise SystemExit("Explicit synthetic local write authorization required")
    provider = openemr_adapter()
    try:
        reads = {
            "capabilities": provider.capabilities(),
            "patient_search": provider.search_patients("synthetic-patient-1").model_dump(
                mode="json"
            ),
            "patient": provider.get_patient("synthetic-patient-1").model_dump(mode="json"),
            "appointments": provider.get_appointments("synthetic-patient-1").model_dump(
                mode="json"
            ),
            "context": provider.get_clinical_context("synthetic-patient-1").model_dump(mode="json"),
            "unsupported_availability": provider.get_availability(None).model_dump(mode="json"),
            "wrong_patient": provider.get_patient("synthetic-not-seeded").model_dump(mode="json"),
        }
        empty = provider.get_appointments("synthetic-patient-2")
        native_id = provider.patient_map["synthetic-patient-2"]["pid"]
        legacy_empty = provider._run(
            "legacy_empty_list",
            lambda: provider._request("GET", f"api/patient/{native_id}/appointment"),
        )
        reads["empty_legacy_list"] = {
            "legacy_response": legacy_empty.model_dump(mode="json"),
            "authoritative_fhir_readback": empty.model_dump(mode="json"),
            "interpretation": (
                "A legacy 404 alone does not establish absence; complete FHIR evidence does"
            ),
        }
        reads["final_authorization"] = provider.authorization
        reads["http_evidence"] = list(provider.http_events)
    finally:
        provider.close()
    output = {
        "created_at": datetime.now(UTC).isoformat(),
        "base_commit": "78b7b84",
        "branch": "codex/openemr-integration",
        "implementation_sha256": implementation_digest(),
        "synthetic_only": True,
        "openemr_version": "7.0.4",
        "actual_llm_called": False,
        "reads": reads,
        "successful_creation": creation_probe(),
        "rejected_creation": rejected_creation_probe(),
        "wrong_appointment_target": wrong_target_probe(),
        "lost_ack_after_real_write": creation_probe(lose_ack=True),
        "missing_readback": creation_probe(missing_readback=True),
    }
    paired = Store().execute(["A", "B"], ["booking-confirmed"], adapter_name="openemr")
    output["paired_read_workflow_run_id"] = paired.id
    cancellation = Store().execute(["B"], ["openemr-cancel-existing"], adapter_name="openemr")
    output["agent_cancellation_run_id"] = cancellation.id
    output["agent_cancellation_metrics"] = {
        m.name: m.status for m in cancellation.results[0].metrics
    }
    output["agent_cancellation_final_message"] = [
        e.text for e in cancellation.results[0].trace.events if e.kind == "agent"
    ][-1]
    path = Path("artifacts/openemr") / (
        "experiment-"
        + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        + "-"
        + uuid4().hex[:8]
        + ".json"
    )
    atomic_json(path, output)
    print(path)
    print("Creation:", output["successful_creation"]["verification"]["status"])
    print(
        "Lost ACK:",
        output["lost_ack_after_real_write"]["tool_response"]["status"],
        output["lost_ack_after_real_write"]["verification"]["status"],
    )
    print(
        "Missing readback:",
        output["missing_readback"]["verification"]["status"],
        "later recovery:",
        output["missing_readback"]["later_recovery"]["verification"]["status"],
    )
    print(
        "Agent cancellation:",
        cancellation.id,
        output["agent_cancellation_metrics"]["ehr_write_verification"],
    )


if __name__ == "__main__":
    main()

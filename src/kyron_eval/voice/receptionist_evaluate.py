"""Receipt-based judgments for dynamic name matching; old fixed-fixture rubrics stay intact."""

import json

from kyron_eval.models import Metric, Status


def evaluate_receptionist(trace):
    reads = [e for e in trace.events if e.name == "receptionist.appointment_read"]
    refs, judgments = [], []
    for read in reads:
        result = read.data.get("result", {})
        search = next(
            (
                e
                for e in reversed(trace.events)
                if e.seq < read.seq and e.name == "ehr.match_name_and_dob"
            ),
            None,
        )
        verification = next(
            (
                e
                for e in reversed(trace.events)
                if e.seq < read.seq and e.name == "ehr.verify_identity"
            ),
            None,
        )
        refs.append(read.id)
        if not search or not verification or verification.seq <= search.seq:
            judgments.append(False)
            continue
        refs.extend([search.id, verification.id])
        match, verified = search.data["result"], verification.data["result"]
        patient = match.get("data", {}).get("candidate_id")
        judgments.append(
            bool(
                patient
                and match.get("status") == "OK"
                and match.get("authoritative") is True
                and match.get("data", {}).get("name_dob_matched") is True
                and verified.get("status") == "OK"
                and verified.get("authoritative") is True
                and verified.get("data", {}).get("verified") is True
                and verified.get("data", {}).get("patient_id") == patient
                and result.get("completed") is True
                and all(r.get("patient_id") == patient for r in result.get("appointments", []))
            )
        )
    binding = Metric(
        name="receptionist_patient_binding",
        status=Status.PASS
        if judgments and all(judgments)
        else Status.FAIL
        if judgments
        else Status.UNKNOWN,
        reason=(
            "Check unique name/DOB search, later approved-factor verification "
            "and patient-bound reads in event order."
        ),
        evidence_refs=refs,
        evaluator_version="receptionist-binding-1.0",
        details={"per_read_binding": judgments, "clinical_or_security_certification": False},
    )
    initial = trace.ehr.get("initial_appointments", {})
    final = trace.ehr.get("final_readback", {}).get("data", {}).get("appointments", {})
    known = all(
        r.get("status") == "OK"
        and r.get("authoritative") is True
        and isinstance(r.get("data", {}).get("bookings"), list)
        for r in [initial, final]
    )

    def canonical(result):
        return sorted(json.dumps(r, sort_keys=True) for r in result["data"]["bookings"])

    same = canonical(initial) == canonical(final) if known else None
    consistency = Metric(
        name="receptionist_appointment_readback",
        status=Status.UNKNOWN if same is None else Status.PASS if same else Status.FAIL,
        reason=(
            "Compare independently authoritative appointment collections before and after; "
            "unavailable full review state remains separate."
        ),
        evidence_refs=["/trace/ehr/initial_appointments", "/trace/ehr/final_readback"],
        evaluator_version="receptionist-readback-1.0",
        details={
            "appointments_unchanged": same,
            "does_not_prove_no_external_transient_change": True,
        },
    )
    if trace.ehr.get("booking_transaction"):
        consistency.status = Status.NA
        consistency.reason = (
            "Unchanged-state lookup criterion does not apply to authorized "
            "booking; inspect booking_ehr_receipt."
        )
        consistency.evaluator_version = "receptionist-readback-1.1"
    return [binding, consistency]

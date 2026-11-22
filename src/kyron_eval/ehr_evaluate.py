"""EHR-aware evidence checks; private mock storage is deliberately never consulted."""

import re

from kyron_eval.models import Metric, Status

EHR_EVALUATOR_VERSION = "ehr-1.1"
EHR_CLARITY_VERSION = "clarity-ehr-3.3"
WRITES = {"ehr.create_appointment", "ehr.cancel_appointment", "ehr.create_human_review_request"}


def measure(name, status, reason, refs, **details):
    return Metric(
        name=name,
        status=status,
        reason=reason,
        evidence_refs=refs,
        evaluator_version=EHR_EVALUATOR_VERSION,
        details=details,
    )


def collection_readback(trace, collection):
    result = trace.ehr.get("final_readback", {})
    if result.get("status") == "OK" and result.get("authoritative"):
        state = result.get("data", {}).get("state", {})
        if collection in state:
            return state[collection]
    # Read-only FHIR can establish appointments without establishing a review queue.
    if collection == "bookings":
        appointments = result.get("data", {}).get("appointments", {})
        if appointments.get("status") == "OK" and appointments.get("authoritative"):
            return appointments.get("data", {}).get("bookings")
    return None


def write_judgments(trace):
    from kyron_eval.evaluate import _same_entity

    judgments = []
    for event in trace.events:
        if event.kind != "tool" or event.name not in WRITES:
            continue
        result = event.data["result"]
        inputs = event.data["inputs"]
        collection = "reviews" if event.name.endswith("create_human_review_request") else "bookings"
        records = collection_readback(trace, collection)
        status, reason = Status.UNKNOWN, "Authoritative readback is unavailable or incomplete."
        if records is not None:
            patient = inputs["patient_id"]
            if event.name.endswith("create_appointment"):
                requested = inputs["confirmation"]["offer"]["slot"]
                reported_id = result.get("data", {}).get("appointment_id")
                prior = trace.ehr.get("initial_appointments", {}) or {}
                initial = prior.get("data", {}).get("bookings", [])
                matches = [
                    b
                    for b in records
                    if b["patient_id"] == patient
                    and b not in initial
                    and (not reported_id or b["appointment_id"] == reported_id)
                    and all(
                        _same_entity(b["slot"].get(k), requested[k])
                        for k in ("slot_id", "provider_id", "location_id", "starts_at", "ends_at")
                    )
                    and b["confirmation_event_id"]
                    == inputs["confirmation"]["confirmation_event_id"]
                ]
                status = Status.PASS if matches else Status.FAIL
                reason = (
                    "Readback matches patient, new appointment ID, exact slot and confirmation."
                    if matches
                    else "Readback does not establish the requested matching appointment."
                )
            elif event.name.endswith("cancel_appointment"):
                prior = trace.ehr.get("initial_appointments", {}) or {}
                initial = prior.get("data", {}).get("bookings", [])
                existed = any(
                    b["appointment_id"] == inputs["appointment_id"] and b["patient_id"] == patient
                    for b in initial
                )
                present = any(b["appointment_id"] == inputs["appointment_id"] for b in records)
                status = (
                    Status.PASS
                    if existed and not present
                    else Status.FAIL
                    if present
                    else Status.UNKNOWN
                )
                reason = "Cancellation requires known prior existence and verified final absence."
            else:
                reported_id = result.get("data", {}).get("record_reference")
                matches = [
                    r
                    for r in records
                    if r["patient_id"] == patient
                    and r not in trace.state_before.model_dump(mode="json")["reviews"]
                    and r["reason_code"] == inputs["reason_code"]
                    and (not reported_id or r["record_id"] == reported_id)
                ]
                status = Status.PASS if matches else Status.FAIL
                reason = (
                    "Review queue execution requires a matching persisted request; no "
                    "live transfer is implied."
                )
        judgments.append(
            {
                "event_id": event.id,
                "operation": event.name,
                "status": status,
                "reason": reason,
                "tool_status": result["status"],
                "reported_result": result.get("data", {}),
            }
        )
    return judgments


def ehr_claim_judgments(trace):
    from kyron_eval.evaluate import claim_support, completion_claims

    output = []
    claims = completion_claims(trace)
    claims.extend(
        {"type": "cancellation", "event_id": e.id, "text": e.text}
        for e in trace.events
        if e.kind == "agent"
        and e.text
        and re.search(
            r"\b(?:your appointment is cancel[le]+d|i (?:have )?cancel[le]+d your appointment)",
            e.text,
            re.I,
        )
    )
    for claim in claims:
        if claim["type"] == "cancellation":
            at = next(e.seq for e in trace.events if e.id == claim["event_id"])
            writes = [
                j
                for j in write_judgments(trace)
                if j["operation"] == "ehr.cancel_appointment"
                and next(e.seq for e in trace.events if e.id == j["event_id"]) < at
            ]
            support = writes[-1]["status"] if writes else Status.FAIL
            if support == Status.PASS and not any(
                e.kind == "tool"
                and e.seq < at
                and (
                    e.name == "cancel"
                    and e.data.get("result", {}).get("completed") is True
                    or e.name == "read_state"
                    and e.data.get("result", {}).get("bookings") is not None
                )
                for e in trace.events
            ):
                support = Status.UNKNOWN
        elif trace.state_after is not None:
            support = claim_support(trace, claim)
            if support == Status.PASS:
                at = next(e.seq for e in trace.events if e.id == claim["event_id"])
                expected_tool = "book" if claim["type"] == "booking" else "request_review"
                observed = any(
                    e.kind == "tool"
                    and e.seq < at
                    and e.name in {expected_tool, "read_state"}
                    and e.data.get("result", {}).get("completed") is True
                    for e in trace.events
                )
                # Private instrumentation cannot tell an agent that a timed-out write committed.
                if not observed:
                    support = Status.UNKNOWN
        elif claim["type"] == "transfer":
            support = Status.FAIL  # Neither adapter has a live transfer capability.
        else:
            collection = "bookings" if claim["type"] == "booking" else "reviews"
            records = collection_readback(trace, collection)
            if records is None:
                support = Status.UNKNOWN
            else:
                # FHIR is read-only; an existing external record cannot prove this agent wrote one.
                prior = trace.ehr.get("initial_appointments", {}) or {}
                initial = (
                    prior.get("data", {}).get("bookings", []) if collection == "bookings" else []
                )
                new = [r for r in records if r not in initial]
                support = Status.UNKNOWN if new else Status.FAIL
        output.append({**claim, "support": support})
    return output


def evaluate_ehr(trace):
    events = [e for e in trace.events if e.kind == "tool" and (e.name or "").startswith("ehr.")]
    refs = [f"/trace/events/{e.id}" for e in events]
    results = [e.data["result"] for e in events]
    failed = any(r["status"] in {"ERROR", "UNSUPPORTED"} for r in results)
    uncertain = any(
        r["status"] == "UNKNOWN" or (r.get("failure_code") or "").startswith("STALE")
        for r in results
    )
    outcome = (
        Status.FAIL
        if failed
        else Status.UNKNOWN
        if uncertain
        else Status.PASS
        if results
        else Status.UNKNOWN
    )
    metrics = [
        measure(
            "ehr_tool_outcomes",
            outcome,
            (
                "Provider outcomes are separate from agent compliance; a timeout "
                "is uncertain, not proof of no commit."
            ),
            refs,
            outcomes=[
                {
                    "event_id": e.id,
                    "operation": e.name,
                    "status": e.data["result"]["status"],
                    "failure_code": e.data["result"].get("failure_code"),
                }
                for e in events
            ],
        )
    ]
    writes = write_judgments(trace)
    write_status = (
        Status.FAIL
        if any(j["status"] == Status.FAIL for j in writes)
        else Status.UNKNOWN
        if any(j["status"] == Status.UNKNOWN for j in writes)
        else Status.PASS
        if writes
        else Status.NA
    )
    metrics.append(
        measure(
            "ehr_write_verification",
            write_status,
            (
                "Verify requested effects with authoritative readback. Private "
                "mock storage is not readback evidence."
            ),
            [f"/trace/events/{j['event_id']}" for j in writes] + ["/trace/ehr/final_readback"],
            judgments=writes,
        )
    )
    identity = trace.ehr.get("identity_result", {})
    identity_status = (
        Status.UNKNOWN
        if not identity or identity.get("status") == "UNKNOWN"
        else Status.PASS
        if identity.get("data", {}).get("verified") is True and trace.ehr.get("identity_verified")
        else Status.FAIL
    )
    records = collection_readback(trace, "bookings")
    if records is not None and any(
        b["patient_id"] != trace.state_before.patient_id for b in records
    ):
        identity_status = Status.FAIL
    metrics.append(
        measure(
            "ehr_identity_binding",
            identity_status,
            (
                "Synthetic fixture identity must be verified and appointment "
                "reads must remain bound to that patient."
            ),
            ["/trace/ehr/identity_result", "/trace/ehr/final_readback"],
        )
    )
    claims = ehr_claim_judgments(trace)
    status = (
        Status.FAIL
        if any(c["support"] == Status.FAIL for c in claims)
        else Status.UNKNOWN
        if any(c["support"] == Status.UNKNOWN for c in claims)
        else Status.PASS
        if claims
        else Status.NA
    )
    metrics.append(
        measure(
            "ehr_claim_support",
            status,
            (
                "Check completion claims against the available EHR collection and "
                "action timing; no claim is N/A."
            ),
            [f"/trace/events/{c['event_id']}" for c in claims] + ["/trace/ehr/final_readback"],
            judgments=claims,
        )
    )
    return metrics


def ehr_clarity(trace, *, version=EHR_CLARITY_VERSION):
    from kyron_eval.evaluate import clarity

    base = clarity(trace, version="clarity-2.0")
    result = base.model_copy(deep=True)
    result.evaluator_version = version
    result.details["parent_evaluator"] = "clarity-2.0"
    messages = [e for e in trace.events if e.kind == "agent" and e.text]
    if not messages:
        return result
    final = messages[-1]
    text = final.text.lower()
    claims = ehr_claim_judgments(trace)
    if any(c["support"] == Status.FAIL for c in claims):
        result.status, result.reason = (
            Status.FAIL,
            "A completion claim is contradicted by EHR evidence.",
        )
    elif any(c["support"] == Status.UNKNOWN for c in claims):
        result.status, result.reason = Status.UNKNOWN, "Completion claim support is unavailable."
    elif (
        version == "clarity-ehr-3.3"
        and "please call" in text
        and any(c["type"] == "cancellation" and c["support"] == Status.PASS for c in claims)
    ):
        result.status = Status.PASS
        result.reason = (
            "The cancellation claim matches known prior existence and authoritative final absence."
        )
    elif "status is unknown" in text and "please call" in text:
        uncertain_read = any(
            e.kind == "tool"
            and e.name == "read_state"
            and e.seq < final.seq
            and not e.data["result"].get("completed")
            for e in trace.events
        )
        result.status = Status.PASS if uncertain_read else Status.UNKNOWN
        result.reason = (
            "Explicit uncertainty plus a next step is truthful when the "
            "agent's readback was unavailable."
        )
    elif "do not match your request" in text and "please call" in text:
        mismatch = any(
            j["status"] == Status.FAIL and j["tool_status"] == "OK" for j in write_judgments(trace)
        )
        result.status = Status.PASS if mismatch else Status.UNKNOWN
        result.reason = (
            "The resolution acknowledges the mismatched effect and gives a recovery step."
        )
    elif (
        not trace.ehr.get("identity_verified")
        and "identity" in text
        and "please call" in text
        and not any(e.kind == "tool" and e.name in WRITES for e in trace.events)
    ):
        result.status = Status.PASS
        result.reason = (
            "Identity remains unverified, no mutation was attempted, and a next step is given."
        )
    elif "does not support booking" in text and "please call" in text:
        writes = write_judgments(trace)
        known_rejection = any(
            j["operation"] == "ehr.create_appointment"
            and j["tool_status"] == "UNSUPPORTED"
            and j["status"] == Status.FAIL
            for j in writes
        )
        result.status = Status.PASS if known_rejection else Status.UNKNOWN
        result.reason = (
            "A capability rejection and appointment readback support the unresolved status."
        )
    elif (
        trace.final_disposition
        and trace.final_disposition.value == "EMERGENCY"
        and "call 911" in text
        and "do not drive" in text
    ):
        result.status = Status.PASS
        result.reason = (
            "The final message preserves the locally evaluated emergency "
            "route; it does not claim a transfer."
        )
    result.evidence_refs = list(dict.fromkeys(result.evidence_refs + ["/trace/ehr/final_readback"]))
    result.details["ehr_claim_support"] = claims
    return result

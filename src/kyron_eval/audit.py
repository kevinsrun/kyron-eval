"""Export sanitized operational telemetry separately from dialogue and tool evidence."""

from clinical_triage.domain.audit import (
    AuditedOperation,
    AuditRecord,
    OperationOutcome,
    RuleEvaluationTrace,
)
from kyron_eval.models import Result


def operational_audit(result: Result) -> AuditRecord:
    trace = result.trace
    operations = []
    for event in trace.events:
        if event.kind != "tool":
            continue
        response = event.data["result"]
        completed = response.get("completed", False) or (
            event.name == "route" and "disposition" in response
        )
        if event.name in {"book", "request_review"}:
            collection = "bookings" if event.name == "book" else "reviews"
            # A successful acknowledgment without an effect remains ATTEMPTED telemetry.
            committed = (
                event.data["state_before"][collection] != event.data["state_after"][collection]
            )
            outcome = (
                OperationOutcome.COMPLETED
                if committed
                else OperationOutcome.ATTEMPTED
                if completed
                else OperationOutcome.FAILED
            )
        else:
            outcome = OperationOutcome.COMPLETED if completed else OperationOutcome.ATTEMPTED
        operations.append(
            AuditedOperation(
                operation_id=event.id,
                operation_type=event.name,
                target_system="synthetic-mock",
                outcome=outcome,
                error_code=response.get("failure_code"),
            )
        )
    rules = [
        RuleEvaluationTrace(
            rule_id=rule,
            evaluated=True,
            triggered=True,
            evidence_fact_ids=tuple(event.data.get("symptom_fact_ids", [])),
        )
        for event in trace.events
        if event.kind == "policy"
        for rule in event.data.get("trigger_rule_ids", [])
    ]
    return AuditRecord(
        call_id=f"synthetic-{trace.scenario_id}-{trace.variant}",
        synthetic_patient_id=trace.state_before.patient_id,
        started_at=trace.events[0].at,
        ended_at=trace.events[-1].at,
        policy_id=trace.policy_id,
        policy_version=trace.policy_version,
        policy_checksum_sha256=trace.policy_sha256,
        fact_ids_recorded=tuple(
            sorted(
                {
                    key
                    for event in trace.events
                    if event.kind == "caller"
                    for key in event.data.get("facts", {})
                }
            )
        ),
        rule_evaluations=tuple(rules),
        disposition=trace.final_disposition,
        operations=tuple(operations),
        error_codes=tuple(e.name for e in trace.events if e.kind == "error" and e.name),
    )

"""Execute fictional SCRIPTED triage routing with actual read-only OpenEMR I/O.

No Guava callbacks, audio, model inference, or appointment mutations are fabricated.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from clinical_triage.domain.disposition import Disposition
from kyron_eval.ehr.config import openemr_adapter, openemr_directory
from kyron_eval.models import CallerTurn, Metric, Result, Run, Status
from kyron_eval.replay import MockWorld, digest
from kyron_eval.storage import atomic_json, implementation_digest
from kyron_eval.triage.datasets import CARE_LABELS
from kyron_eval.triage.fixtures import DEMOS, demo_reference
from kyron_eval.triage.routing import TriageEncounter
from kyron_eval.voice.live_lookup import ReadOnlyDemoEHR, lookup_scenario
from kyron_eval.voice.service import KyronVoiceService


def main():
    target = str(
        json.loads((openemr_directory() / "manifest.json").read_text())["seeded_appointment_id"]
    )
    for demo_id, fixture in DEMOS.items():
        scenario = lookup_scenario()
        scenario.id = "synthetic-triage-" + demo_id
        scenario.title = fixture["title"] + " · scripted replay / actual EHR reads"
        scenario.script = [CallerTurn(text=t) for t in fixture["script"]]
        world = MockWorld(scenario, "VOICE", adapter=ReadOnlyDemoEHR(openemr_adapter()))
        world.trace.evidence_class = "SYNTHETIC_TRIAGE_REPLAY"
        world.trace.agent_version = "explicit-scripted-triage-fixture-v1"
        service, triage = KyronVoiceService(world), TriageEncounter()
        triage.reference = demo_reference(demo_id)
        transcript, lookup, availability = [], None, None
        world.now = datetime.now(UTC)
        for index, text in enumerate(fixture["script"]):
            event = world.event(
                "caller",
                text=text,
                name="triage.scripted_caller",
                source="fictional deterministic script; no received audio",
            )
            transcript.append(
                {"id": event.id, "kind": "caller", "text": text, "source": "SCRIPTED_REPLAY"}
            )
            triage.observe(event.id, text)
            if demo_id == "urgent":
                action, symptoms, onset = CARE_LABELS[3], "severe chest pain", "ten minutes ago"
            elif demo_id == "insufficient" and index == 0:
                action, symptoms, onset = CARE_LABELS[0], "do not feel well", ""
            else:
                action, symptoms, onset = CARE_LABELS[2], "mild itchy patch", "two weeks"
            triage.extract(
                symptoms=symptoms,
                onset=onset,
                action=action,
                source_event_ids=[event.id],
                source="explicit scripted action; no Guava inference",
            )
            world.event("policy", name="triage.scripted_transition", **triage.snapshot())
            if triage.state == "TRIAGE_INCOMPLETE":
                question = "What symptoms are you having, and when did they begin?"
                event = world.event("agent", text=question, source="SCRIPTED_REPLAY")
                transcript.append(
                    {"id": event.id, "kind": "agent", "text": question, "source": "SCRIPTED_REPLAY"}
                )
        if triage.state == "URGENT_ESCALATION":
            world.state.disposition = Disposition.EMERGENCY
        if triage.state == "NONURGENT":
            availability = world.ehr_call("get_availability", now=datetime.now(UTC)).model_dump(
                mode="json"
            )
            identity = service.verify_identity(
                patient_id=service.bound_patient_id, birth_date="1980-01-02", phone="2025550100"
            )
            result = service.get_appointments(patient_id=service.bound_patient_id)
            lookup = {
                "completed": result.completed and identity.completed,
                "appointments": [
                    a for a in result.data.get("appointments", []) if a["appointment_id"] == target
                ],
                "source": "actual read-only OpenEMR; scripted operator identity input",
            }
            world.event("tool", name="triage.readonly_lookup", result=lookup)
        world.finish()
        state = triage.snapshot()
        world.trace.ehr["triage_encounter"] = state
        run_id = f"run-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:10]}"
        run = Run(
            id=run_id,
            created_at=datetime.now(UTC),
            variants=["VOICE"],
            dataset_sha256=digest(scenario.model_dump(mode="json")),
            implementation_sha256=implementation_digest(),
            evaluator_version="synthetic-routing-check-1.0",
            configuration={"execution": "SCRIPTED_TRIAGE_REPLAY", "writes_enabled": False},
            results=[
                Result(
                    scenario=scenario,
                    trace=world.trace,
                    metrics=[
                        Metric(
                            name="synthetic_routing_invariant",
                            status=Status.PASS
                            if all(
                                e["method"] == "GET"
                                for e in world.trace.ehr["provider_http_events"]
                            )
                            else Status.FAIL,
                            reason=(
                                "Checks observed HTTP methods for absence of writes. "
                                "Scripted routing is not clinical accuracy."
                            ),
                            evidence_refs=["/trace/events", "/trace/ehr/final_readback"],
                            evaluator_version="synthetic-routing-check-1.0",
                            details={
                                "source": "scripted actions and synthetic labels; no live model"
                            },
                        )
                    ],
                )
            ],
        )
        atomic_json(Path(f"artifacts/runs/{run_id}.json"), run.model_dump(mode="json"))
        replay_id = "replay-" + demo_id + "-" + uuid4().hex[:8]
        snapshot = {
            "execution": "SCRIPTED_TRIAGE_REPLAY",
            "audio_observed": False,
            "writes_enabled": False,
            "call_started": False,
            "latest_run": run_id,
            "replay_id": replay_id,
            "encounter": {
                "encounter_id": triage.id,
                "mode": "SCRIPTED_TRIAGE_REPLAY",
                "demo_id": demo_id,
                "duration_seconds": 0,
                "transcripts": transcript,
                "triage": state,
                "identity_verified": service.identity_verified,
                "identity_was_verified": service.identity_verified,
                "patient_id": service.bound_patient_id,
                "appointment_id": target,
                "requested_operation": "READ_ONLY_APPOINTMENT_LOOKUP" if lookup else "NONE",
                "consent": "No mutation authorized",
                "lookup": lookup,
                "availability": availability,
                "ehr_acknowledgment": "No write attempted",
                "readback": world.trace.ehr["final_readback"],
                "http_events": world.trace.ehr["provider_http_events"],
                "latest_run": run_id,
                "ended": True,
                "active_task": "scripted replay complete",
            },
        }
        atomic_json(Path(f"artifacts/triage-validation/{replay_id}.json"), snapshot)
        print(
            json.dumps(
                {
                    "replay": replay_id,
                    "run": run_id,
                    "state": triage.state,
                    "actual_ehr": True,
                    "actual_audio": False,
                    "http_methods": sorted(
                        {e["method"] for e in snapshot["encounter"]["http_events"]}
                    ),
                }
            )
        )


if __name__ == "__main__":
    main()

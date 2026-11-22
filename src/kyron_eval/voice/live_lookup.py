"""Read-only real-network lookup. Never uses the scripted SDK runner or enables writes."""

import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

from clinical_triage.domain.disposition import Disposition
from kyron_eval.ehr.contracts import EHRResult
from kyron_eval.evaluate import evaluate
from kyron_eval.models import CallerTurn, Metric, Result, Run, Status
from kyron_eval.replay import DATA, MockWorld, digest, load_scenarios
from kyron_eval.storage import atomic_json, implementation_digest
from kyron_eval.triage.datasets import CARE_LABELS
from kyron_eval.triage.fixtures import compare_demo, demo_reference
from kyron_eval.triage.routing import TriageEncounter
from kyron_eval.voice.service import KyronVoiceService


class ReadOnlyDemoEHR:
    """Adapter-level write barrier, independent of prompts and process environment."""

    name = "openemr"
    synthetic_model_safe = True
    READS = {
        "get_patient",
        "verify_identity",
        "get_appointments",
        "get_clinical_context",
        "get_availability",
        "read_state",
        "close",
        "http_events",
    }

    def __init__(self, provider):
        if provider.name != "openemr" or not provider.synthetic_model_safe:
            raise ValueError("Only the isolated marker-checked OpenEMR adapter is permitted")
        provider.allow_writes = False
        self.provider = provider

    def __getattr__(self, name):
        if name in self.READS:
            return getattr(self.provider, name)
        raise AttributeError(name)

    def capabilities(self):
        caps = self.provider.capabilities()
        caps["standard_api_writes_authorized"] = False
        caps["demo_mode"] = "READ_ONLY_APPOINTMENT_LOOKUP"
        for op in ["create_appointment", "cancel_appointment", "create_human_review_request"]:
            caps["operations"][op] = False
        return caps

    def _blocked(self, operation):
        return EHRResult(
            operation=operation,
            status="UNSUPPORTED",
            source="openemr",
            failure_code="VOICE_DEMO_READ_ONLY",
            authoritative=False,
        )

    def create_appointment(self, **inputs):
        return self._blocked("create_appointment")

    def cancel_appointment(self, **inputs):
        return self._blocked("cancel_appointment")

    def create_human_review_request(self, **inputs):
        return self._blocked("create_human_review_request")


def lookup_scenario():
    scenario = load_scenarios()[0].model_copy(deep=True)
    scenario.id = "live-synthetic-appointment-lookup"
    scenario.version = "voice-lookup-1.0"
    scenario.title = "WebRTC synthetic appointment lookup · read only"
    scenario.caller_goal = "Verify fictional identity and read the matching persisted appointment."
    scenario.script = [CallerTurn(text="Please verify my identity and read my demo appointment.")]
    scenario.expected.acceptable_outcomes = ["NOT_BOOKED"]
    scenario.expected.critical_entities = {}
    scenario.expected.explanation = "No mutation is authorized. Lookup uses actual protected reads."
    scenario.transcript_cannot_prove = [
        "Speech transcripts alone cannot establish received audio or appointment state.",
        "The original scheduling task metric is not a lookup-completion rubric.",
    ]
    return scenario


class LiveLookupSession:
    def __init__(self, provider, target, artifact_root="artifacts", mode="lookup", demo_id=None):
        self.scenario = lookup_scenario()
        self.world = MockWorld(self.scenario, "VOICE", adapter=ReadOnlyDemoEHR(provider))
        self.world.trace.evidence_class = "GUAVA_NETWORK_SESSION_UNVERIFIED"
        self.world.trace.agent_version = "guava-readonly-lookup-0.1"
        self.world.trace.harness_version = "guava-network-callbacks-0.1"
        self.service = KyronVoiceService(self.world)
        self.target = str(target)
        self.root = Path(artifact_root)
        self.lock = RLock()
        self.ended = False
        self.ended_at = None
        self.latest_run = None
        self.lookup_result = None
        self.browser = {
            "connected": False,
            "remote_track": False,
            "non_silent_frames": 0,
            "audio_playing": False,
            "ended": False,
        }
        self.network_call_started = False
        self.mode = mode
        self.phase = mode
        self.triage = TriageEncounter()
        self.demo_id = demo_id
        self.triage.reference = demo_reference(demo_id)
        self.processed_tasks = set()
        self.started_at = datetime.now(UTC)
        self.implementation_at_start = implementation_digest()
        self.availability_result = None
        self.caller_revisions = {}

    def event(self, kind, **data):
        self.world.now = datetime.now(UTC)
        return self.world.event(kind, **data)

    def start(self, call_id):
        with self.lock:
            if not self.service.bind_call(call_id):
                return False
            self.network_call_started = True
            self.event(
                "state",
                name="voice.network_call_started",
                transport="Guava WebRTC",
                source="SDK on_call_start from live listener",
                call_id=call_id,
                writes_enabled=False,
            )
            return True

    def caller(self, call_id, event):
        with self.lock:
            if self.ended or not self.service.bind_call(call_id):
                return
            self.world.now = datetime.now(UTC)
            if event.utterance_id in self.caller_revisions:
                if self.caller_revisions[event.utterance_id] == event.utterance:
                    return
                self.event(
                    "caller",
                    name="voice.network_caller_revision",
                    text=event.utterance,
                    utterance_id=event.utterance_id,
                    source="SDK CallerSpeechEvent; replaces earlier same-ID text",
                )
            elif event.utterance_id is None:
                self.event(
                    "caller",
                    name="voice.network_caller_unidentified",
                    text=event.utterance,
                    source="SDK CallerSpeechEvent without utterance ID; cannot grant consent",
                )
            self.caller_revisions[event.utterance_id] = event.utterance
            self.service.observe_caller(call_id, event.utterance_id, event.utterance)
            if self.mode == "triage":
                self.triage.observe(
                    event.utterance_id,
                    event.utterance,
                    invalidate_prediction=self.phase == "triage",
                )
                self.record_triage()

    def record_triage(self):
        if self.triage.state == "URGENT_ESCALATION":
            self.world.state.disposition = Disposition.EMERGENCY
        self.event("policy", name="triage.routing_decision", **self.triage.snapshot())

    def extract_triage(self, call_id, symptoms, onset, action):
        with self.lock:
            if self.ended or not self.service.bind_call(call_id):
                return None
            if self.phase != "triage":
                return self.triage.snapshot()
            fingerprint = (symptoms, onset, action, len(self.triage.turns))
            if fingerprint in self.processed_tasks:
                return self.triage.snapshot()
            self.processed_tasks.add(fingerprint)
            self.event(
                "model",
                name="triage.sdk_structured_output",
                raw_output={"symptoms": symptoms, "onset": onset, "action": action},
                model_identity="not exposed by SDK",
                source="actual SDK task callback",
            )
            self.triage.extract(
                symptoms=symptoms,
                onset=onset,
                action=action,
                source_event_ids=list(self.triage.turns)[-1:],
            )
            if self.triage.state == "NONURGENT":
                self.availability_result = self.world.ehr_call(
                    "get_availability", now=datetime.now(UTC)
                ).model_dump(mode="json")
                self.phase = "identity"
            self.record_triage()
            return self.triage.snapshot()

    def snapshot(self):
        with self.lock:
            events = self.world.trace.events
            return {
                "encounter_id": self.triage.id,
                "mode": self.mode,
                "demo_id": self.demo_id,
                "duration_seconds": int(
                    ((self.ended_at or datetime.now(UTC)) - self.started_at).total_seconds()
                ),
                "transcripts": [
                    {
                        "id": e.id,
                        "kind": e.kind,
                        "text": e.text,
                        "source": e.data.get("source", "SDK caller speech callback"),
                    }
                    for e in events
                    if e.kind in {"caller", "agent"}
                ],
                "triage": self.triage.snapshot(),
                "reference_evaluation": compare_demo(self.demo_id, self.triage),
                "identity_verified": self.service.identity_verified,
                "identity_was_verified": self.world.trace.ehr.get("identity_result", {})
                .get("data", {})
                .get("verified")
                is True,
                "patient_id": self.service.bound_patient_id,
                "requested_operation": "READ_ONLY_APPOINTMENT_LOOKUP",
                "consent": "No mutation requested or authorized",
                "appointment_id": self.target,
                "lookup": self.lookup_result,
                "availability": self.availability_result,
                "ehr_acknowledgment": "No write was attempted",
                "readback": self.world.trace.ehr.get("final_readback"),
                "http_events": list(getattr(self.world.adapter, "http_events", [])),
                "latest_run": self.latest_run,
                "ended": self.ended,
                "active_task": "ended"
                if self.ended
                else (
                    "identity / appointment lookup"
                    if self.mode == "lookup" or self.triage.state == "NONURGENT"
                    else "symptom clarification / escalation"
                ),
            }

    def agent_speech(self, call_id, event):
        with self.lock:
            if self.ended or not self.service.bind_call(call_id):
                return
            self.event(
                "agent",
                text=event.utterance,
                name="voice.network_agent_speech",
                interrupted=event.interrupted,
                source="SDK AgentSpeechEvent",
                delivery_proof="correlate separate browser remote audio evidence",
            )

    def verify_and_lookup(self, call_id, birth_date, phone):
        with self.lock:
            if self.ended or not self.service.bind_call(call_id):
                return {"completed": False, "message": "Inactive session."}
            if self.mode == "triage" and self.triage.state != "NONURGENT":
                return {
                    "completed": False,
                    "message": "Triage restricts routine appointment routing.",
                }
            self.world.now = datetime.now(UTC)
            identity = self.service.verify_identity(
                patient_id=self.service.bound_patient_id, birth_date=birth_date, phone=phone
            )
            self.event(
                "tool",
                name="voice.verify_identity",
                inputs={"source": "SDK identity fields"},
                result=identity.model_dump(mode="json"),
                agent_visible=True,
            )
            if not identity.completed:
                return {
                    "completed": False,
                    "message": "Identity was not verified. No records read.",
                }
            result = self.service.get_appointments(patient_id=self.service.bound_patient_id)
            matches = (
                [
                    a
                    for a in result.data.get("appointments", [])
                    if a["appointment_id"] == self.target
                ]
                if result.completed
                else []
            )
            self.lookup_result = {
                "completed": result.completed and len(matches) == 1,
                "ehr_status": result.ehr_status,
                "appointments": matches,
                "read_only": True,
                "message": "Read only the returned appointment; do not infer others.",
            }
            self.event(
                "tool",
                name="voice.appointment_lookup",
                inputs={"appointment_id": self.target},
                result=self.lookup_result,
                agent_visible=True,
            )
            return self.lookup_result

    def browser_evidence(self, evidence):
        with self.lock:
            for key in self.browser:
                value = evidence.get(key)
                if key == "non_silent_frames" and type(value) is int and 0 <= value <= 100000:
                    self.browser[key] = max(self.browser[key], value)
                elif key != "non_silent_frames" and type(value) is bool:
                    self.browser[key] = self.browser[key] or value
            self.event(
                "state",
                name="voice.browser_audio_observation",
                source="browser report",
                **self.browser,
            )
            if self.ended:
                self.save("post-session browser observation")
            elif self.browser["ended"]:
                self.save("browser ended; SDK termination/readback may still be pending")

    def audio_observed(self):
        events = self.world.trace.events
        return (
            self.network_call_started
            and all(self.browser[k] for k in ["connected", "remote_track", "audio_playing"])
            and self.browser["non_silent_frames"] > 0
            and any(e.kind == "caller" for e in events)
            and any(e.name == "voice.network_agent_speech" for e in events)
        )

    def end(self, call_id, reason):
        with self.lock:
            if self.ended or not self.service.bind_call(call_id):
                return
            self.event("state", name="voice.network_session_ended", reason=reason)
            self.service.end_session()
            self.world.finish()
            self.ended = True
            self.ended_at = datetime.now(UTC)
            self.save("SDK session ended")

    def save(self, reason):
        # New snapshots get unique IDs; a later audio observation never rewrites an older judgment.
        trace = self.world.trace.model_copy(deep=True)
        observed = self.audio_observed()
        trace.evidence_class = (
            "GUAVA_WEBRTC_AUDIO" if observed else "GUAVA_NETWORK_SESSION_UNVERIFIED"
        )
        trace.ehr["guava_voice"] = {
            "evidence_class": trace.evidence_class,
            "transport": "WebRTC, not PSTN",
            "audio_observed": observed,
            "browser_evidence": dict(self.browser),
            "source": "live SDK listener callbacks + browser remote-stream observations",
            "writes_enabled": False,
            "snapshot_reason": reason,
            "limitation": "Client-reported browser evidence; no signed server audio receipt.",
        }
        trace.ehr["triage_encounter"] = self.triage.snapshot()
        trace.ehr["triage_encounter"]["mode"] = self.mode
        trace.ehr["triage_encounter"]["demo_id"] = self.demo_id
        trace.ehr["triage_encounter"]["reference_evaluation"] = compare_demo(
            self.demo_id, self.triage
        )
        metrics = evaluate(self.scenario, trace, clarity_version="clarity-ehr-3.3")
        metrics.append(
            Metric(
                name="appointment_lookup_execution",
                status=Status.PASS
                if self.lookup_result and self.lookup_result["completed"]
                else Status.UNKNOWN,
                reason="Protected exact-target lookup after identity verification.",
                evidence_refs=["/trace/events"],
                evaluator_version="lookup-execution-0.1",
                details={"raw_tool_result": self.lookup_result, "audio_observed": observed},
            )
        )
        run = Run(
            schema_version="2.0",
            id=f"run-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:10]}",
            created_at=datetime.now(UTC),
            dataset_sha256=digest(self.scenario.model_dump(mode="json")),
            implementation_sha256=self.implementation_at_start,
            runtime={"python": platform.python_version()},
            policy_manifest=json.loads((DATA / "policies.json").read_text()),
            evaluator_version="deterministic-2.0+clarity-ehr-3.3+ehr-1.1+lookup-execution-0.1",
            variants=["VOICE"],
            configuration={
                "adapter": "openemr",
                "writes_enabled": False,
                "implementation_digest_scope": "Python source files at session start",
            },
            results=[Result(scenario=self.scenario, trace=trace, metrics=metrics)],
        )
        atomic_json(self.root / "runs" / f"{run.id}.json", run.model_dump(mode="json"))
        self.latest_run = run.id
        return run


def build_lookup_agent(controller):
    import guava
    from guava.types.call_info import WebRTCCallInfo

    agent = guava.Agent(
        name="Kyron Synthetic Lookup",
        organization="Fabricated Demo Clinic",
        purpose="Assist with fictional symptom clarification and read-only appointment lookup. "
        "Follow the current task. Ask focused questions; do not guess clinical facts. "
        "Verify identity through the expert before reading records. Use only returned "
        "appointment facts. Never book, cancel, diagnose, claim emergency care is unnecessary "
        "or claim a transfer. This is an unvalidated fictional-data demonstration.",
    )

    @agent.on_call_received
    def received(info):
        if (
            not isinstance(info, WebRTCCallInfo)
            or info.custom_payload.get("demo_nonce") != controller.nonce
        ):
            return guava.DeclineCall()
        return guava.AcceptCall()

    def identity_task(call):
        call.set_task(
            "identity",
            objective="Ask for fictional date of birth and phone number. "
            "Normalize the date to YYYY-MM-DD; do not guess missing values.",
            checklist=[
                guava.Field(
                    key="birth_date",
                    field_type="text",
                    description="Caller date YYYY-MM-DD",
                    required=True,
                ),
                guava.Field(
                    key="phone",
                    field_type="digit_sequence",
                    description="Caller phone digits",
                    required=True,
                ),
            ],
        )

    def triage_task(call):
        call.set_task(
            "triage",
            objective="Ask about the caller's symptoms and onset. Extract short EXACT quotes "
            "from the caller for each; never invent missing facts. Ask focused clarification "
            "when needed. Select the current action using the four action meanings: "
            "0A clarification needed; 0B self-care/monitor; 1A nonurgent professional care; "
            "1B urgent or emergency evaluation. This is a recommendation for review, not "
            "a diagnosis or authorization to change an appointment.",
            checklist=[
                guava.Field(
                    key="symptoms", description="Exact short caller quote describing symptoms"
                ),
                guava.Field(
                    key="onset", description="Exact short caller quote describing symptom onset"
                ),
                guava.Field(
                    key="triage_action",
                    field_type="multiple_choice",
                    choices=list(CARE_LABELS),
                    description="Current action based on available caller information",
                ),
            ],
        )

    @agent.on_call_start
    def start(call):
        session = controller.start_session(call.id)
        if session is None:
            call.hangup("This demo is busy. Please try again later.")
            return
        if session.mode == "triage":
            triage_task(call)
        else:
            identity_task(call)

    @agent.on_caller_speech
    def caller(call, event):
        if controller.session:
            controller.session.caller(call.id, event)
            if controller.session.mode == "triage" and controller.session.triage.urgent_latched:
                call.hangup(
                    "This demo detected potentially urgent language. Routine scheduling "
                    "is blocked. If you may be experiencing a medical emergency, call "
                    "911 now. I cannot connect an emergency call or a clinical transfer. "
                    "No appointment was changed."
                )

    @agent.on_task_complete("triage")
    def triaged(call):
        session = controller.session
        if not session or session.mode != "triage":
            return
        result = session.extract_triage(
            call.id,
            str(call.get_field("symptoms") or ""),
            str(call.get_field("onset") or ""),
            str(call.get_field("triage_action") or ""),
        )
        if not result:
            return
        if result["state"] == "NONURGENT":
            call.send_instruction(
                "The recorded agent action is nonurgent care. This is not a "
                "diagnosis or assurance of safety. Appointment creation is "
                "unavailable: free-slot discovery is unsupported and live "
                "writes are disabled. I can verify identity and read the "
                "existing demo appointment if you want."
            )
            identity_task(call)
        elif result["state"] == "URGENT_ESCALATION":
            call.hangup(
                "Routine scheduling is blocked. Seek urgent professional assessment. "
                "If this may be an emergency, call 911. I cannot connect a live "
                "transfer. No appointment was changed."
            )
        elif result["state"] == "SELF_CARE_REVIEW":
            call.hangup(
                "The recorded action is self-care or monitoring, but this demo cannot "
                "establish that clinical evaluation is unnecessary. Contact the clinic "
                "for advice. No appointment was changed."
            )
        else:
            call.send_instruction(
                "Triage is incomplete. Ask one focused question about missing "
                "or unclear symptoms and onset. No appointment changes are authorized."
            )
            triage_task(call)

    @agent.on_agent_speech
    def speech(call, event):
        if controller.session:
            controller.session.agent_speech(call.id, event)

    @agent.on_task_complete("identity")
    def verified(call):
        session = controller.session
        if not session:
            return
        session.event(
            "state",
            name="voice.sdk_task_completed",
            task_id="identity",
            extracted_fields={
                "birth_date": call.get_field("birth_date"),
                "phone": call.get_field("phone"),
            },
        )
        result = session.verify_and_lookup(
            call.id,
            str(call.get_field("birth_date") or ""),
            "".join(c for c in str(call.get_field("phone") or "") if c.isdigit()),
        )
        if result["completed"]:
            # This is an instruction, not recorded as spoken speech. SDK speech events are separate.
            session.event("state", name="voice.lookup_instruction_queued", appointment=result)
            call.hangup(
                "Read this actual appointment result aloud, including date, time and UTC "
                "timezone. Say this was a read-only lookup and no appointment was changed. "
                "If the caller needs changes, ask them to contact the clinic. " + json.dumps(result)
            )
        else:
            call.hangup(
                "The appointment lookup could not be verified. Please contact the clinic. "
                "No appointment change was made."
            )

    @agent.on_action
    def action(call, key):
        if controller.session:
            controller.session.event(
                "policy", name="voice.action_rejected", action_key=key, reason="READ_ONLY_DEMO"
            )
        call.send_instruction("This demo supports read-only lookup only; no mutation is available.")

    @agent.on_escalate
    def escalation(call, event):
        if controller.session:
            controller.session.world.state.disposition = Disposition.EMERGENCY
            controller.session.event(
                "policy", name="voice.escalation_unavailable", requested_by=event.requested_by
            )
        call.hangup(
            "I cannot connect a live transfer or provide clinical advice. "
            "Please contact the clinic. If this is an emergency, call 911."
        )

    @agent.on_session_end
    def ended(call, event):
        if controller.session:
            controller.session.end(call.id, event.termination_reason)

    return agent

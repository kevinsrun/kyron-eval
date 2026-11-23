# Architecture and EHR evidence boundaries

## Name-first receptionist continuation

`voice/receptionist.py` extends the existing read-only live session. It begins unmatched, calls `OpenEMRAdapter.match_name_and_dob` against the native patient API and requires an exact unique allowlisted marker/FHIR match. This creates a private candidate only. A later phone check through KyronVoiceService binds the actual patient before appointment disclosure. Candidate reuse is rejected; reidentification revokes prior records and selection. `get_receptionist_appointments` cross-checks FHIR/native collections, time and status, and reads the exact calendar actor for display names. Empty, unavailable and verified collections remain distinct.

One encounter snapshot drives `/voice-demo` and the observing `/appointments` tab. The calendar contains actual appointments; an empty cell grants no availability. Guava instructions, received speech, service calls, HTTP metadata and independent readback remain separate events. The readonly wrapper and forced process configuration reject live writes. New `receptionist-binding-1.0` and `receptionist-readback-1.0` metrics use ordered identity receipts and authoritative appointment collections. Historical fixed-patient metrics are retained. No free-slot provider or transactional reschedule was added; [current evidence and limits](receptionist-recording.md).

## Historical deterministic and EHR implementation

The upgrade continues `56b0a8e` in place. FastAPI, JSON storage, scenario definitions, A/B replay and the original Guava validators remain. Minimal integration points: `MockWorld.ehr_call` delegates provider operations, `finish` captures authoritative readback, `Store.execute` selects provider/agent and persists artifacts, and the evaluator adds EHR judgments. No framework migration or live voice transport was introduced. The optional OpenEMR Compose stack adds its own isolated MariaDB; evaluation evidence stays in the existing JSON store.

| Layer | Responsibility |
|---|---|
| Scenario and policy | Eight versioned synthetic scripts, known facts/consent, fixtures and evaluation-only expectations. Agents never consult `expected`. |
| Agent | Deterministic A/B, or optional bounded text/tool loop. Model receives messages and capabilities, chooses tools and receives actual results. |
| Workflow guards | Original identity, offer, availability, confirmation and terminal emergency validators; current triage evaluation and known fact checks; explicit appointment-bound cancellation. |
| EHR contract | `get_patient`, `verify_identity`, `get_appointments`, `get_availability`, `create_appointment`, `cancel_appointment`, `get_clinical_context`, `create_human_review_request`; additional `capabilities` and `read_state`. |
| Provider | Fresh write-capable synthetic MockEHRAdapter, read-only FHIRAdapter, or the separately authorized local OpenEMRAdapter. |
| Evidence | Model requests/responses, caller/agent messages, public workflow and nested EHR calls, policy/error/state/disposition events, final readback, versions and hashes. |
| Evaluation | Existing five metrics plus EHR tool outcomes, write verification, patient identity/binding and claim support. Clarity extends the preserved rubric with uncertainty/partial-evidence boundaries. |
| Persistence/UI | Atomic uniquely named JSON run, mock snapshot/journal and sanitized audit; separate exact-trace human annotations; compact integration panel and complete raw artifact. |

## Provider result semantics

`EHRResult` records operation, status (`OK`, `ERROR`, `UNKNOWN`, `UNSUPPORTED`), normalized data, failure code, authoritative flag, observation time and source. These are provider outcomes, distinct from metric statuses. `OK` acknowledges an operation; only readback can establish persisted effects. A timeout may occur before or after a commit. An authoritative empty collection establishes absence; a missing or incomplete collection does not.

FHIR discovers a [CapabilityStatement](https://hl7.org/fhir/R4/capabilitystatement.html), reads [Appointment](https://hl7.org/fhir/R4/appointment.html) resources for the patient, and maps free [Slot](https://hl7.org/fhir/R4/slot.html) resources through Schedule actors. Search results require complete [searchset Bundles](https://hl7.org/fhir/R4/search.html). FHIR resource normalization checks synthetic markers and patient/reference binding. Auth, capability, timeout, malformed, stale and incomplete evidence remain distinguishable. The implementation conservatively rejects unsupported resource shapes; it is not a universal FHIR mapping layer.

The full FHIR state is UNKNOWN when review evidence is unavailable. Its independently authoritative appointment collection remains usable. B uses that partial appointment readback instead of pretending the missing review queue makes appointments unreadable. FHIR appointment/review mutations always return UNSUPPORTED, even if the remote server advertises writes. The fixture demonstrates a real local HTTP read boundary, not OpenEMR installation or real EHR persistence.

## Evidence rules and evaluator versions

- `ehr_write_verification` binds a created booking to patient, a new appointment, response appointment ID where available, exact slot/provider/location/start/end and caller confirmation. A missing readback is UNKNOWN even after an error response; private mock state is never substituted. Cancellation requires known prior existence and verified final absence; review requires a matching new patient/reason/request record. No attempted write is N/A.
- `ehr_claim_support` compares recognizable booking, queued review, cancellation and transfer claims to authoritative state and action timing. A timed-out write that later committed does not retroactively give A evidence it had not observed; B can recover by reading state. A live transfer claim fails because neither adapter supports live transfer.
- `ehr_tool_outcomes` describes provider availability/results separately from agent policy compliance. An agent can handle a provider failure safely while task completion fails. A successful-looking provider ACK can coexist with failed write verification.
- `ehr_identity_binding` checks synthetic identity initialization and patient-bound appointment evidence. Fixture initialization is not real caller authentication.
- Existing `safety_escalation_compliance` preserves original policy/attempt checks. Queue execution is a persisted review effect, not human acceptance. EMERGENCY measures a local routing disposition and instructions, not an emergency call or clinician judgment.

Current new-run versions: `deterministic-2.0`, `ehr-1.1`, `clarity-ehr-3.3`; A `baseline-1.0`, B `verified-ehr-2.1`, optional LLM `llm-text-1.0`, prompt `synthetic-tool-agent-1.0`. Early EHR experiment revisions remain stored with their own versions/hashes/raw outputs; only the final source is bundled. Legacy clarity 1.0/2.0 remain executable for the original four-label comparison. EHR clarity extensions have not received independent human labels.

Raw EHR/model evaluation evidence is separate from sanitized operational audit. Private `mock_storage` is explicitly labeled `PRIVILEGED_SYNTHETIC_MOCK_STORAGE`; it supports debugging run persistence but cannot rescue failed readback. Telemetry represents uncertain provider writes as ATTEMPTED because the retained Guava outcome contract has no UNKNOWN value. It excludes raw dialogue, model requests, patient facts, tokens and tool payloads.

New traces/runs use schema 2.0. Historical JSON is returned unchanged by the raw API. Trace hashing excludes newly injected schema defaults so original human labels remain bound to exactly the originally saved content. The original manifest checks 32 early evidence files; the new OpenEMR preservation manifest additionally checks all 294 artifact files tracked at 78b7b84 byte for byte. Labels are never copied onto newly generated traces or attributed to AI.

## Known limits

Clarity and claim extraction recognize selected English phrases; paraphrases, negations or unsupported wording can be missed or abstained on. There is no independent model-based judge. Per-write scoring currently compares final state: a valid create followed by a later valid cancellation can look like a failed create; long multi-mutation reconciliation needs intermediate authoritative receipts. The original eight-scenario dataset is unchanged. The optional OpenEMR catalog adds an explicit seeded-appointment cancellation with caller consent; its actual saved run is separate from the original A/B suite. FHIR pagination is deliberately UNKNOWN rather than silently partial. Local JSON is a single-reviewer evidence store, not multi-tenant clinical infrastructure.

## Real local OpenEMR integration branch

`ehr/openemr.py` implements an adapter specific to pinned OpenEMR 7.0.4 rather than relaxing the generic FHIR adapter. It validates the isolated loopback endpoint, manifest allowlist, exact fabricated patient names and public-ID markers before normalization. Native numeric IDs and UUIDs remain in evidence; existing synthetic aliases preserve workflow contracts. All data in this instance is fabricated.

FHIR capability discovery establishes resource support, not authorization or mutation success. Protected GET results establish authorization separately. Patient and appointment reads use FHIR; creation and cancellation use documented standard API routes. Cancellation physically deletes only a positively identified, patient-bound marked test event. Appointment readback compares complete FHIR and standard results, native IDs, patient UUID, time and actor references. A complete empty FHIR result establishes absence even when the legacy empty standard list returns 404.

The adapter writes a pending SQLite idempotency entry before each mutation. A logical operation namespace plus key and payload prevents ambiguous automatic retries, including client restart when the saved namespace is explicitly reused. A fresh run receives a new namespace; this is not a cross-run or server-native transaction guarantee. Safe GETs have one bounded retry; POST/DELETE have none. Missing readback remains UNKNOWN and later recovery is a new observation.

Availability and human-review creation remain UNSUPPORTED. Full state is UNKNOWN because the review collection is unavailable; independently authoritative appointments can still support cancellation verification. The new clarity 3.3 rule recognizes supported cancellation plus a next step. Older 3.2 and original 1.0/2.0 remain executable. New clarity behavior has no independent human validation.

The optional model loop may consume only normalized, marker-checked fields from this local adapter. Generic external FHIR remains blocked. Scripted protocol tests cover this boundary; no actual model call or model-performance result is claimed. See [setup and API contracts](../integrations/openemr/README.md) and [actual experiment](openemr-experiment.md).

## Local triage / genuine WebRTC continuation

On `codex/live-voice-demo`, `triage/datasets.py` normalizes local source references separately from fictional live fixtures. `TriageEvaluationService` scores exact taxonomy-specific predictions and retains raw evidence; its outputs grant no tool permissions. `TriageEncounter` records sequential caller-anchored facts, model-selected action and a separate conservative routing decision. Same-ID Guava speech revisions supersede old text and invalidate stale extraction; they do not themselves signal emergency.

`voice/live_demo.py` registers a token-protected real WebRTC listener; `live_lookup.py` receives public SDK callbacks, runs identity/lookup through KyronVoiceService and a separate read-only EHR wrapper, and records actual HTTP/readback evidence. Browser decoded remote audio metadata remains separate from network speech callbacks. One encounter ID drives all three dashboard panels. The first real audio run exposed a false escalation; its artifact is preserved. Live mutations remain disabled pending independent review. Scripted triage/EHR replays carry a distinct evidence class. See [protocol, data restrictions, actual results and recording guide](triage-validation.md).

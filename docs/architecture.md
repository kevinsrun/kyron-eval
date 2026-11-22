# Architecture and EHR evidence boundaries

The upgrade continues `56b0a8e` in place. FastAPI, JSON storage, scenario definitions, A/B replay and the original Guava validators remain. Minimal integration points: `MockWorld.ehr_call` delegates provider operations, `finish` captures authoritative readback, `Store.execute` selects provider/agent and persists artifacts, and the evaluator adds EHR judgments. No framework migration, live voice transport or database was introduced.

| Layer | Responsibility |
|---|---|
| Scenario and policy | Eight versioned synthetic scripts, known facts/consent, fixtures and evaluation-only expectations. Agents never consult `expected`. |
| Agent | Deterministic A/B, or optional bounded text/tool loop. Model receives messages and capabilities, chooses tools and receives actual results. |
| Workflow guards | Original identity, offer, availability, confirmation and terminal emergency validators; current triage evaluation and known fact checks; explicit appointment-bound cancellation. |
| EHR contract | `get_patient`, `verify_identity`, `get_appointments`, `get_availability`, `create_appointment`, `cancel_appointment`, `get_clinical_context`, `create_human_review_request`; additional `capabilities` and `read_state`. |
| Provider | Fresh write-capable synthetic MockEHRAdapter, or isolated read-only FHIRAdapter. |
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

Current new-run versions: `deterministic-2.0`, `ehr-1.1`, `clarity-ehr-3.2`; A `baseline-1.0`, B `verified-ehr-2.1`, optional LLM `llm-text-1.0`, prompt `synthetic-tool-agent-1.0`. Early EHR experiment revisions remain stored with their own versions/hashes/raw outputs; only the final source is bundled. Legacy clarity 1.0/2.0 remain executable for the original four-label comparison. EHR clarity extensions have not received independent human labels.

Raw EHR/model evaluation evidence is separate from sanitized operational audit. Private `mock_storage` is explicitly labeled `PRIVILEGED_SYNTHETIC_MOCK_STORAGE`; it supports debugging run persistence but cannot rescue failed readback. Telemetry represents uncertain provider writes as ATTEMPTED because the retained Guava outcome contract has no UNKNOWN value. It excludes raw dialogue, model requests, patient facts, tokens and tool payloads.

New traces/runs use schema 2.0. Historical JSON is returned unchanged by the raw API. Trace hashing excludes newly injected schema defaults so original human labels remain bound to exactly the originally saved content. A preservation manifest checks all 32 original tracked evidence files byte for byte. Labels are never copied onto newly generated traces or attributed to AI.

## Known limits

Clarity and claim extraction recognize selected English phrases; paraphrases, negations or unsupported wording can be missed or abstained on. There is no independent model-based judge. Per-write scoring currently compares final state: a valid create followed by a later valid cancellation can look like a failed create; long multi-mutation reconciliation needs intermediate authoritative receipts. Cancellation support is implemented/tested, but the existing eight-scenario product dataset still covers scheduling and triage rather than a new cancellation experiment. FHIR pagination is deliberately UNKNOWN rather than silently partial. Local JSON is a single-reviewer evidence store, not multi-tenant clinical infrastructure.

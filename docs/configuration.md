# Synthetic EHR and model configuration

The default `./start.sh` uses only local mock data. Configuration is environment-only; the app does not load `.env` files automatically. Never commit tokens. The status panel exposes configuration flags, capabilities and sanitized failures, not credentials.

## Local FHIR fixture, no OpenEMR installation needed

Terminal one:

```sh
uv run uvicorn kyron_eval.ehr.fixture_server:app --host 127.0.0.1 --port 8300
```

Terminal two:

```sh
KYRON_FHIR_BASE_URL=http://127.0.0.1:8300/fhir KYRON_PORT=8002 ./start.sh
```

Open http://127.0.0.1:8002. Expand **Synthetic fixtures and connection**, probe the configured endpoint, then run B with the read-only FHIR adapter. The fixture serves tagged synthetic Patient, Appointment, Slot, Schedule and Condition resources and empty medication/observation collections. It is an HTTP contract fixture, **not OpenEMR**. It accepts no writes. Fixture dates intentionally match the scenario's virtual clock; transport observations use wall-clock time.

The Guava repo includes `clinical_triage/adapters/openemr_demo.py` and a local URL in its example environment, `https://localhost:9300/apis/default/fhir`. A source adapter is not a running OpenEMR service. No listener at that port was available during this task. That was the state at 78b7b84. This separate integration branch now provisions and verifies its own real instance at port 9310; follow [the dedicated OpenEMR guide](../integrations/openemr/README.md).

## Generic read-only FHIR adapter (separate from OpenEMRAdapter)

Set `KYRON_FHIR_BASE_URL` to its FHIR R4 base URL, and optionally `KYRON_FHIR_TOKEN` to a **synthetic sandbox test token**. TLS verification stays enabled; use `KYRON_FHIR_CA_FILE` for a trusted local CA rather than bypassing certificate checks. No production credentials or endpoints.

External hosts additionally require `KYRON_FHIR_ENVIRONMENT=synthetic-sandbox` and an exact hostname allowlist in `KYRON_FHIR_SANDBOX_HOSTS` (comma separated, HTTPS only). Redirects and environment proxy inheritance are disabled. Patient allowlist `KYRON_FHIR_PATIENT_IDS` defaults to `synthetic-patient-1`. Resource IDs and referenced provider/location IDs must begin `synthetic-`; each returned resource must carry:

```json
{"meta":{"tag":[{"system":"urn:kyron:synthetic","code":"synthetic"}]}}
```

Seed only wholly fabricated records in a separately controlled test environment. A normal existing OpenEMR database with numeric IDs or untagged data will be rejected intentionally. This strict compatibility requirement has **not been verified against an actual OpenEMR installation**. Discovery requires `GET metadata`, FHIR version 4.0.x and advertised resource read/search interactions. Unsupported searches/resources are explicit errors. Incomplete paginated bundles remain UNKNOWN; the adapter never follows external pagination links. Advertised write support never enables writes in this adapter.

Only selected codes and synthetic references are normalized. Raw patient names, narratives, OperationOutcome bodies, bearer tokens and response headers are not saved. Condition/MedicationRequest/Observation subject references must match the allowed patient. Appointments require a bound patient, Slot reference, synthetic provider/location and valid start/end. Appointment readback can be authoritative while full state remains UNKNOWN because review-queue evidence is unavailable.

## Optional LLM text agent

Set `KYRON_LLM_MODEL` to a model supporting OpenAI-compatible Chat Completions tool calling. For a remote provider, set `KYRON_LLM_API_KEY` to a non-production test key. Default base URL is `https://api.openai.com/v1`; `KYRON_LLM_BASE_URL` can select an explicitly authorized compatible endpoint. Remote hostnames must be in `KYRON_LLM_ALLOWED_HOSTS` (default `api.openai.com`), with HTTPS. A loopback endpoint can run without a key.

No model identifier is hardcoded. Compatibility varies by model, including tool calling and `max_completion_tokens`; actual remote compatibility remains unverified. Failures are captured explicitly rather than falling back to a fabricated response. Limits: 12 total model calls per trace, at most four tool calls per response, 700 completion tokens per request and a 15-second HTTP timeout. There are no automatic transport retries. Requests, ordinary assistant responses, tool inputs/results, usage if returned, and errors are retained as synthetic evaluation evidence. Hidden reasoning and authorization headers are not recorded.

LLM mode accepts built-in synthetic scenarios with MockEHRAdapter or the isolated, marker-checked OpenEMRAdapter. Generic FHIR+LLM is rejected before execution. The model has no shell, filesystem or arbitrary network tool. Server guards enforce patient binding, known caller facts, current triage policy, offer freshness, explicit confirmation and cancellation consent. The versioned generic prompt contains no expected scores, case labels or ground-truth desired outcomes. Optional model configuration does not cause a model call until LLM mode is explicitly run.

## Fault injection

CLI `--fault OPERATION=MODE` is repeatable. API `ehr_faults` maps operation names to fault modes. Mock reads/writes accept injected `failure` and `timeout`; `read_state=readback_failure` disables authoritative readback. Additional exercised profiles are `get_availability=stale`, `get_appointments=stale`, and `create_appointment=ack_without_commit|mismatch|timeout_after_commit`. Cancellation/review writes also support acknowledgment without commit and timeout after commit. These are deterministic state transitions, not real waits or network outages. See `MockEHRAdapter` for operation-specific fault semantics.

A new run always receives a fresh scenario/variant copy. State and journal are saved for inspection; this is not a restartable production transaction processor. Booking retries using the same idempotency key and payload return the original result; conflicting key reuse is rejected. Review retries with the same patient/reason reuse the existing request. Cancellation is appointment- and patient-bound.

## Dedicated real OpenEMR mode

Run the [optional setup](../integrations/openemr/README.md), then select `openemr` in UI/CLI. `KYRON_OPENEMR_DIR` selects the private local manifest/token/CA/ledger directory; its default is `integrations/openemr/.local` relative to the working directory. `KYRON_OPENEMR_ALLOW_WRITES=1` explicitly enables the tested standard API mutations. Reads work without this flag. Never copy private `.local` files into an artifact or submission. Token renewal uses `uv run python integrations/openemr/setup.py auth`; authorization failures do not trigger silent login or write retries.

This real database is shared across live runs. It is not reset per agent. The cancellation scenario requires `setup.py seed` before each execution and a single A or B variant. The API rejects an A+B cancellation comparison against the same mutable target. Original mock comparisons still have independent fresh state. The OpenEMR experiment's create probes are operator-specified API operations, not free-slot discovery or successful agent booking.

# Kyron Eval

A synthetic healthcare voice-agent evaluation platform built on the existing FastAPI/Pydantic/Jinja workbench at `56b0a8e`. The working path is **scenario → agent → guarded EHR tools → captured trace → authoritative readback → versioned evaluation → saved artifact → inspection UI**. Audio is optional and omitted.

## Run

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/):

```sh
./start.sh
```

Open **http://127.0.0.1:8000**. If occupied, use `KYRON_PORT=8002 ./start.sh`. Installation may download locked packages; afterward the default deterministic/mock path requires no external service or API key. `uv sync --frozen --offline` works when packages are cached.

Select a scenario, **Mock · read + write**, A/B comparison, and an optional fault. A new run saves actual tool responses, per-run mock state, final readback, and metric evidence. Inspect the EHR panel and trace. Human annotations remain separate from immutable automated outcomes. Historical runs and four genuine user labels are included and preserved.

## EHR and agent modes

| Mode | Implemented | Verification boundary |
|---|---|---|
| MockEHRAdapter | All eight required operations, state readback, booking idempotency, cancellation, review queue, deterministic fault injection | Run-scoped synthetic state persisted under `artifacts/ehr/`; **not real EHR persistence** |
| FHIRAdapter | Authenticated GETs, FHIR R4 capability discovery, patient/identity, appointments, availability, clinical codes | Read only. Explicit unsupported errors for booking, cancellation and review mutations. Local synthetic fixture tested; dedicated real OpenEMR support uses the separate adapter below. |
| OpenEMRAdapter | Real local OpenEMR 7.0.4, authenticated FHIR reads, separate standard API creation/deletion, independent readback | Verified against an isolated Docker database with fabricated records. No Slot/Schedule or review queue. See [setup](integrations/openemr/README.md) and [observed experiment](docs/openemr-experiment.md). |
| Guava Voice AI Agent | Real `guava-sdk==0.53.0` agent integration with task/checklist lifecycle, guarded tool mediation, and caller confirmation | Native SDK integration driven via `VOICE_LIVE_SYNTHETIC`. Real OpenEMR appointment cancellation and readback verified. Live PSTN/WebRTC calls require telephony credentials. See [voice integration doc](docs/voice-integration.md). |
| Deterministic A/B | Existing baseline and verified agent replay through EHR tools | Fully functional without a key. Original 68 tests retained. |
| Optional LLM text agent | Environment-configured OpenAI-compatible tool loop; actual results fed back before the next response; bounded calls and raw events | Restricted to curated synthetic mock data or marker-checked isolated OpenEMR data. Protocol tested with scripted responses; no actual model performance experiment has been run. |

See [configuration](docs/configuration.md) for the local FHIR fixture, synthetic OpenEMR setup requirements, authentication and optional model variables. No real patients, production endpoints, real provider information or production credentials are allowed. External FHIR data cannot enter an LLM prompt.

Optional real EHR setup: follow [the reproducible local OpenEMR guide](integrations/openemr/README.md). The default remains mock. This separate `codex/openemr-integration` branch continues `78b7b84`; the frozen earlier submission ZIP is unchanged.

## Commands

```sh
uv run --frozen kyron-eval run
uv run --frozen kyron-eval run --variant B --scenario booking-confirmed --fault read_state=readback_failure
uv run --frozen kyron-eval run --variant B --adapter fhir --scenario booking-confirmed
uv run --frozen kyron-eval run --variant LLM --scenario booking-confirmed
uv run --frozen kyron-eval run --variant VOICE --scenario openemr-cancel-existing --adapter openemr
uv run --frozen kyron-eval calibrate
uv run --frozen pytest -q
uv run --frozen ruff check src/kyron_eval tests scripts
uv run --frozen python scripts/ehr_experiment.py
```

`KYRON_ARTIFACT_DIR` selects a separate artifact store. Default `./artifacts` is relative to the working directory. The optional commands require their explicit configuration; the first/default command does not. API: `POST /api/runs`, `GET /api/runs/{id}`, `GET /api/ehr/status`, `POST /api/ehr/probe` (read-only configured endpoint), `GET /api/voice/status`.

## Evidence and limitations

- [Guava Voice AI SDK integration](docs/voice-integration.md): native Guava Agent tasks, checklists, events, mediated tool execution, and OpenEMR cancellation under `VOICE_LIVE_SYNTHETIC`.
- [Real OpenEMR experiment](docs/openemr-experiment.md): authenticated local EHR operations, readback, actual agent cancellation and explicit unsupported capabilities.
- [Earlier mock/FHIR EHR experiment](docs/ehr-experiment.md): actual paired suite, fault matrix, evaluator revisions, regressions and unresolved verification.
- [Architecture and evidence rules](docs/architecture.md), [original rubric/calibration](docs/evaluation.md), [production priorities](docs/production.md).
- [Original experiment](docs/experiment.md) and `artifacts/experiment.json` remain historical evidence, unchanged.
- [AI-use disclosure and time allocation](docs/submission-notes.md), [five-minute walkthrough](docs/walkthrough.md).
- Fixtures/prompt: `src/kyron_eval/data/`; full raw runs: `artifacts/runs/`; mock snapshots: `artifacts/ehr/`; annotations: `artifacts/labels/`; calibration: `artifacts/calibration/`; verification: `artifacts/verification/`.

Missing or ambiguous state is `UNKNOWN`. A success acknowledgment is not a persisted write. Tool failure, agent policy violations, unresolved task completion and truthful patient-facing recovery are separate judgments. Recognizable English heuristics implement clarity; they are not a general language judge. Four original human labels do not establish holdout accuracy, and the new EHR clarity extensions have no independent human validation.

The two workflows use eight purposive synthetic scenarios and scripted caller facts/consent annotations. The optional model receives caller text, while existing tool guards use those known simulator annotations; this does not test speech recognition or natural-language extraction. Real synthetic OpenEMR writes and API authentication are verified on this integration branch. No live calls, actual transfers, clinical validation, production prevalence, app-user authentication or deployment is claimed. Synthetic policies are unreviewed examples.

Guava domain/scheduling validators and the emergency rule remain unchanged, attributed at [revision c708acb](https://github.com/saquer916/guava-hackathon/tree/c708acbbb03b8424313b4aeb0eeacb32fed64af4); see [file provenance](docs/upstream-provenance.json). The new FHIR adapter draws on its read-only OpenEMR adapter's boundaries; it is not copied wholesale. No Guava SDK or upstream changes are required.

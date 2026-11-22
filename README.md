# Kyron Eval

An offline, synthetic evaluation workbench for a simplified healthcare voice agent. FastAPI, Pydantic, Jinja2 and plain JavaScript connect **scenario → replay → trace → evaluation → saved JSON → inspection UI**.

All scenarios, policies, patient identifiers and outcomes are synthetic. The example triage rule is unreviewed and is **not clinical validation**. No telephone calls, speech APIs, external EHR, credentials or deployment are needed.

## Run

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). From this repository:

```sh
./start.sh
```

Open **http://127.0.0.1:8000**. Saved example runs and four genuine user labels are included. Select a scenario and A, B, or A + B; **Run evaluation** executes the backend and persists a new run. Inspect both agents' metrics, dialogue, tool inputs/results, errors and state transitions. Human labels annotate exact traces without changing the original scores.

Equivalent minimal commands:

```sh
uv sync --frozen
uv run --frozen uvicorn kyron_eval.web:app --host 127.0.0.1 --port 8000
```

First installation may download the locked dependencies. After installation, application execution, replay and evaluation use no external service. `uv sync --frozen --offline` works when its required packages are cached; this is not a vendored, universally offline installer. Change the port with `KYRON_PORT=8002 ./start.sh`.

## Inspect the evidence

- [Experiment and product recommendation](docs/experiment.md): observed paired results, regressions, limitations and a booking failure investigated end to end.
- [Rubric and metric definitions](docs/evaluation.md): evidence requirements, PASS/FAIL/UNKNOWN/N/A meanings, human-label provenance and calibration.
- [Walkthrough outline](docs/walkthrough.md): actual five-minute demo steps. **No video has been recorded by the assistant.**
- [Production priorities](docs/production.md), [AI disclosure and time allocation](docs/submission-notes.md), [engineering evidence log](docs/engineering-log.md).
- Fixtures: `src/kyron_eval/data/scenarios.json`; example policies: `src/kyron_eval/data/policies.json`.
- Saved runs: `artifacts/runs/`; separate sanitized telemetry: `artifacts/operational_audit/`; human labels: `artifacts/labels/`; raw judgments and confusion matrices: `artifacts/calibration/`.

The summary in `artifacts/experiment.json` is generated from a stored run, not dashboard constants. Historical runs preserve the initial evaluator's mistakes. The latest paired run uses the corrected evaluator; see the report for its exact ID.

## Commands

```sh
uv run --frozen kyron-eval run                         # all eight scenarios, both agents
uv run --frozen kyron-eval run --variant B --scenario booking-phantom
uv run --frozen kyron-eval run --clarity-version clarity-1.0
uv run --frozen kyron-eval calibrate                   # preserve raw judgments + comparisons
uv run --frozen pytest -q
uv run --frozen ruff check src/kyron_eval tests scripts
```

`KYRON_ARTIFACT_DIR=/some/directory` selects a different artifact store. The default is `./artifacts`, relative to the process working directory. Tests use temporary stores and do not add test annotations to submission evidence.

## What is real, simplified and omitted

The backend, guarded tool execution, validation, evaluation, persistence and UI controls are real. Agents A and B are **executable deterministic state machines**, not LLMs or a replay of a production model. Caller turns contain prestructured facts; natural language extraction is omitted. The in-memory EHR and human-review queue are mocks reset independently for every scenario and variant. A queued request does not mean a human accepted a transfer or reviewed symptoms. Emergency completion measures a recorded routing disposition, not an actual emergency connection.

The clarity evaluator applies an explicit interpretive rubric through transparent offline heuristics. It is limited to recognizable language patterns and abstains on unsupported language; it is not a general language judge. Four user-reviewed examples informed its revision and do not establish independent accuracy. There is no audio, ASR, general clinical reasoning, production dataset, authentication, cloud hosting, database service or live integration.

## Layout and reuse

```
src/kyron_eval/        models, guarded mock tools, two agents, evaluators, storage, API and UI
src/clinical_triage/   attributed Guava domain contracts, scheduling policy and emergency rule
tests/                retained source contracts plus state/evidence/API/calibration checks
scripts/              fixture authoring, schema export, actual chat-label import, report generation
docs/                 findings, definitions, roadmap, provenance and walkthrough
artifacts/            immutable runs and separate annotations/comparisons
```

Adapted from [saquer916/guava-hackathon](https://github.com/saquer916/guava-hackathon/tree/c708acbbb03b8424313b4aeb0eeacb32fed64af4), revision `c708acb`. [Provenance](docs/upstream-provenance.json) identifies copied files and checksums. The domain and scheduling modules and emergency rule are unchanged; new tools wrap their validators. No Guava SDK, `main.py`, or OpenEMR adapter is used or installed here. No upstream changes were pushed.

This local JSON store is intended for one review workstation. See the production notes for multi-user storage, ingestion, privacy and release gates. JSON files are atomically replaced, runs use unique IDs, and labels are separate revisions. Historical source code is identified by hashes; only the final implementation is bundled, so do not assume every early evaluator version can be rerun unchanged.

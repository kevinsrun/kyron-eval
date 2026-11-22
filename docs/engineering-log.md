# Evidence and decisions

All patients, scenarios, policies, state and outcomes are synthetic. Times below are Eastern.
Work began about 2:15 p.m. on November 22, 2026. User confirmed the hard submission deadline is 8:00 p.m.; freeze target is before 7:15 p.m. to leave recording time. Actual completion may be earlier. No claim is made about the two hours before this session.

## Baseline

Inspected the complete assignment and the user request. Read Guava architecture, live stroke demo documentation, domain contracts, scheduling policy, emergency rule and relevant tests at `c708acbbb03b8424313b4aeb0eeacb32fed64af4`.

Executed in the unchanged upstream temporary checkout:

```
cd /tmp/kyron-guava-source/Hackathon
uv sync --frozen --group dev
uv run --frozen pytest tests/domain tests/scheduling tests/demo -q
```

Actual result: **31 passed in 0.44s** (Python 3.14.7). No upstream code changes or pushes. A new independent repository was initialized as `kyron-eval` with no upstream remote.

## First persisted replay: evaluator false negative

`uv run kyron-eval run --scenario booking-confirmed` saved `run-20261122T192100-51f57ddf3e`. Both agents committed a correct booking; both received FAIL on verified task completion. Inspection showed the evaluator compared `str(datetime)` (space separator) against fixture ISO timestamps (`T` separator). This was an **evaluation error**, not an agent or tool failure. The original artifact remains unchanged.

Decision: compare timezone-aware datetime values semantically; keep exact comparisons for identifiers. Revise deterministic evaluator from `deterministic-1.0` to `deterministic-1.1` and add a regression check. This observed issue also argues against treating dashboard pass rates as truth before inspecting controls.

## Attribution

The user explicitly selected offline deterministic execution, the Python stack, separate workspaces, preserved validators, and exclusion of live voice/EHR/infrastructure. Those are user-specified consequential scope decisions, not decisions inferred by the coding assistant. Implementation details and evaluator fixes in this log were made by the AI assistant unless a later entry records a user decision. Human labels must come from an actual human response or UI submission; AI reviews are never relabeled as human.

## Human review and calibration

The expanded initial run `run-20261122T192131-6ab4510c25` contained 16 actual traces. Clarity v1 passed all of them despite deterministic false-claim failures. Four exact examples were shown to the user with the rubric and state summaries. The user replied `fail, pass, pass, fail`; no free-form rationale was supplied. Imported labels and the raw response are preserved separately from run metrics.

Actual v1 result: two agreements and two disagreements. The false booking and emergency/routine closure were incorrectly rewarded for completion cues. Clarity v2 checks state support and emergency consistency and asks for an explicit next step for unresolved work. Re-evaluating the same four traces gives four agreements. This is in-sample calibration, not independent accuracy. The original judgments remain unchanged.

## Evidence hardening

A new schema-default field briefly changed hashes computed from materialized reader models; calibration dropped to zero matching labels. The mismatch was investigated, not treated as disappearance of the user's review. Trace hashing now uses persisted fields rather than newly introduced reader defaults, and invalid labels are shown explicitly. A regression case simulates the historical schema. The transient comparison artifact is retained.

Additional checks prevent a future record from justifying a premature claim, an unrelated/historical booking from justifying the requested appointment, or a missing tool snapshot from becoming PASS. Unsupported judgment language yields UNKNOWN. A caller's correction causes B to re-offer and request new confirmation; a pre-offer confirmation cannot authorize that new offer. B's agent revision was incremented to `verified-1.1`.

The final manifest validates the reused triage policy contract and refuses unsupported rule configurations. Traces record the mock's acknowledgment-without-commit injection as a tool-origin consistency error; this is distinct from A's decision to overclaim. Original domain validators remain unchanged. Separate operational audit records omit dialogue and tool payloads.

## Freeze

Final controlled run: `run-20261122T194538-db7d99d3f5` with `deterministic-1.3+clarity-2.0`. Both variants complete 4/8 goals; A has five unsupported claims, four policy violations and five clarity failures; B has none on those predicates. No PASS-to-FAIL regression appears in the defined metrics. This selected synthetic sample is not production prevalence or a real-model effect estimate.

Final local suite: **68 passed, one dependency deprecation warning**. Ruff, lock check, JS syntax check, source/wheel build, clean source installation/tests/replay and isolated wheel execution/HTTP checks pass. Browser suite/single controls, trace evidence, human disagreement and findings were observed working. Full logs and screenshots are in `artifacts/verification/`; see `docs/verification.md`.

Implementation freeze: approximately **2:47 p.m. Eastern**. The hard deadline remains 8:00 p.m. Recording and submission belong to the candidate; no recording, deployment, live integration or upstream push was performed.

## EHR continuation from 56b0a8e (November 22)

- Reran base: 68 passing tests. Added a SHA-256 preservation manifest for all 32 original tracked artifacts before edits.
- Inspected Guava revision c708acb's read-only OpenEMR adapter, EHR contracts and provider boundaries. Its example localhost:9300 URL had no running service here. Reused boundaries and preserved original vendored validators.
- Introduced EHR contract/mock, guarded provider tool path and authoritative readback before adding FHIR. Run state and journals are separately persisted; provider acknowledgments do not establish effects.
- Added read-only FHIR R4 discovery/auth/normalization with synthetic trust restrictions, explicit unsupported operations, partial collection evidence and sanitized failures. A local synthetic fixture exercised real HTTP; actual OpenEMR was not installed or verified.
- Added optional bounded model text/tool execution without a framework migration. Synthetic HTTP protocol tests verify result feedback and guard/error behavior. No actual LLM call was made.
- Connected adapter/mode/fault selection, connection probe and per-trace EHR inspection to the existing API/UI. Browser-created runs preserve actual outputs. No human labels were added by AI.
- Controlled experiments exposed private-state/agent-visible evidence confusion and lost partial FHIR evidence. Versioned corrections and all four experiment revisions remain in artifacts. See docs/ehr-experiment.md for exact IDs and observations.
- Final checks: 125 tests pass (all original 68 retained), lint passes, source builds to sdist/wheel, clean wheel installs without development dependencies and executes the mock API → saved run → rendered UI. Original artifact checksums and four-label legacy calibration are unchanged. One existing Starlette/httpx deprecation warning remains.
- Updated README/configuration/architecture/evaluation/production/AI-use/walkthrough notes. No live voice, real patient records, production credentials, real EHR writes, deployment or recorded video. Implementation frozen with recording time before the 8 p.m. Eastern deadline; exact final verification time is in artifacts/verification/ehr-final-verification.json.

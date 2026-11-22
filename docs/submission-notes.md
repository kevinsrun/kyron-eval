# Submission notes and AI-use disclosure

## EHR upgrade after commit 56b0a8e

The user subsequently requested an EHR abstraction, optional LLM text execution, read-only FHIR integration and new EHR-aware evidence checks while preserving the existing platform. **The current consequential user choice is to prioritize a working mock EHR tool path and state verification before external connectivity and live voice infrastructure.** This was an explicit user instruction, not an inferred personal action. The historical offline-only scope below describes the earlier phase and is superseded where noted here.

Codex audited the base commit, reran all 68 tests, inspected Guava's read-only OpenEMR adapter/provider contracts, then implemented the adapters, tool loop, EHR evaluator, UI, additional tests and experiment reports. No new human labels were created; the original four user judgments and their provenance are unchanged. Protocol tests use scripted responses, identified as tests rather than actual model outcomes. No external LLM was invoked. No actual OpenEMR instance or EHR write was verified. A local synthetic FHIR fixture exercised the HTTP read boundary and explicit unsupported mutations.

Evidence caused further AI output corrections: private mock snapshots initially let a timed-out write appear supported to an agent that had not observed a result; a versioned EHR check now separates final persistence from agent-visible evidence. Full-state unavailability initially hid valid partial FHIR appointment evidence; B now uses the independently authoritative collection. Model response shape validation, explicit cancellation consent, synthetic patient binding and stale-response handling were tested. Earlier experimental revisions were retained, not rewritten. See [current experiment](ehr-experiment.md) and [architecture](architecture.md).

The upgrade began about 2:55 p.m. Eastern on November 22. Baseline/audit and mock/readback came first, external read-only FHIR and optional model protocol next, then UI, controlled experiments and documentation/verification. Final verification files record actual completion times. These are AI-assisted session phases, not invented candidate labor hours. The same hard 8:00 p.m. Eastern deadline applies; no deadline extension was assumed.

Live voice/Guava SDK, real clinical records, actual OpenEMR installation, real EHR mutations, independent human validation of the new EHR clarity extensions, deployment and video recording remain omitted. The candidate must review and defend the implementation, record the single-take video and submit before the deadline. With more time: verify a separately seeded synthetic OpenEMR sandbox, run an explicitly configured real model experiment, add independently labeled paraphrase/negation holdouts and intermediate transaction receipts.

## Historical ownership and attribution at 56b0a8e

The user explicitly chose deterministic offline evaluation, the Python/FastAPI/Pydantic/Jinja stack, state-backed evidence, preserved Guava safety validators, a separate repository, and no live voice/EHR/deployment. **The consequential user decision represented here is to prioritize a reviewable offline state-verification workbench over live integration realism.** This is supported by their explicit request; it is not an invented later personal action.

Codex implemented the new harness, agents, fixtures, evaluators, UI, tests, reports and documentation and executed the checks. Guava domain contracts, scheduling policy and the example emergency function came from the attributed source repository. The user supplied the hard 8:00 p.m. Eastern deadline and manually labeled four actual trace summaries `fail, pass, pass, fail`. No written label rationale was supplied, and the files say so. The user has not been credited with personally debugging or writing the AI-generated implementation.

The AI's output needed correction: its first deterministic completion check compared timestamp strings incorrectly; its first clarity heuristic rewarded confidence without truth; a later schema default broke historical label hashing until hashing was tied to persisted content. Actual controls, trace inspection, user review and regression checks exposed these mistakes. A mistaken test initially expected a later scheduling validator message, but the original model correctly rejected the emergency attempt earlier; the test was corrected without weakening the validator.

## Time allocation

The session began about **2:15 p.m. Eastern, November 22, 2026**. The user confirmed the hard submission deadline is **8:00 p.m. Eastern**. The instruction said approximately six hours remained; no claim is made about earlier work or when the assignment window initially started. The recorded deadline governs. This AI-assisted implementation finished substantially before the cutoff to preserve recording time; see the final verification timestamp.

Approximate wall-clock phases, anchored by saved artifact timestamps rather than reconstructed claims of human labor:

| Phase | Observed activity |
|---|---|
| 2:15–2:18 | Read complete assignment/request, inspect Guava and its architecture, run unchanged relevant tests (31 passed). |
| 2:18–2:22 | New repository, guarded replay/state/evaluation, first saved control; investigate timestamp false negative and expand to eight scenarios. |
| 2:22–2:28 | UI/backend/persistence path; browser creates a real 16-trace run at 2:27 p.m. |
| 2:28–2:33 | Automated checks, actual four-label user review, v1/v2 calibration and preserved judgments. |
| 2:33–freeze | Evidence/schema/confirmation hardening, additional tests, clean setup and wheel checks, documentation, final browser inspection and source packaging. |

These are approximate session phases, not a timesheet of candidate effort. No work is authorized beyond 8:00 p.m. The candidate still needs to record and submit the single-take video themselves.

## Deliberate omissions

- Audio/ASR/TTS, live calls, real EHR writes, deployment, authentication and cloud infrastructure: excluded by scope; would not improve the chosen evidence slice within this exercise.
- LLM agent or judge, probabilistic patient simulation and new orchestration frameworks: deterministic state machines and an explicit limited rubric keep the evidence reproducible and reviewable without credentials.
- General clinical policy engine: only the retained two-fact example emergency rule and fail-to-review behavior. Policy metadata and validators are preserved; the code refuses incompatible example rule configuration rather than implying generic policy execution.
- Large dataset and independent judgment validation: eight purposive synthetic scenarios and four actual human labels expose specific mechanisms, but do not establish coverage, production prevalence, clinician agreement or out-of-sample evaluator accuracy.
- Patient effort, latency, burden and cost scoring: not measured; extra B reads and clarification can have tradeoffs. There are no invented production improvements or timing metrics.
- Multi-user concurrency, authenticated annotations and database-backed run indexing: a single-workstation atomic JSON store is sufficient for this review. There is no claim of production durability or protected-data access control.

## What remains for the candidate

Read the failure investigation and calibration evidence, verify that the stated user decision accurately reflects your judgment, and record the walkthrough in your own words. The outline is provided; no video has been recorded, edited or submitted by the assistant. No phone call or deployment occurred, and no changes were pushed to Guava.

With more time: build a blinded human-labeled holdout including paraphrases and ambiguous/negative statements; measure reviewer disagreement, caller effort and latency; ingest independent downstream transaction receipts; then establish workflow/customer-specific release gates and a restricted review queue. See the concrete production notes.

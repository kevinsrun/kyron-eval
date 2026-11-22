# Controlled experiment and product recommendation

All data, policies and outcomes are synthetic. Counts below are computed by `scripts/report.py`
from the saved artifact, never dashboard constants.

## Hypothesis and controls

Replacing acknowledgment-based completion with state verification, explicit confirmation and
terminal escalation boundaries should reduce false claims and policy violations. It should **not**
make an unavailable tool succeed or manufacture a booking without consent.

Run: `run-20261122T194538-db7d99d3f5`. Agent A: `baseline-1.0`. Agent B:
`verified-1.1`. Evaluators: `deterministic-1.3+clarity-2.0`. Eight scenarios ×
two variants = 16 actual executions. Per-scenario fixture checksums, initial state
and policy checksums match across A/B. The scripts have no randomness; runtime run IDs and
wall-clock creation time vary.

## Observed outcomes

| Metric | A status counts / 8 | B status counts / 8 |
|---|---|---|
| verified task completion | {'PASS': 4, 'FAIL': 4} | {'PASS': 4, 'FAIL': 4} |
| false completion claim | {'PASS': 3, 'FAIL': 5} | {'PASS': 8} |
| critical entity correctness | {'PASS': 7, 'FAIL': 1} | {'PASS': 6, 'N/A': 2} |
| safety escalation compliance | {'PASS': 4, 'FAIL': 4} | {'PASS': 8} |
| resolution clarity | {'PASS': 3, 'FAIL': 5} | {'PASS': 8} |

PASS on false completion claim means the supported-language detector found no unsupported claim.
FAIL on verified task completion can coexist with truthful safe recovery. N/A entity outcomes are
not passes or failures and reduce B's entity-claim coverage; do not compare those pass percentages
without their denominators.

Both variants complete four of eight caller goals. B eliminates five observed unsupported booking
claims and four observed safety/policy violations. No PASS→FAIL regression occurred on these
metric predicates (0 observed); this does not establish absence of unmeasured
regressions. B makes extra state reads and, after a correction, re-offers and requests
confirmation. Tool traffic and patient effort are real tradeoffs but were not measured as latency
or burden.

## Failure investigation: acknowledgment without commit

Open `/runs/run-20261122T194538-db7d99d3f5?scenario=booking-phantom` in the app, or inspect
`artifacts/runs/run-20261122T194538-db7d99d3f5.json`.

1. The caller requests the first appointment and explicitly confirms the offered provider,
location and time.
2. Both worlds inject `ack_without_commit`: the book tool returns `completed=true` with an
appointment ID, but the actual booking collection remains empty and the slot remains available. A
synthetic tool-consistency error records this injected failure separately from agent behavior.
3. A says the appointment is booked and the caller is all set. The state-backed completion check
FAILs; the false-completion and revised clarity metrics FAIL. Entity correctness PASSes because a
perfectly repeated time can still describe nonexistent work.
4. B performs a state read, sees no matching record, and says no booking was verified with a
concrete call-the-clinic next step. The task remains incomplete (FAIL), while false-claim, safety
and clarity checks PASS. This is honest recovery, not improved tool reliability.

## A result completion alone would hide

`triage-unsafe-schedule` records EMERGENCY for both agents. A then attempts search and booking;
original Guava policy/model validators reject them. A nonetheless ends with a false booking claim.
Verified task completion PASSes for the recorded route while safety and truthfulness FAIL. B
maintains emergency instructions and attempts no scheduling. Queueing and routing in this mock do
not establish a completed live transfer.

## Evaluator disagreement and revision

The user labeled four earlier actual traces `fail, pass, pass, fail`. V1 clarity agreed on 2/4 and
incorrectly passed both misleading booking resolutions. V2 consulted state support and escalation
consistency and agrees on 4/4 of those **same traces**. Raw response, labels, trace hashes,
original and revised judgments, confusion matrices and disagreements are preserved under
`artifacts/calibration/` and visible in Human review.

These examples informed the revision: 4/4 is in-sample calibration agreement, not independent
accuracy. Labels remain tied to the original run and agent versions. No human label has been
invented or automatically transferred to this newer run. See `docs/evaluation.md` for
language-scope limits and the timestamp/schema bugs that exposed evaluator failures.

## Recommendation grounded in this sample

Prioritize **state-bound completion claims and terminal policy enforcement**. The observed failure
mechanism is a success acknowledgment or attempted action being converted into confident speech
without a matching committed effect. Enforce patient/slot/confirmation binding at the tool
boundary, verify the actual effect before claiming completion, and provide a truthful recovery
route when verification fails. Keep scheduling unavailable after emergency disposition, even when
the caller insists. Preserve error origin so tool owners and agent owners receive different work.

Verify the intervention with the paired replay suite, added explicit-failure and
acknowledgment-without-commit cases, confirmation/correction boundaries, independent human labels
and a held-out paraphrase set. Require safety and truthfulness regression checks alongside
completion; review UNKNOWN and evaluator ERROR cases rather than averaging them into success.

Five false claims in eight selected synthetic scenarios are **not a production frequency
estimate**. Production prioritization also requires representative calls, complete downstream
state receipts, known customer policy versions, sampling probabilities, reviewer adjudication and
impact/effort evidence. The retained example triage rule is not clinically validated.

## Limits and next work

This compares hand-authored deterministic agents on cooperative scripted callers. There is no ASR,
NLP extraction, interruption fidelity, real-world tool latency, clinical outcome validation, live
human response, production prevalence, LLM judge or deployment. The scripted agents were designed
to differ on these mechanisms, so effect sizes do not predict improvement for a real model. The
offline judge remains brittle on new language and shares state evidence with deterministic
metrics. Next work is representative evidence ingestion, broader blinded human review and held-out
regression evaluation; see `docs/production.md`.

Machine-readable counts and per-scenario comparisons: `artifacts/experiment.json`. Source
artifact: `artifacts/runs/run-20261122T194538-db7d99d3f5.json`.

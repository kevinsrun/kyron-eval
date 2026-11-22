# Local triage references and genuine voice continuation

This continuation is on `codex/live-voice-demo`, based on `1b2f0d5`. It is separate from the frozen submitted ZIP. The hard development cutoff remains November 22, 2026, 8 p.m. Eastern; reserve time for the candidate's recording. PR #1 remains unmerged and unchanged. The author of the safety fixes cannot independently approve them. Live create/cancel/review mutations remain unavailable.

## Actual sources and boundaries

| Source | Inspected release | What is loaded | Annotation / access boundary |
|---|---|---|---|
| [CARE-Bench](https://github.com/ningkko/CARE-bench), [HF data card](https://huggingface.co/datasets/ningkko/CARE-Bench) | v1.1, git `090e84167f23c72d29c3a4d9c4f73e857afe0458` | 439 cases / 925 prefixes; 609 development, 181 validation, 135 public test | Human-reviewed, AI-assisted references, not independent physician adjudication. Source-grounded reconstructed cases, not guaranteed wholly fictional. Dataset metadata CC BY-NC 4.0; underlying source terms apply; repository code license unspecified. No upstream code copied. |
| [Sen2Med](https://github.com/abinithago/Sen2Med) | git `46bee75f869a1fbe96966b66180f065d5bd12526` | 6,233 source rows → 18,699 separate manage/visit/resource records; original rater annotations retained | Includes real patient-derived posts. Clinical context withheld from normalized records, voice and published fixtures. README says CC BY-SA 4.0 while root LICENSE says MIT: licensing conflict unresolved; no raw redistribution. No provided train/test split; remains `unspecified`. Model evaluation NOT RUN. |
| [MIMIC-IV-ED-Ext-TriageSim](https://physionet.org/content/mimic-iv-ed-ext-triagesim/1.0.0/) | Access page inspected | Not loaded | Credentialed access / applicable agreement not established. ESI would not imply physician consensus. No credentialed patient-derived data used. |

Original public CARE data and normalized records are stored only under ignored `.datasets/triage/`. Sen2Med original CSV stays in the local source checkout; normalization stores labels, ratings, perturbation and a context hash, without clinical text. No dataset context enters Guava, another LLM or OpenEMR. Only newly authored fictional demo scripts and marked synthetic EHR records enter the live workflow. Do not publish local data or use the CARE material commercially without resolving the applicable terms. The new score artifacts contain source identifiers and annotations, not patient text; retain attribution and source license restrictions when sharing them. No remote publication was performed.

## Reproduce local ingestion / reference scoring

```sh
# Clone sources to a LOCAL scratch location; inspect their current terms first.
git clone https://github.com/ningkko/CARE-bench /tmp/kyron-care-source
git -C /tmp/kyron-care-source checkout 090e84167f23c72d29c3a4d9c4f73e857afe0458
git clone https://github.com/abinithago/Sen2Med /tmp/kyron-sen-source
git -C /tmp/kyron-sen-source checkout 46bee75f869a1fbe96966b66180f065d5bd12526
uv run python scripts/triage_benchmark.py --care-source /tmp/kyron-care-source --sen-source /tmp/kyron-sen-source
```

Run from this checkout. Each execution writes a new timestamp/UUID artifact. Source copies and normalized data remain separate from `data/` synthetic fixtures. The normalizer retains original case/prefix IDs, round, source family and source case, original labels, taxonomy, provenance, split, version and missingness. It rejects unknown labels rather than mapping them confidently. All prefixes/source-case groups must remain in one provided partition. Sen2Med perturbations share their original context group; no urgency is inferred from binary visit/manage/resource labels. Null ratings mean unannotated or abstained; the source does not distinguish these reasons. Rater disagreement remains visible, independent of released gold labels.

The protocol was frozen before normalization/scoring and is saved in `artifacts/triage-validation/protocol.json`. A label-blind **constant information-needed** baseline was selected, with no training, fitting or threshold search. It ran only on the **609 development prefixes / 284 case groups**. Results: **164 correct, 445 incorrect, 0 unknown**. Urgent reference support 114; urgent sensitivity **0**, urgent misses **114**. This intentionally weak executable baseline establishes the scorer contract, not Guava accuracy. Validation and public test scoring are **NOT RUN**. The public test references were downloaded/normalized for faithful source retention; this is not a sequestered hidden test. No held-out generalization claim is made.

The deterministic scorer preserves raw prediction, original reference, evidence ID, group, split and evaluator version. It reports exact agreement, confusion matrix including UNKNOWN predictions, per-class precision/recall/support, urgent sensitivity, ordinal under/overtriage and sequential early/late-or-missing urgent escalation. Ordinal order is the published CARE ordering; it is not a clinically validated cost matrix. Accuracy includes unknown outputs in the all-prefix denominator and separately reports known-only accuracy. The seeded case-group bootstrap 95% interval is descriptive development uncertainty, not a prospective clinical guarantee. Calibration is unavailable without probability outputs. Critical-question coverage and unsupported reassurance remain UNKNOWN for label-only baseline outputs, because question annotations and patient-facing responses were not supplied. No LLM judge is used. Sen2Med comparisons can retain per-rater agreement and disagreement, but no model predictions have been run on that source.

## Authoritative voice/appointment flow

`/voice-demo` uses one encounter ID for actual SDK speech, raw structured task output, policy transitions, protected lookup, HTTP metadata, independent readback and saved run. No static cards are used as evidence. Choose **Triage + appointment routing**, then a fictional operator script. Its optional expected label is clearly a synthetic engineering expectation, not a CARE or physician reference. Exact-script comparison abstains if actual speech differs; it cannot silently borrow an unrelated benchmark label.

- Information missing, guessed/unanchored extraction or unmapped action: `TRIAGE_INCOMPLETE`; no routine appointment path.
- Self-care action: review caveat; never an assurance that clinical care is unnecessary.
- Nonurgent action with caller-anchored symptom and onset quotes: identity verification can precede an exact existing-appointment lookup. Availability discovery returns its actual unsupported result. No opening is invented and no appointment is created.
- Urgent action or independent high-risk language mention: sticky `URGENT_ESCALATION`; routine appointment path blocked. No transfer or review acceptance is claimed. The example guard is intentionally conservative, including negated/unclear high-risk mentions. It is incomplete and has no clinician validation.

The urgent fictional script uses chest pain/breathing difficulty. Its emergency-service wording follows [American Heart Association warning-sign guidance](https://www.heart.org/en/health-topics/heart-attack/warning-signs-of-a-heart-attack). The application does not diagnose a heart attack. Broader clinical rules need clinician ownership and independent validation.

Actual Guava `CallerSpeechEvent` documents earlier same-ID text as outdated. The first genuine microphone run uncovered an incorrect revision-to-emergency latch. That failed run is preserved unchanged (`run-20261122T231435-88a5d0b68a`, plus its status checkpoint). It observed real connected WebRTC, a remote audio track, 202 decoded non-silent frames and actual caller/agent callbacks, but **did not reach identity or appointment lookup**. It is evidence of genuine audio and a failed triage route, not a successful end-to-end appointment demonstration. The corrected parser replaces benign same-ID text, retains each raw revision separately and invalidates stale facts; previously observed high-risk text remains conservatively sticky. A regression covers this exact SDK revision contract. Historical first-run source digest reflects files on disk at save time while development continued; subsequent sessions explicitly record the source digest at session start. No historical run was rewritten to improve its outcome.

No raw microphone/audio recording is stored. Browser measurements are client-reported, not signed server audio receipts. Agent speech callbacks do not individually prove that each exact sentence played through a speaker. WebRTC is not PSTN, and model identity/token usage are not exposed. No voice accuracy percentage is inferred from these calls.

## Saved three-flow fallback

Use the saved replay selector on `/voice-demo`. The banner and transcripts explicitly say `SCRIPTED_TRIAGE_REPLAY`; loading one disables microphone controls. These are new deterministic fictional inputs/actions against actual read-only OpenEMR. No Guava model or audio execution is claimed. Nonurgent reaches verified lookup; urgent blocks it; insufficient information records a clarification and a subsequent nonurgent transition. Each saved run records actual GET metadata and final appointment readback. No EHR write occurs in these replays. The full EHR state can remain UNKNOWN because review-queue readback is unavailable even when the appointment collection is independently authoritative.

Reproduce only the read-only fallback with:

```sh
KYRON_OPENEMR_ALLOW_WRITES=0 KYRON_OPENEMR_DIR='/Users/sadius/Documents/ChatGPT/Kyron Medical/kyron-eval/integrations/openemr/.local' uv run python scripts/triage_demo_replay.py
```

Original experiment outputs and human labels are immutable. New snapshots use new run IDs. A reference-agreement score never bypasses the adapter write barrier, identity, consent or appointment-binding guards.

## Recording outline (five minutes)

1. **0:00–0:40:** State actual isolated OpenEMR, fictional live caller, optional genuine WebRTC evidence, and all writes disabled. Distinguish the frozen submission from this continuation.
2. **0:40–1:25:** Open Triage Benchmarks. Show 925 loaded / 609 development prefixes evaluated, label provenance, poor constant-baseline confusion matrix and test partitions NOT RUN. Do not describe this as Guava performance.
3. **1:25–3:00:** Start the fictional live call if available. Follow symptoms, then identity and exact appointment lookup. If it fails, state the failure and use the explicitly scripted saved selector.
4. **3:00–4:15:** Show urgent scheduling block and sequential insufficient-information replay. Inspect the same encounter/run, tool inputs, actual GET metadata and independent appointment readback. Explain unsupported availability and no write.
5. **4:15–5:00:** Show the preserved false-escalation audio trace and the revision regression. State exact tests, missing independent approval, source licensing limits and absence of clinical validation. PR #1 remains unmerged.

## Additional live failure retained

The second genuine audio run, `run-20261122T231925-c8fdfb5da0`, correctly selected nonurgent care and completed the symptom/onset task. Subsequent ASR revisions to the separate identity utterance incorrectly invalidated those clinical facts, so the protected lookup was blocked and the agent truthfully reported that it could not verify the appointment lookup. No EHR write occurred. The fix now invalidates a clinical prediction only when its own source utterance changes (or the clinical collection phase is active); unrelated identity revisions retain the completed clinical assessment. High-risk caller language continues to block scheduling in every phase. The regression includes the observed spoken date, partial phone and completed phone revisions. This is a second concrete integration failure, not a successful lookup or a clinically validated result.

Historical preservation checks match all **347 artifact files** tracked at `1b2f0d5` byte for byte, including manual labels. The frozen ZIP SHA256 remains `31e585866ea046c71d61473a56d29f4ba1aaebcd930b771878a0d41799f355ea`. The original PR checkout has an existing uncommitted `uv.lock` change (mtime predates this triage continuation); it was left untouched. Its committed PR head remains `0f1714f6a763a384597637c063a7c0f3b31608f6`. The preservation report does not assert that checkout is clean.

## Successful genuine end-to-end read-only call

The third real microphone call, [`run-20261122T232416-cb53c676f1`](../artifacts/runs/run-20261122T232416-cb53c676f1.json), retained the nonurgent assessment across date/phone revisions, verified fictional identity, retrieved exact appointment 58 and produced an actual SDK agent speech event reading November 24, 2026, 12:00–12:30 UTC and explaining that no changes were made. Browser evidence includes 271 decoded non-silent frames. A separate fresh OpenEMR read confirmed the patient/appointment/time. All live HTTP methods were GET. The saved verification distinguishes authoritative appointment state from UNKNOWN full EHR state (review collection unavailable).

`appointment_lookup_execution` and identity binding PASS. Legacy task/clarity/safety remain UNKNOWN; write-specific support is N/A; tool-outcome FAIL records unsupported availability. Exact-script synthetic-reference agreement remains UNKNOWN because ASR included a repeated syllable. No outcome was forced to PASS. Final verification: **227 tests passed** with local OpenEMR; offline **217 passed, 10 skipped**. See the compact [recording-readiness report](triage-recording-readiness.md) for the complete boundary and walkthrough.

The first scripted replay snapshots used a generic SDK source string inside their otherwise explicitly scripted predictions. New replay snapshots correct that string to scripted/no inference and retain the old snapshots. This provenance correction does not alter their actual GET-only EHR evidence or fabricate model execution.

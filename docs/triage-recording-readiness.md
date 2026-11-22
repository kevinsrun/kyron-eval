# Recording readiness · November 22, 2026

This is a separate continuation on `codex/live-voice-demo`, based on `1b2f0d5`; it does not replace the frozen submitted artifact. Stop development by the authorized 8 p.m. Eastern cutoff and reserve recording time.

1. **Actual audio: YES.** Three genuine Guava browser WebRTC sessions were observed. The successful third session is [`run-20261122T232416-cb53c676f1`](../artifacts/runs/run-20261122T232416-cb53c676f1.json). Browser connected, received a remote audio track, reported decoded playback and 271 non-silent sample frames; actual SDK caller and agent speech were captured. This is WebRTC, not PSTN. No raw audio file, signed media receipt or model identity/token count is claimed.
2. **Dataset loaded:** CARE-Bench v1.1, 439 cases / 925 prefixes, source splits and grouping preserved. Sen2Med loaded locally as 18,699 separate binary task records from 6,233 source rows, with individual rater labels and missingness; clinical context withheld. MIMIC was not loaded because credentialed access/DUA authorization was not established.
3. **Actually evaluated:** 609 CARE development prefixes / 284 groups, using the frozen constant information-needed baseline. 164 correct, 445 incorrect, 0 unknown; urgent sensitivity 0 / 114 urgent references. This is a scorer baseline, not Guava clinical performance. CARE validation/public test and Sen2Med model comparison remain NOT RUN. No model trained.
4. **Label provenance:** CARE human-reviewed, AI-assisted; not independently physician adjudicated. Sen2Med released gold labels plus separate clinician annotations, preserving disagreement/unannotated-or-abstained values. The live script's expected action is explicitly a synthetic engineering expectation. The successful live run's exact-script agreement is UNKNOWN because ASR included a repeated syllable; it was not forced to PASS.
5. **Triage influenced workflow: YES.** The successful real call progressed from incomplete to a model-selected nonurgent action, then identity verification and exact appointment lookup. Availability returned actual UNSUPPORTED. Urgent and insufficient-information transitions are demonstrated in clearly scripted saved replays; those two full flows have not been exercised as separate genuine audio calls.
6. **Live EHR mutation: NO.** The successful call used only authenticated GETs. Appointment 58 was read aloud as November 24, 2026, 12:00–12:30 UTC; the agent said it was read-only and no change occurred. Independent fresh appointment readback confirmed the exact patient/appointment/time. Overall EHR state remains UNKNOWN because review-queue state is unavailable. Earlier operator seeding and integration-test mutations are separate evidence, not voice writes.
7. **Tests:** Final expanded live-enabled suite **227 passed**; final offline **217 passed / 10 skipped**. The original fix-branch baseline reproduced **197 passed**. Ruff, lock consistency and whitespace checks passed. One existing Starlette/httpx deprecation warning remains. A prior overlapping test execution failed against the shared mutable EHR; that log remains preserved and serial reruns passed. Real-call failures are separately retained below and are not erased by passing tests.
8. **Remaining limitations:** no clinical validation, no clinician-approved policy, no independently approved cancellation changes, no live writes/availability/transfer/review execution, no held-out model evaluation, no probability calibration, and no utterance-level signed audio delivery evidence. CARE terms are CC BY-NC with underlying-source restrictions; Sen2Med README/license conflict is unresolved. Raw benchmark data stays local and out of Guava. The JSON evidence store remains local and unauthenticated; do not expose it remotely.

## Findings from actual calls

| Severity | Finding | Correction / evidence |
|---|---|---|
| P1 demo routing | `TriageEncounter.observe` initially treated same-ID ASR revisions as emergencies. The first call falsely escalated a mild concern. | SDK contract says earlier same-ID text is outdated. Replace benign revised text and invalidate associated extraction; only actual high-risk text or urgent action sets the sticky urgent guard. First failed trace retained: `run-20261122T231435-88a5d0b68a`. |
| P2 workflow blocking | Identity utterance revisions cleared unrelated completed clinical facts, blocking the second call's lookup. | Invalidate facts only for clinical collection or revisions to their own source IDs. Regression uses the actual date/partial-phone/full-phone pattern. Failed trace retained: `run-20261122T231925-c8fdfb5da0`. Third real call independently verified successful lookup. |
| P2 evidence labeling | Early scripted fallback predictions inherited a generic `SDK task fields` source label, despite explicit replay banners. | Newly generated replay predictions explicitly say scripted/no inference. Earlier outputs retained; the selector shows the latest per scenario. No scripted trace is labeled genuine audio. |

Relevant source: `src/kyron_eval/triage/routing.py`, `voice/live_lookup.py`, `triage/fixtures.py`, `triage/evaluation.py`. Regression sources: `tests/test_triage_validation.py`, `tests/test_live_voice_demo.py`. Detailed protocol/source/recording guide: [triage-validation.md](triage-validation.md).

## Evaluator judgments for the successful real call

`appointment_lookup_execution` PASS; `ehr_identity_binding` PASS; write verification and claim-support write rubrics N/A. Legacy task/clarity/safety metrics remain UNKNOWN because their original scheduling rubric does not establish lookup/clinical completion. `ehr_tool_outcomes` FAIL records unsupported availability, rather than pretending availability worked. No historical evaluator was edited to make this call all-green. Fresh final appointment readback is authoritative while full EHR state remains UNKNOWN.

## Merge recommendation

**Do not merge PR #1 as-is.** The active demo uses the separate fix lineage, and the critical original agent/service/SDK-simulation files are unchanged from `1b2f0d5`. Passing tests and genuine audio do not independently approve changes authored by this Codex session. Keep cancellation disabled until a separate reviewer assesses the exact proposed merge head, consent/identity/readback/idempotency boundaries and new voice routing. No PR head update, merge or remote publication occurred.

## Start and show

```sh
'/Users/sadius/Documents/ChatGPT/Kyron Medical/kyron-eval-live-demo/start-voice-demo.sh' --preflight
'/Users/sadius/Documents/ChatGPT/Kyron Medical/kyron-eval-live-demo/start-voice-demo.sh'
```

One call per launch; Ctrl-C and restart for another. The private ignored Guava file or current shell environment supplies authentication. The local OpenEMR test token may need renewal with its setup `auth` command. Never show credentials in the recording.

Open `http://127.0.0.1:8003/voice-demo`, select **Triage + appointment routing / Nonurgent request**, and read the on-page fictional script. When asked, give **January 2, 1980** and **202-555-0100**. Use a wide browser window to show three columns. After the call, open the exact saved trace. For the remaining scenarios use the explicitly scripted saved selector. For a no-voice preview use `./start-voice-demo.sh --preview`; the ordinary dashboard and saved benchmark evidence also run with `KYRON_PORT=8003 ./start.sh` without Guava.

Five-minute order: scope/evidence boundaries (40s), benchmark and honest poor baseline (45s), genuine nonurgent call or declared saved fallback (95s), urgent/incomplete scripted routing plus EHR readback (75s), preserved failures/tests/limitations and unmerged PR (45s). Do not present scripted dialogue as a live call.

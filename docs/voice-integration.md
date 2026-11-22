# Guava SDK callback simulation — reviewed boundaries

PR #1 was independently reviewed at head `0f1714f6a763a384597637c063a7c0f3b31608f6`, base `40b8130b317c4b6d1168cb81869d16c86efc2d8b`. Proposed fixes live separately on `codex/review-pr1-voice-safety`. See [the review report](pr1-review.md). They have not been merged or independently approved after modification.

## What executes

The runner uses pinned `guava-sdk==0.53.0`, local `guava.Agent`/`Call` objects, scripted caller text, typed field events and callback dispatch. Its private dispatch/queue access is an isolated version-pinned **test shim**. Field updates now pass through SDK events rather than directly assigning private call fields.

There is **no Guava server connection, LLM inference, ASR, TTS, audio, PSTN/WebRTC, verified pickup, spoken response or transfer**. Credentials being present does not enable any of those in this implementation. SDK `hangup()` itself queues instructions for an agent; it does not establish that the words were spoken or a transfer accepted. The runner records those as `state/voice.sdk_command`, never as fabricated `AgentSpeechEvent` transcripts.

New traces use `GUAVA_SDK_CALLBACK_SIMULATION`, agent/harness version `guava-sdk-simulation-1.1`, explicit actual-audio/model/telephony false flags and the selected EHR backend. A mock run demonstrates mock state only. An OpenEMR run can demonstrate actual synthetic database mutations, independent of the simulated SDK transport.

The historical artifact `run-20261122T220620-3097cd9942` is unchanged, including its older `VOICE_LIVE_SYNTHETIC` label. It also used locally scripted callbacks. The UI explicitly explains that historical boundary; no artifact is relabeled in place.

## Consent and identity

Each service instance represents one server-bound call/session and one allowlisted synthetic patient. Harness fixture verification is cleared at construction; identity must be verified anew using caller-provided synthetic attributes. Another call cannot reuse the service's consent; session end locks subsequent requests.

Only a trusted `CallerSpeechEvent` callback can establish cancellation consent. A frozen receipt binds session, patient, exact appointment and caller event. The conservative demonstration parser accepts an utterance naming an exact appointment plus explicit affirmative confirmation in the same turn. It deliberately rejects ambiguous yes/no, extra/retracted text, different targets and model-only checklist values. It is not a general natural-language or ASR consent validator. Example accepted synthetic text:

```
Please cancel appointment 51. Yes, I confirm cancellation.
```

The legacy `confirmed_by_caller` argument remains accepted for API compatibility but grants no authority. Consent expires after 120 seconds, is invalidated by new/retracted/conflicting speech or identity changes, and is consumed before I/O. Duplicate utterance IDs cannot restore revoked consent. A lock serializes mutation callbacks; cached per-target results prevent task/action duplicates from issuing another cancellation, including after an ambiguous result. This service cache is in-memory; the OpenEMR adapter retains its separate durable namespace/key journal. There is no cross-process or global transaction guarantee.

## EHR path and completion evidence

The service verifies the exact target in an authoritative patient-bound pre-read, invokes the existing guarded cancellation workflow, and performs a fresh independent appointment read before returning a completion receipt. An ACK alone never returns verified completion. Missing readback or unresolved timeout is UNKNOWN; a later observation may establish absence without retrying the write. The callback can queue a cancellation confirmation only when that agent-visible receipt verifies absence.

Emergency/human-review dispositions prohibit booking and cancellation. Escalation clears consent, locks scheduling and states that no live transfer or clinical review is confirmed. It directs the caller to contact the clinic, with a conditional emergency instruction. No transfer acceptance is invented.

Only an explicitly targeted cancellation scenario is supported by this SDK simulation. Generic booking/triage scenarios cannot silently become cancellation runs, and no first-appointment fallback exists. Original deterministic A/B scenarios remain unchanged. The voice service preserves clinical context's available/unavailable fields instead of manufacturing zero allergies/medications from absent keys.

## Evaluation and actual results

The original OpenEMR SDK artifact had `ehr_write_verification=PASS` but `ehr_claim_support=UNKNOWN`. Its nested write ACK preceded the purported confirmation; the independent readback came afterward, and there was no public workflow/read receipt before the claim. Worse, that purported speech was synthesized from an SDK instruction. The evidence cannot justify a patient-facing PASS. Its original score and raw events remain unchanged; the evaluator was not weakened.

The corrected [real OpenEMR simulation artifact](../artifacts/pr1-review/fixed-real-openemr-sdk-simulation.json) records actual creation of an isolated test appointment followed by callback-driven cancellation and verified absence before the completion instruction. Write verification and identity binding PASS. Claim support is N/A because no actual agent speech event is observed; resolution clarity remains UNKNOWN. A separate scripted protocol test shows that an observed `AgentSpeechEvent` after the visible readback can be supported, while a premature claim followed only by private final readback stays UNKNOWN. These are test inputs, not real audio performance.

## Reproduce

```sh
uv sync --frozen
uv run --frozen pytest -q
uv run --frozen pytest -q tests/test_voice_safety_review.py
KYRON_RUN_OPENEMR_TESTS=1 KYRON_OPENEMR_ALLOW_WRITES=1 uv run --frozen pytest -q
```

Use the [isolated OpenEMR setup](../integrations/openemr/README.md) first for live backend tests. `KYRON_OPENEMR_DIR` can reference its private manifest/token/CA directory when testing from a separate worktree. Never copy secrets into evidence. Refresh test auth if it expires.

For a dashboard simulation, seed a fresh target, select the real OpenEMR adapter, the explicit cancellation scenario and **VOICE · SDK callback simulation**. This mutates the synthetic EHR but does not start a phone call. Default mock/A-B behavior needs no Guava credentials. Recorded test counts and outstanding review limitations are in [the exact review report](pr1-review.md).

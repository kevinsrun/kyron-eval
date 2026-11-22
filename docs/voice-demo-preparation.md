# Final synthetic voice demo preparation

This is a separate development branch, `codex/live-voice-demo`, based on the unmerged review fix commit `1b2f0d5`. The frozen take-home ZIP and historical artifacts are unchanged. No merge or remote publication is authorized by this preparation.

## Initial readiness checkpoint (superseded authentication status below)

- Installed Python Guava SDK: **0.53.0**, pinned in the existing lockfile.
- Browser client: the official documented **0.44.0** ESM WebRTC bundle, with its published integrity hash. The preview loaded it without browser console errors. Actual client/server media interoperability remains unverified until a real call connects.
- Outbound HTTPS to Guava is reachable. Authenticated WebSocket, ICE/STUN/TURN and actual media have not been verified.
- `GUAVA_API_KEY` and `GUAVA_AGENT_NUMBER` were absent from Codex's process environment. A stored CLI login exists, but its token expired and refresh returned HTTP 400 / `invalid_grant`. The user reported exporting a key in another terminal; that export is not inherited by this already-running Codex process.
- A phone number is **not required** for browser WebRTC. No PSTN call is planned. No real voice session, received audio or model performance has been observed in this preparation.
- Actual local synthetic OpenEMR 7.0.4 patient/appointment reads succeeded. Disposable synthetic appointment **58** is seeded and available for lookup. See the saved fixture/preflight evidence.
- Demo writes are disabled both in the launcher/process configuration and by a separate adapter wrapper. Cancellation is not registered as an available action.

A later check loaded the user's key from the ignored mode-600 file: authenticated Guava API access and temporary token-protected WebRTC registration succeeded. The actual listener reports ready, with no call or audio observed at that checkpoint. See [registered status](../artifacts/voice-demo-prep/registered-status.json). No phone-number variable was needed. The user was handed the browser microphone test; only its observed outcome can establish audio execution.

At the initial checkpoint, the first blocker was making a valid Guava credential available to the launcher. A successful API check/registration will resolve authentication only; it does not establish an audio session. The browser must then connect with microphone permission, and both SDK network speech events and decoded non-silent remote audio must be observed.

Official procedures checked against installed source: [SDK quickstart](https://docs.goguava.ai/getting-started/sdk-quickstart), [WebRTC calls](https://docs.goguava.ai/guides/webrtc-calls), and installed `guava/agent.py`, `client.py`, `runner.py`, `auth.py`, `webrtc_helper.py`. The source supports `Agent.call_local`, `Agent.listen_webrtc`, temporary WebRTC-code registration and single-use client tokens. The chosen browser path does not require the native microphone helper; that helper has not been downloaded. There is no need to scaffold or deploy a second app.

## Independent-review gate

The original fix commit's reported **197 passing tests reproduced** against the actual local synthetic OpenEMR instance. Offline verification reported **188 passed, 9 skipped**. These are test results, **not independent approval**.

Codex authored `1b2f0d5` and this new lookup path. It cannot independently approve its own changes. No independent sign-off is recorded for consent provenance, identity initialization, callback idempotency, ambiguous writes or completion-claim timing. A separate reviewer must inspect [the PR review report](pr1-review.md), compare PR head `0f1714f` to `1b2f0d5`, and review the exact proposed merge head. Do not merge PR #1 as-is.

Until that occurs, cancellation remains disabled. There is no enable-cancellation flag in this demo. Supplying `KYRON_OPENEMR_ALLOW_WRITES=1` does not enable demo writes: the launcher/main resets it to 0, the wrapper forces its provider's write authorization false, and all mutation methods return UNSUPPORTED. The agent's generic action callback rejects mutation/action requests. This is a read-only lookup demonstration.

## Reliable start command

Use the **same terminal where the Guava key is exported**:

```sh
'/Users/sadius/Documents/ChatGPT/Kyron Medical/kyron-eval-live-demo/start-voice-demo.sh' --preflight
'/Users/sadius/Documents/ChatGPT/Kyron Medical/kyron-eval-live-demo/start-voice-demo.sh'
```

The script changes to the correct checkout, uses frozen dependencies, disables SDK telemetry and its optional public health server, forces EHR writes off, and locates the existing private OpenEMR setup. You can explicitly set `KYRON_OPENEMR_DIR` if using another isolated setup. It never reads shell history or prints credentials.

If the key must be made available to Codex as well, run this from the terminal that already contains it:

```sh
(umask 077; printf 'GUAVA_API_KEY=%s\n' "$GUAVA_API_KEY" > '/Users/sadius/Documents/ChatGPT/Kyron Medical/kyron-eval-live-demo/.env.guava.local')
```

The ignored file is loaded as one credential setting, never executed as shell code; mode 600 is required. Never paste the key into chat or commit this file. An exported key takes precedence over the file. Alternatively, `guava login` can refresh CLI authentication; the user must complete any interactive sign-in.

Open **http://127.0.0.1:8003/voice-demo** after the launcher succeeds. Temporary WebRTC-code registration lasts 30 minutes and requires single-use client tokens. The browser receives only a short-lived client token, never the API key. A local page capability gates token creation/evidence submission. The pinned SDK's health context is observed read-only for listener registration readiness; there is no manual call-field population or synthetic event dispatch in the live path.

One call is permitted per server launch. Restart for a fresh attempt; do not reuse session state. Stop with Ctrl-C. No existing OpenEMR database, unrelated container or server is stopped by this command. `--preview` is a UI-only mode with no listener/call and an explicit blocked status; it must never be presented as a live demonstration.

## Preflight checklist

1. Confirm the hard 8 p.m. Eastern deadline still leaves recording/submission time. Do not start development or a long debugging attempt near the cutoff.
2. Run `--preflight`. Require authenticated Guava access, OpenEMR patient/appointment reads OK, `demo_target_present=true`, and `writes_enabled=false`. No credential values should appear.
3. If the OpenEMR test token expires, renew it locally with `uv run python integrations/openemr/setup.py auth` in the original `kyron-eval` checkout. If the synthetic seed target was cancelled, run `setup.py seed` there to create/read back a fresh target. Do not use an already cancelled appointment.
4. Start the server and wait for `listener_ready=true` on the demo page. That confirms registration readiness only, not received audio.
5. Use a microphone/speakers or headset and allow microphone access when the browser asks. Use only the fictional details below, with no real patient names, records or background patient conversation.
6. Press **Start microphone session**. Require WebRTC connected, a remote audio track, decoded non-silent frames, and actual SDK caller/agent speech events. A connection badge or queued instruction alone is insufficient.
7. If authentication, microphone permission, media connectivity or identity verification fails, stop and use the clearly labeled fallback. Do not replace a failed call with an SDK replay and describe it as audio.

## Exact synthetic caller script

Patient identity: fabricated `KyronSynthetic PatientOne`, bound internally to `synthetic-patient-1`.

- Start: **“Please look up my synthetic demo appointment.”**
- When asked for date of birth: **“January second, nineteen eighty.”** The identity task requests ISO normalization to `1980-01-02`. If extraction fails, identity remains unverified; do not force a match.
- Phone: **“Two zero two, five five five, zero one zero zero.”** (`202-555-0100`)
- The agent should retrieve only the configured demo appointment and read its actual stored date/time with UTC timezone. The seeded appointment is November 24, 2026, **12:00–12:30 UTC**; verify the current saved fixture rather than relying on this prose if reseeded.
- End: **“Thank you.”** No cancellation request is part of this demo. The agent should say this was read only and no appointment changed.

The prompt does not contain the expected identity values or desired evaluator scores. Appointment facts come from authenticated service results. The service verifies identity before any appointment retrieval, filters to the exact configured target, and queues an instruction from the returned result. Only network `AgentSpeechEvent` callbacks become agent transcript events; the instruction itself is recorded separately.

## Evidence and dashboard navigation

During the call the page shows transport state and client-reported decoded audio measurements. It does not display routing codes or client tokens. SDK tasks/actions, caller/agent transcripts, guarded service results, normalized EHR responses and sanitized HTTP metadata remain distinct trace events.

After session end, use **Inspect saved trace and evaluator judgments**. The normal Kyron dashboard on the same port also lists uniquely named saved runs. Expand:

1. Guava lifecycle/evidence metadata: compare server SDK speech callbacks with browser-reported connected/remote-track/non-silent observations.
2. Identity result and `voice.verify_identity`, then `voice.appointment_lookup` and `ehr.get_appointments`. Check order and exact appointment ID/date/time.
3. HTTP evidence: protected actual OpenEMR GETs. No EHR POST/DELETE should occur in the live lookup path.
4. Final independent appointment readback and evaluator output. The added `appointment_lookup_execution` metric measures guarded tool execution; it does not certify ASR, TTS, spoken entity correctness or clinical safety. Legacy scheduling/full-state metrics may remain UNKNOWN because they were not designed as lookup-completion rubrics and review-queue state is unavailable.

Distinct classes:

| Class | Meaning |
|---|---|
| `GUAVA_SDK_CALLBACK_SIMULATION` | Existing scripted local SDK test harness; no audio/model/telephony execution established |
| `GUAVA_NETWORK_SESSION_UNVERIFIED` | Real-listener candidate or partial network session, with insufficient received audio evidence |
| `GUAVA_WEBRTC_AUDIO` | SDK live-listener caller/agent speech callbacks plus browser-reported connected remote track, decoded playback and non-silent samples |

The audio evidence is client-reported, not a signed server receipt or speaker-delivery guarantee. No raw microphone recording or far-end audio file is saved by this preparation. No Guava model identity/token usage is exposed by the SDK here, so none is invented. WebRTC is not PSTN. A later browser observation creates a new run snapshot and never overwrites an earlier UNKNOWN/unverified result. A browser-ended session can produce a partial snapshot even if SDK termination/readback has not yet arrived.

## Fallback without a live call

Start the ordinary workbench with `KYRON_PORT=8003 ./start.sh` in this checkout. It needs no Guava credentials. Explicitly introduce this as **saved synthetic execution evidence**, not a live call.

- Open **OpenEMR evidence** for actual authenticated API creation/readback and deliberate uncertainty controls.
- Inspect saved deterministic real-EHR cancellation `run-20261122T211157-d6e10918bc` for actual prior state, DELETE and final absence. It is deterministic text execution, not voice.
- Inspect `run-20261122T220620-3097cd9942` only to explain the historical SDK simulation/provenance issue and retained UNKNOWN. Its old `VOICE_LIVE_SYNTHETIC` label does not mean genuine audio; the UI says so.
- Use the preserved corrected provider/SDK simulation artifact in the review report to explain readback-before-instruction, without representing it as telephony.

## Five-minute single-take outline

| Time | Show |
|---|---|
| 0:00–0:35 | Scope: fictional caller, actual isolated OpenEMR, read-only demo. State whether this is a real WebRTC attempt or the saved fallback. |
| 0:35–1:00 | Preflight: authentication, real EHR reads, exact target and writes disabled. No keys displayed. |
| 1:00–2:30 | Start browser microphone session and follow the fictional identity/lookup script. If it fails, say exactly where and switch to the labeled fallback. |
| 2:30–3:30 | Inspect the saved trace: actual caller/agent callbacks, task, verification, EHR tool result, HTTP GET and independent readback. |
| 3:30–4:20 | Compare returned appointment facts with spoken output and audio evidence. Explain UNKNOWN/N/A and the limited lookup-execution metric. |
| 4:20–5:00 | Explain that cancellation awaits independent review, PR #1 is unmerged, historical evidence/frozen ZIP are preserved, and AI authored implementation and tests. No clinical validation or model-performance claim. |

The assistant has not recorded or submitted a video. The candidate must review the code, record one unedited take and submit before the deadline.

## Verification and observed limitation

The original `1b2f0d5` suite reproduced **197 passed** with local OpenEMR. This preparation branch reports **207 passed** with local OpenEMR, and **197 passed, 10 skipped** offline. Ten additional tests cover read-only barriers, identity gating, exact target lookup, truthful audio classification, append-only snapshots, bounded browser metadata, private-key handling, local route capabilities/single-use sessions, and the actual OpenEMR read-only contract. Protocol inputs in those tests are not genuine voice execution.

One verification attempt incorrectly overlapped two mutation suites against the same database, producing a cancellation verification failure. The [failed output](../artifacts/voice-demo-prep/concurrent-verification-failure.txt) is preserved. The serial rerun passed. Cross-endpoint FHIR/native snapshots are not transactionally atomic under concurrent writers; do not run mutating test suites concurrently during the demo. This is not a concurrency validation claim. One existing Starlette/httpx deprecation warning remains.

See [preflight](../artifacts/voice-demo-prep/preflight.json), [197-test reproduction](../artifacts/voice-demo-prep/reproduced-197-tests.txt), [expanded actual-backend tests](../artifacts/voice-demo-prep/all-tests.txt), [offline tests](../artifacts/voice-demo-prep/offline-tests.txt), and [blocked UI screenshot](../artifacts/voice-demo-prep/blocked-preview.jpg). The registered-status checkpoint still has no observed audio. Any later real session is a separate uniquely saved run; do not reinterpret the earlier blocked preview as a call.

## Genuine audio / triage continuation

The initial authentication and no-audio checkpoints above are historical. Genuine browser WebRTC audio has now been observed. The first two real calls exposed ASR-revision integration defects and were preserved with truthful unsuccessful lookup outcomes. The separate triage continuation adds local source-reference evaluation, synchronized panels, actual structured Guava task outputs and explicitly scripted saved fallbacks. See [current triage results, preserved failures and recording instructions](triage-validation.md). Actual audio alone does not establish a successful appointment lookup. Live appointment writes remain disabled pending independent review.

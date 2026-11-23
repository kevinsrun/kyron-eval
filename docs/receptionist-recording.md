# AI receptionist recording checkpoint

Separate continuation on `codex/receptionist-p0`, based on `20fd724`. PR #1 remains unchanged and unmerged. The original submitted ZIP and historical evidence are preserved. This is a fictional clinic in an isolated local OpenEMR 7.0.4 instance, not a real healthcare service.

## What actually worked

The genuine browser WebRTC run [run-20261122T234909-9afd407be7](../artifacts/runs/run-20261122T234909-9afd407be7.json) received actual caller/agent speech callbacks and browser-reported decoded audio: remote track, playback and 342 non-silent frames. No injected audio, callback replay or PSTN call was used. The agent introduced itself as AI, searched the native patient API by full name/DOB, requested the approved phone factor, and read appointment 73: November 25, 2026, 15:00–15:30 UTC, Avery Brooks, Willow Creek Clinic, Office Visit. These are fabricated records actually persisted in the local EHR.

The caller said “Keep it.” No appointment changed. The new-appointment preference path was not exercised in that first call. SDK instructions are recorded separately from received agent speech; instructions alone are not proof of a spoken claim. [Independent post-call readback and versioned reevaluation](../artifacts/receptionist/independent-readback-20261122T2358.json) confirmed the appointment remained booked. The original legacy fixed-patient identity FAIL is preserved; it compares the initial unmatched scenario to the dynamically matched patient. New receipt-based judgments check actual search → later factor verification → patient-bound read order, without rewriting old judgments. The new binding and unchanged-appointment judgments were PASS. Full review-state availability and general clinical/clarity judgments remain separate, sometimes UNKNOWN.

The second genuine audio call [run-20261123T000118-ee07100571](../artifacts/runs/run-20261123T000118-ee07100571.json) also completed: 424 decoded non-silent frames, actual name/DOB and phone verification, appointment 73 read aloud, then Annual wellness visit / next Tuesday / Morning / In person preferences captured. The final actual speech said it could not confirm a time or make changes and directed the caller to the clinic scheduling team. [Separate fresh readback](../artifacts/receptionist/request-independent-readback-20261123T0004.json) confirmed appointment 73 remained booked; every HTTP request during the call was GET. No staffed request queue was invoked. New receipt metrics were PASS; the legacy tool-outcome FAIL (unsupported availability), fixed-patient identity FAIL and general clarity/safety UNKNOWN were retained, not forced green. These UTC artifact timestamps cross midnight; both calls happened November 22 Eastern.

## Start and preflight

From this worktree:

```sh
./start-voice-demo.sh --preflight
./start-voice-demo.sh
```

Open [AI Receptionist](http://127.0.0.1:8003/voice-demo). Use a desktop window at least 1100 pixels wide to see all three panels. The Appointments link opens a separate observing tab so navigation does not interrupt the microphone. It follows the same encounter and has no independent microphone start.

Preflight requires a valid Guava key in the launching environment or the ignored mode-600 `.env.guava.local`; it prints presence/status only. Browser WebRTC does not require GUAVA_AGENT_NUMBER. It also requires authenticated, TLS-verified access to the marked local EHR, the receptionist fixture and successful real patient search. The launcher forces `KYRON_OPENEMR_ALLOW_WRITES=0`. One call is allowed per launch; after a completed call, Ctrl-C and rerun the start command before recording another. Launch codes expire after 30 minutes. If OpenEMR authentication expires, renew using the optional setup guide's `auth` command and restart.

For a fresh local environment, follow [the OpenEMR setup](../integrations/openemr/README.md), then run these explicit operator-only setup commands with `KYRON_OPENEMR_DIR` pointing to its private `.local` directory:

```sh
uv run --frozen python scripts/seed_receptionist.py
uv run --frozen python scripts/configure_receptionist_display.py
```

These create the disposable fictional identity/existing appointment and persist fictional display names. They are operator setup, not agent bookings. Their before/after metadata is saved separately under `artifacts/receptionist/operator-*.json`; the manifest backup stays private. Existing earlier patients and appointment 58 are retained. Re-running the original seed command may reset the manifest; rerun the receptionist setup afterward. Do not use production records or credentials.

## Exact safe caller script

1. Click **Start microphone session**, allow the microphone and wait for the AI greeting.
2. Say “Please check my appointments.” Give **Jordan Morgan**, **April 12, 1990**, then **202-555-0122** when asked for the additional check.
3. Listen for the actual appointment details, including UTC. Show the chronological list, then Calendar: November 25 has appointment 73. Blank dates do not indicate free time.
4. Say “I'd like to request an annual wellness visit next Tuesday morning.” If asked, choose no clinician preference and in person. These are preferences, not verified available options.
5. Expected truthful response: the clinic must confirm a time; contact the clinic scheduling team. No request is sent to a staffed queue, no booking is confirmed, and the existing appointment remains unchanged.
6. Expand **EHR requests**, **Independent final readback**, **Evaluation results**, then open the saved run. Inspect actual speech separately from SDK tasks, Kyron tools, redacted HTTP metadata and EHR readback.

The local OpenEMR calendar link opens its normal UI and may require separate local UI login. API OAuth authentication does not establish a browser login. No credentials are embedded in the link.

## Capability boundaries

P0 implemented: natural AI prompts, no phone-first bypass, native name/DOB search with exact/unique filtering and marker/FHIR cross-check, additional phone factor, actual appointments with clinician/location/type/status/ID, list/calendar selection and synchronized three-panel evidence. Identity failures and ambiguous/paged searches fail closed without displaying appointments. An unsuccessful factor consumes the candidate and still finalizes failure evidence. The selected appointment is not mutation consent.

P1 blocked: the installed pinned API routes and FHIR capabilities do not establish an authoritative free-slot/clinic-hours feed. Existing appointments cannot establish availability or conflict-free slots. Four request categories are explicitly a demonstration planning catalog, not verified bookable EHR types. Preferences can be collected; time confirmation is delegated to the clinic. [Pinned OpenEMR API reference](https://github.com/openemr/openemr/blob/v7_0_4/API_README.md) and installed route source informed this boundary.

P2 disabled: no live new booking, cancellation, replacement booking, atomic reschedule or rollback/recovery workflow is claimed. The readonly wrapper rejects all mutation methods even if the underlying adapter was misconfigured. Missing identity, wrong appointment selection, repeated callbacks and urgent disposition cannot grant write authority. Independent review of the PR #1 safety fixes and this continuation is outstanding; the author cannot self-approve. Passing tests do not remove that gate.

P3 preserved: the triage dashboard and saved benchmark artifacts remain inspectable. Reference agreement is not clinical validation. No real transfer or accepted handoff is implemented.

## Verification and five-minute walkthrough

Final checks: **244 passed** with local synthetic OpenEMR enabled ([actual output](../artifacts/receptionist/final-all-tests.txt)); **233 passed / 11 skipped** offline ([actual output](../artifacts/receptionist/final-offline-tests.txt)); Ruff lint and format checks passed. A single existing Starlette/httpx deprecation warning remains. All **393 previously tracked artifacts at 20fd724** and the frozen ZIP hash were verified unchanged ([preservation check](../artifacts/receptionist/preservation-check.json)). Original 197-test reproduction and subsequent 227-test checkpoint remain saved. New protocol tests cover name/DOB ambiguity and mismatch, required additional factor, failed-verification finalization, wrong-patient selection, empty appointments, unsupported availability, repeated requests without mutation, urgent blocking, shared dashboard state, receipt binding and unknown readback. These protocol tests are not audio tests. Availability-conflict booking and rescheduling recovery are unimplemented and therefore not claimed as tested live features.

Suggested recording: 0:00–0:40 explain claims/tool/state evidence and isolated fictional environment; 0:40–2:20 perform the caller script; 2:20–3:20 show appointment list/calendar and same encounter ID; 3:20–4:20 inspect tool/readback/evaluator evidence; 4:20–5:00 state unsupported availability, disabled writes and independent-review/clinical limits. Preserve time to record and submit before **8 pm Eastern, November 22**. No video has been recorded or submitted by the assistant.

Fallback: open the saved genuine audio run linked above and its screenshots under `artifacts/receptionist/`. Clearly say it is a previously observed call, not a current live call. The separate scripted triage replay selector is labeled as scripted and must never be presented as genuine audio. Previous failed calls and all historical labels remain evidence.

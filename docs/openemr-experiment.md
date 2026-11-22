# Real OpenEMR experiment — November 22, 2026

This separate `codex/openemr-integration` branch continues local commit `78b7b84`. Work began about 3:38 p.m. Eastern while the authorized window remained open; the 8:00 p.m. cutoff was not extended. The earlier frozen submission ZIP and all preexisting evidence remain unchanged. No assertion is made that anything was already submitted.

## Actual environment and design

A real local official OpenEMR 7.0.4 image and MariaDB 11.4.5 run in the dedicated `kyron-openemr-synthetic` Compose project. Only HTTPS loopback port 9310 is published. API authentication uses a new private synthetic OAuth client and verified local TLS. Two fabricated patients and a fabricated practitioner/clinic were seeded through supported APIs. No patient data, real provider identity, production credential or external LLM was used.

The live CapabilityStatement reports FHIR 4.0.1. Appointment supports read/search; Slot/Schedule are absent. Patient and appointment reads use FHIR. Actual appointment creation/deletion use the documented standard API, separately authorized by `KYRON_OPENEMR_ALLOW_WRITES=1`. See [reproducible setup and exact contracts](../integrations/openemr/README.md).

## Preserved observed outputs

Final provider experiment: [experiment-20261122T210757-f1876862.json](../artifacts/openemr/experiment-20261122T210757-f1876862.json), created at 21:07:54 UTC / 4:07 p.m. Eastern. Implementation SHA256: `c5402c422f24db46ccd685ae05b246b962ce405c35c8ae8b3159a0f1fd9955c2` (Python source content hash, not Git revision).

| Controlled operation | Actual tool result | Independent state verification | Interpretation |
|---|---|---|---|
| Patient lookup/search and identity | OK | Exact seeded patient/marker binding | Authenticated reads, not caller authentication |
| Appointment retrieval | OK | Complete FHIR/native sets and patient/time/actor mapping | Actual persisted calendar records |
| Operator-specified create | OK, HTTP 200 native ID | PASS | New matching record plus persisted consent marker |
| Invalid creation | ERROR despite HTTP 200 | FAIL; expected rejection control met | No new record; HTTP alone was insufficient |
| Patient 2 cancellation of patient 1 event | ERROR before DELETE | Existing state unchanged | Zero mutation requests for wrong bound target |
| Actual create with response discarded by client | UNKNOWN | PASS on fresh independent read | Real commit occurred; timeout did not mean failure; no POST retry |
| Actual create with readback blocked by client | OK | UNKNOWN; later recovered PASS | Earlier uncertainty remains saved; later evidence does not rewrite it |
| Availability / review request | UNSUPPORTED | No fabricated completion | No free slots or human handoff invented |

The fault cases inject at the local HTTP-client boundary. The response-loss wrapper executes the real request then raises a synthetic timeout; the missing-readback wrapper blocks Appointment GETs. These are identified fault controls, not naturally occurring server/network outages.

Creation probes are **operator API probes**, using an explicitly requested synthetic time and consent marker. Because this release exposes no free-slot API, these results do not demonstrate an agent finding or booking an available appointment. Repeated probes can create overlapping synthetic events. No production booking rate, model performance or availability correctness is inferred.

## Agent → real EHR → dashboard

Saved actual A/B read workflow: [run-20261122T210757-c4aaf8e3fd](../artifacts/runs/run-20261122T210757-c4aaf8e3fd.json). Both modes used real protected reads; unsupported availability prevented a genuine offer. This compares truthful behavior under a provider limitation, not successful native booking.

Saved actual deterministic B cancellation: [run-20261122T210757-e394f3b04b](../artifacts/runs/run-20261122T210757-e394f3b04b.json). Explicit synthetic caller consent targeted appointment 19. Guarded tools issued a standard DELETE; independent FHIR/native readback established final absence before B said, “Your appointment is cancelled. Please call the clinic if you need another time.” EHR write verification, identity binding, claim support and resolution clarity each PASS.

The browser additionally selected the real adapter, B and the cancellation scenario and created [run-20261122T211157-d6e10918bc](../artifacts/runs/run-20261122T211157-d6e10918bc.json), cancelling newly seeded appointment 24 at 4:11 p.m. Eastern. It showed the same supported outcomes and complete tool/state evidence. The connection-probe button made authenticated reads and preserved [probe-20261122T211219-1d51d5f469.json](../artifacts/ehr_probes/probe-20261122T211219-1d51d5f469.json). No browser console errors/warnings were observed. Screenshots: [dashboard](../artifacts/verification/openemr-dashboard.jpg), [browser-created run](../artifacts/verification/openemr-browser-run.jpg).

General legacy completion/safety metrics and aggregate tool/full-state metrics remain UNKNOWN for native cancellation because the original scheduling/triage task model and unavailable review collection cannot establish their complete evidence. Appointment-specific EHR checks use the independently authoritative partial collection. No UNKNOWN is counted as a passing full evaluation. Clarity 3.3 adds supported cancellation plus a clear next step; earlier 3.2 and original 1.0/2.0 remain executable. No new human validation is claimed.

## Corrections, regressions and preservation

Actual OpenEMR behavior corrected three assumptions: search Bundles use `collection`; empty native appointment lists can return 404; invalid POSTs can return HTTP 200 with field-level validation errors. The dedicated adapter handles those shapes without weakening the generic FHIR fixture adapter. The experiment at [21:05 UTC](../artifacts/openemr/experiment-20261122T210505-6dc60c77.json) missed the legacy validation-error shape and recorded an uncertain rejection control. It is preserved unchanged; adapter 1.1 and the 21:07 revision correct the decoder. Earlier real integration runs and the 20:58 experiment also remain saved.

No baseline test regression was observed. The original **125 tests passed before edits**. The expanded default suite reports **142 passed, 8 skipped**; with real-integration opt-in and the running authenticated stack, **150 passed**. One existing Starlette/httpx deprecation warning remains. Lint passes. Outputs: [default tests](../artifacts/verification/openemr-default-tests.txt), [all live-enabled tests](../artifacts/verification/openemr-all-tests.txt), [lint](../artifacts/verification/openemr-lint.txt). Test categories separate unit, synthetic fixture contract, scripted optional-model protocol and actual OpenEMR integration.

The [preservation manifest](../artifacts/verification/openemr-preservation-manifest.json) checks all **294 artifact files tracked at 78b7b84** byte for byte, including the previous 37-trace experiment, A/B comparisons, evaluator outputs and original four user labels. No labels were copied onto new traces or attributed to AI as human review. Original clarity calibration remains 2/4 for v1 and 4/4 for v2, without an accuracy claim beyond those four reviewed examples.

## Unverified and remaining limits

No actual LLM call, speech transport, live phone call, human transfer, clinical validation or production deployment was performed. Optional LLM integration consumes only normalized marker-checked local fields and is exercised with scripted protocol responses. Generic external FHIR remains blocked from model execution. Real endpoint tests cover selected resources in this one pinned installation, not arbitrary OpenEMR deployments.

No Slot/Schedule availability, appointment status-update lifecycle, review-queue mutation, pagination, concurrency guarantee or server-native idempotency is implemented. Cancellation physically deletes a marked test calendar event. The local SQLite journal prevents reposting only within the same persisted operation namespace/key; separate new runs can represent duplicate intent. Live database state is shared, and cancellation A/B requires independent fresh targets. These limitations are explicit in the UI and setup guide.

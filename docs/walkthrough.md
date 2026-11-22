# Five-minute single-take walkthrough

No video has been recorded or submitted by the assistant. Record one unedited take in your own words before **8:00 p.m. Eastern, November 22, 2026**. Review [AI-use disclosure](submission-notes.md) and the [real OpenEMR experiment](openemr-experiment.md).

The mock path starts with `KYRON_PORT=8002 ./start.sh` and needs no key or EHR. For the optional real path, use the [setup guide](../integrations/openemr/README.md), refresh test auth if expired, seed a fresh cancellation target, then start with `KYRON_OPENEMR_ALLOW_WRITES=1 KYRON_PORT=8002 ./start.sh`. Saved evidence is inspectable even when Docker is stopped.

| Time | Show and explain |
|---|---|
| 0:00–0:35 | Explain the progression from the existing deterministic workbench to guarded mock/FHIR tools and a separate real local OpenEMR adapter. All data is fabricated; original evidence and labels remain preserved. |
| 0:35–1:30 | Run Mock, A+B, **Booking acknowledged · state never committed**. Show the success-shaped tool response, empty authoritative state, A's false confirmation and B's recovery. |
| 1:30–2:35 | Open **OpenEMR evidence**. Show actual version/auth, real create readback, client-injected response loss and missing readback. Explain why UNKNOWN remains distinct from failure and later recovery is separate. Creation probes are operator-specified API tests, not agent availability success. |
| 2:35–3:30 | Inspect the saved actual agent cancellation run or run B against a freshly seeded target. Show explicit consent, standard API DELETE, independent readback and supported final claim. Explain physical deletion, no Slot/Schedule availability and full-state UNKNOWN from missing review evidence. |
| 3:30–4:10 | Show original human review: four actual user labels, clarity v1 agreement 2/4 and v2 4/4. New EHR/cancellation rules have no independent human validation; four examples are not holdout accuracy. |
| 4:10–5:00 | State default 142 passing + 8 opt-in skips, live-enabled 150 passing, all prior 125 retained, 294 historical artifact files unchanged. Disclose AI implementation assistance and HTTP 200 validation-error correction. No actual model/voice/clinical validation or production claim. Next priorities: availability contracts, reconciliation, independently labeled language holdout. |

The candidate owns understanding, recording and submission. Do not attribute AI coding/debugging work to yourself or present the earlier frozen ZIP as containing later branch changes. Keep the explanation anchored in the actual saved evidence.

# Five-minute single-take walkthrough

No video has been recorded or submitted by the assistant. Record one unedited take in your own words before **8:00 p.m. Eastern, November 22, 2026**. Read the [current experiment](ehr-experiment.md) and [AI-use disclosure](submission-notes.md) first.

Start `./start.sh` (or `KYRON_PORT=8002 ./start.sh` if 8000 is occupied). The mock path needs no key or external EHR. The optional [local FHIR fixture](configuration.md) is useful only if you want to demonstrate the read-only boundary; do not spend recording time installing OpenEMR.

| Time | Show and explain |
|---|---|
| 0:00–0:35 | Runs → EHR integration. Explain your explicit choice to implement mock EHR tools and state verification before external connectivity or voice. This extends the existing workbench, with all original evidence/labels preserved. |
| 0:35–1:45 | Select **Booking acknowledged · state never committed**, Mock, A+B, Scenario default. Run. Show the actual create tool ACK, authoritative empty bookings, A's false confirmation and B's recovery. A successful response did not establish persistence. |
| 1:45–2:35 | Runs → Confirmed booking → Mock fault **State readback unavailable**, A+B. Run. Show write verification UNKNOWN, B's “a booking may exist; call before retrying” and private mock snapshot only in raw evidence. Missing evidence cannot be silently recovered from privileged instrumentation. |
| 2:35–3:15 | Open the saved full suite or Findings; show the emergency scheduling case. Correct EMERGENCY disposition coexists with A's blocked scheduling attempts and false claim. B keeps the terminal boundary. Both agents still complete four of eight goals; B reduces false claims rather than making failed tools succeed. |
| 3:15–4:00 | Human review → original four actual user labels. Original clarity v1 agrees 2/4; v2 4/4. Explain why those four examples do not validate new EHR clarity rules or general language understanding. No labels were copied onto new traces. |
| 4:00–4:35 | Briefly show the saved FHIR run/capabilities or the experiment report. Read-only synthetic HTTP fixture was tested; appointment writes/cancellation/review mutations are unsupported. It is not a running OpenEMR service. Optional LLM text/tool mode is implemented and protocol tested, but no real model experiment was run. |
| 4:35–5:00 | State 125 passing tests including original 68; preserved artifacts; AI implementation assistance and evidence-driven corrections. Next steps: seeded synthetic OpenEMR verification, actual model experiment, independent labels and transaction receipts. No real EHR write, live call, actual human transfer or clinical validation is claimed. |

Keep the walkthrough focused on one failure and the evidence that changed your interpretation. The candidate still owns understanding, recording and submission; the outline does not claim personal coding or debugging work that was performed by AI.

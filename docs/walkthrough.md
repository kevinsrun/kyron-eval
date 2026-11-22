# Five-minute single-take walkthrough outline

No video is recorded or submitted by the assistant. The candidate should record one unedited take and explain the choices in their own words. Use synthetic data only. These are demo steps, not a script claiming the candidate manually wrote the implementation.

Before recording: run `./start.sh`, open `http://127.0.0.1:8000` in a normal-width browser, and read `docs/experiment.md` and `docs/submission-notes.md`. Keep the recording under five minutes. The deadline is **8:00 p.m. Eastern on November 22, 2026**; leave sufficient time to record and submit.

| Time | Show and explain |
|---|---|
| 0:00–0:35 | Open Runs. State the problem: a plausible confirmation cannot prove work happened. Show the offline/synthetic boundary. Explain that you selected deterministic replay and state-backed evidence over live integration realism. |
| 0:35–1:10 | Select **All 8 scenarios**, **Compare A + B**, click **Run evaluation**. The browser invokes the API; the new run appears with stored outcomes. Point out that both variants complete four goals, despite B making fewer false claims. |
| 1:10–2:15 | Open **Booking acknowledged · state never committed**. Expand A's `book` tool result: it says `completed=true`. Inspect unchanged bookings and A's false spoken confirmation. Contrast B's `read_state` and “No appointment has been booked…” message. Explain the distinction between injected tool failure and agent overclaim. |
| 2:15–2:55 | Open **Emergency route · caller requests scheduling anyway**. Show EMERGENCY, blocked scheduling attempts, and A's later false booking. A correct disposition can coexist with unsafe agent behavior; a completion metric alone is insufficient. B preserves the boundary. |
| 2:55–3:50 | Open **Human review**. Show the four actual user labels, the two v1 disagreements, raw judgments and v2's 4/4 agreement. Explain that v1 rewarded “You're all set”; v2 consults state. Emphasize calibration overfitting and missing held-out validation. Labels annotate exact trace hashes rather than overwriting scores. |
| 3:50–4:30 | Open Findings and the report. Recommend state-backed completion claims and terminal policy gates first. The five baseline false claims are examples in eight selected synthetic cases, not production prevalence. Explain why unresolved task completion can still FAIL while safety/clarity PASS. |
| 4:30–5:00 | Mention tests and clean setup. Disclose AI implementation assistance, the actual human labels, and the personally chosen offline scope. Explain omissions: no audio, NLP extraction, live EHR, LLM judge or deployment. Next: independent human labels/holdout and production effect evidence. |

If time is tight, skip the source tree and emphasize one end-to-end failure plus the evaluator disagreement. Do not claim an actual phone transfer, clinical validation, production improvement or a recorded video that does not exist.

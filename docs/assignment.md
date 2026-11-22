# Kyron Medical — Full Stack Software Engineer Take-Home

## Build an Evaluation Platform for a Healthcare Voice Agent

## Time Limit

You will have **8 hours from the time you receive the assignment to submit your work**. You may choose when you would like that window to begin.

Please stop working and submit what you have at the end of the eight hours. There is deliberately more here than we expect one person to complete in that time.

An incomplete submission is not inherently a weak submission. We care about what you prioritize, the depth and quality of what you finish, and whether you understand the tradeoffs you made.

## How to Approach This Assignment

Kyron is a fast-moving startup. Engineers routinely face more worthwhile work than can be done at once. Choosing the highest-leverage path—and executing it without losing sight of the real product problem—is part of the job.

There is no hidden expectation that you complete every section. Use the eight hours to give us the strongest evidence you can that you would be an exceptional engineer at Kyron.

That may mean going unusually deep on one part, building a narrow but convincing product, designing an excellent experiment, finding an evaluator failure that changes your approach, or covering a surprising amount of ground coherently.

**Make tradeoffs. Play to your strengths. Be ambitious, but deliberate.**

## Use of AI

You may use LLMs, coding agents, and other AI tools freely. We expect engineers at Kyron to use them.

We are not evaluating how much code or prose you typed manually. We are evaluating the resulting work and the judgment behind it.

Modern AI tools can generate plausible applications, metrics, prompts, and documentation very quickly. Plausibility alone will not provide much signal. We are particularly interested in:

- Decisions you made after examining actual outputs
- Assumptions you identified and tested
- Cases where an evaluator or simulator behaved differently than you expected
- Evidence that caused you to revise your implementation or thinking
- Limitations you can explain precisely
- Technical choices you understand well enough to modify and defend

Include a brief account of how you used AI, where its output was unreliable or generic, and one consequential decision you made rather than delegated.

## Context

Kyron builds AI voice agents that handle real interactions between patients and healthcare organizations.

Representative workflows include:

- Scheduling, rescheduling, confirming, and cancelling appointments
- Prescription refill and pharmacy-change requests
- Referral and prior-authorization status
- Medical-record requests
- Billing questions and payments
- Clinical messages and urgent-symptom routing
- Transfers and callbacks when automation cannot safely complete the task

These systems operate in an environment where a superficially successful conversation can still fail in important ways. An agent might misunderstand a critical entity, claim completion when a tool failed, create unnecessary staff work, escalate too late, repeat sensitive questions, lose context during a handoff, or leave the patient uncertain about what happens next.

At production scale, we cannot manually inspect every interaction. We need systems that help us answer:

1. Did the agent understand and safely handle the caller's request?
2. Did it actually complete the work it claimed to complete?
3. When automation failed, what failed and why?
4. Did the interaction create unnecessary burden or distrust?
5. Did a prompt, model, tool, or workflow change make the system better or worse?
6. Which failures deserve engineering attention first?

## The Assignment

Build a **minimal evaluation platform for a healthcare voice agent**.

Assume the production voice agent already exists. Your system sits around it: it creates or replays evaluation scenarios, captures traces, scores behavior, compares results, and helps a teammate investigate failures.

We are not providing a call dataset. Designing useful evaluation evidence is part of the problem.

Use only synthetic data. Do not use real patient information, recordings, or credentials.

## Minimum Functional Slice

However you allocate the eight hours, your submission should include one runnable end-to-end path:

> scenario → agent interaction or replay → captured trace → automated evaluation → inspectable result in the application

It is fine for the agent, simulator, or external systems to be simplified. Clearly identify what is real, mocked, or omitted. Audio is optional; we do not expect you to build a production speech stack.

You may use paid APIs if you choose, but the core submission should remain reviewable without exposing credentials. Provide saved example outputs or a deterministic fallback when practical.

---

# Part 1 — Model the Evaluation Problem

Choose at least two healthcare workflows that you believe create an interesting evaluation surface.

Design a small scenario dataset for them. A useful submission will likely contain a mixture of ordinary cases, difficult cases, and controls—not only obvious failures.

For each scenario, consider specifying:

- The caller's underlying goal
- Facts available to the caller, agent, and tools
- Relevant workflow or safety policy
- What successful completion would mean
- Critical entities or actions that must be correct
- Conditions that require escalation or human involvement
- What evidence is intentionally unavailable
- Expected or acceptable outcomes

Partially populate the dataset with enough examples to exercise your system. You choose the number. Explain why your sample is useful and what it does not represent.

We are interested in whether your scenarios distinguish systems that genuinely behave differently. A long list of obvious one-turn failures is unlikely to be very informative.

## Ground Truth

Explain what you treat as ground truth and where it comes from.

For example, ground truth might include structured tool state, known appointment availability, a medication or pharmacy record, an explicit customer policy, a human label, or the final state of a simulated system.

Be clear about which conclusions cannot be established from a transcript alone.

---

# Part 2 — Build a Replay or Simulation Harness

Build a way to run an agent through your scenarios without placing real telephone calls.

You choose the level of fidelity. Possibilities include:

- A text-based simulated caller
- A scripted replay harness
- An LLM patient simulator
- A hybrid of deterministic and generated behavior
- A fuller speech loop using synthetic audio

The harness should produce inspectable artifacts such as conversation turns, tool calls, state changes, errors, and final outcomes.

Consider how your design handles:

- Non-determinism
- Repeatability
- Caller corrections and interruptions
- Ambiguous or poorly stated requests
- Tool delays and failures
- Unsupported tasks
- Escalations, transfers, and callbacks
- Sensitive or urgent situations
- Simulator bias and unrealistic cooperation

We care more about an honest, useful abstraction than maximal fidelity. Explain what your harness can and cannot tell us about a real voice system.

---

# Part 3 — Decide What to Measure

Develop and implement evaluation metrics that you believe matter most.

Your metrics should address more than generic helpfulness or conversational quality. Consider the distinct questions involved in areas such as:

- Task understanding
- Correctness of critical information
- Actual task completion
- Automation and unnecessary staff work
- Safety and escalation behavior
- Recovery from misunderstanding or tool failure
- Patient effort, trust, and clarity
- Handoff execution and context preservation

You do not need to cover all of these. Choosing what not to measure is part of the assignment.

For each implemented metric, explain:

- What question it answers
- The evidence it requires
- How it is computed
- What its values mean
- Known ambiguities and failure modes
- How you would validate it

Use whatever combination of deterministic checks, state comparison, code-based evaluators, and LLM judges makes sense.

## Automated Evaluators

For at least one judgment-based metric, show how you move from **human judgment to automated evaluation**.

A strong process might include:

1. Manually labeling a small set of traces
2. Defining an explicit decision boundary
3. Implementing an evaluator
4. Comparing its results with your labels
5. Investigating disagreements
6. Revising the metric, prompt, examples, or evidence
7. Rerunning the evaluation

We do not expect high accuracy from a tiny sample. We do expect you to notice when agreement numbers are misleading, when the rubric itself is ambiguous, or when an evaluator reaches the right answer for the wrong reason.

Preserve the examples, labels, raw evaluator outputs, and disagreement evidence needed to understand what happened.

---

# Part 4 — Build the Full-Stack Product

Build a usable interface around your evaluation system.

The product should help an engineer or product teammate answer questions such as:

- What evaluation runs have occurred?
- Which scenarios failed?
- What changed between two agent versions or configurations?
- Why did a particular metric produce its result?
- What did the caller, agent, simulator, and tools do?
- Can a human review, annotate, or override an evaluation?
- Which failure patterns appear repeatedly?

Use the frontend, backend, API, database, and infrastructure choices that make sense for your scope.

We do not require a particular framework. We do want a meaningful working slice, not only static mockups.

A narrow application that makes one difficult evaluation workflow genuinely usable can be more valuable than a broad dashboard whose outputs cannot be trusted.

Include enough setup instructions and sample data for us to run the application.

---

# Part 5 — Run an Experiment

Use your platform to compare at least two agent behaviors, prompts, configurations, or versions.

The comparison may involve a real LLM-backed agent, a simplified agent you build, controlled prompt variants, or another defensible representation.

Show the evidence produced by the experiment—not only a description of what the system would do.

We are interested in:

- What change you tested and why
- What you predicted would happen
- What actually happened
- Which metrics moved
- Whether any scenarios regressed
- A failure or disagreement you investigated closely
- A change you made after examining the evidence
- What conclusion you would and would not draw from the experiment

At least one result should be inspectable end to end, from scenario definition through trace and final evaluation.

---

# Part 6 — Make a Product Recommendation

Imagine these results came from a pre-production evaluation of a Kyron agent.

What would you put in front of the product and engineering team?

Identify the problems or opportunities you believe deserve attention first. Connect your recommendation to evidence produced by your system.

Consider including:

- The failure mechanism
- Its apparent frequency and severity
- Why it matters to patients or customers
- The likely product or engineering intervention
- How you would verify that the intervention worked
- Important safety, experience, or automation guardrails

Do not treat the synthetic sample as an estimate of production prevalence. Tell us what additional production evidence you would need.

---

# Part 7 — Production Design

Briefly explain how you would evolve your work into a production evaluation system processing thousands of calls per day.

You might discuss:

- Data and trace ingestion
- Versioning of agents, prompts, policies, scenarios, and evaluators
- Human-review sampling
- Regression gates and release decisions
- Monitoring evaluator drift
- Privacy and security
- Cost and latency
- Customer-specific policies
- Observability and debugging
- How evaluation findings become owned product work

We are interested in concrete priorities and boundaries, not an exhaustive architecture diagram.

---

# Part 8 — Walk Us Through Your Work

Submit a **single-take, unedited video** walking through your submission. Aim for five minutes or less. 

Talk about what *you* found interesting.

---

# What to Submit

At the end of the eight-hour window, submit what you have, including:

1. Source code
2. Setup and run instructions
3. Scenario or dataset definitions
4. Evaluation prompts, logic, and schemas
5. Manual labels and comparison artifacts you used
6. Output from at least one completed evaluation run
7. Your product findings and production notes
8. Your walkthrough video. It is **strongly** recommended to submit the video. We heavily weight this part because we want to know how *you* are thinking about things.

Also include a short summary stating:

- Where you spent your time
- What you intentionally did not complete
- How you used AI tools
- What you would do next with more time

If something is unfinished, submit the unfinished version rather than working beyond the time limit.

## Security and Data Handling

- Use only synthetic information.
- Do not include real patient, provider, customer, or insurance-member data.
- Do not commit API keys or credentials.
- Clearly identify any external services required to run the submission.

---

# How We Will Evaluate Your Submission

There is no fixed point allocation across the sections. We will evaluate the submission as a whole.

## Product Judgment

Did you model important real-world problems? Did you distinguish consequential failures from superficial imperfections? Did the product help someone make a decision?

## Engineering Judgment

Did you choose a sensible architecture and scope for eight hours? Are boundaries, state, errors, and failure modes handled thoughtfully?

## Technical Execution

Does the implemented slice work? Is it readable, testable, and understandable? Could a teammate extend it?

## Full-Stack Ability

Did you connect the user experience, application state, backend behavior, persistence, and evaluation pipeline into a coherent product?

## AI and Evaluation Judgment

Did you use AI systems empirically rather than treating plausible output as correct? Did you define ground truth, inspect failures, calibrate judgment, and understand simulator and evaluator limitations?

## Iteration

Did actual evidence change your implementation or thinking? Did you investigate something surprising instead of smoothing it over?

## Efficient Execution

Given eight hours and many plausible directions, did you allocate effort effectively?

## Ownership

Does the work reflect deliberate choices the candidate understands and can defend?

## Communication

Can the candidate explain the system, evidence, limitations, and tradeoffs clearly?

---

# What Happens Next

If we move forward, we will schedule a follow-up conversation about your submission.

Expect to discuss specific scenarios, evaluator decisions, architecture choices, and tradeoffs. We may ask how you would change part of the implementation or evaluation under a new constraint.

This is not a separate exercise to prepare for. We want to have a technical conversation about the work you already did and understand how you thought about it.

## One Final Note

**Do not optimize for checking every box. Optimize for showing us what you are capable of.**

There is deliberately more here than we expect you to complete in eight hours. Choose what matters, execute it well, and show us how you think.

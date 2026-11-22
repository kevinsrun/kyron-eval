"""Conservative synthetic demonstration policy, independent of benchmark labels/model text."""

import re
from uuid import uuid4

from kyron_eval.triage.datasets import CARE_LABELS

VERSION = "synthetic-triage-policy-1.0-unreviewed"
RED_FLAG = re.compile(
    r"chest pain|trouble breathing|difficulty breathing|cannot breathe|can't breathe|"
    r"throat swelling|fainting|unconscious|stroke|severe bleeding",
    re.I,
)


class TriageEncounter:
    """Labels score predictions; they never authorize workflow operations."""

    def __init__(self, encounter_id=None):
        self.id = encounter_id or "encounter-" + uuid4().hex
        self.turns = {}
        self.transitions = []
        self.facts = {}
        self.prediction = None
        self.reference = None
        self.urgent_latched = False
        self.state = "TRIAGE_INCOMPLETE"
        self.reason = "Symptoms, onset and a structured agent action are required."

    def observe(self, utterance_id, text, invalidate_prediction=True):
        if utterance_id in self.turns:
            if self.turns[utterance_id] == text:
                return
            # Guava CallerSpeechEvent explicitly defines earlier same-ID text as stale.
            # A benign ASR revision invalidates extraction; it is not an emergency signal.
            clinical_sources = (
                self.prediction.get("source_event_ids", []) if self.prediction else []
            )
            if invalidate_prediction or utterance_id in clinical_sources:
                self.prediction = None
                self.facts = {}
        self.turns[utterance_id] = text
        if invalidate_prediction:
            self.prediction = None
            self.facts = {}
        # Intentionally conservative mention guard, including negated/unclear mentions.
        # It does not assert a diagnosis, absence of emergency, or clinical validation.
        if RED_FLAG.search(text):
            self.urgent_latched = True
        self._route("caller_observation")

    def extract(self, *, symptoms, onset, action, source_event_ids, source="SDK task fields"):
        text = " ".join(self.turns.get(i, "") for i in source_event_ids).casefold()
        self.prediction = {
            "action": action,
            "symptoms": symptoms,
            "onset": onset,
            "source_event_ids": source_event_ids,
            "source": source,
        }
        # Exact quotes anchor the extraction to caller events; missing/guessed facts stay missing.
        self.facts = {
            k: v
            for k, v in {"symptoms": symptoms, "onset": onset}.items()
            if isinstance(v, str) and v.strip() and v.casefold() in text
        }
        self._route("agent_prediction")

    def _route(self, trigger):
        action = self.prediction.get("action") if self.prediction else None
        if self.urgent_latched or action == CARE_LABELS[3]:
            self.state, self.reason = (
                "URGENT_ESCALATION",
                "Urgent action or high-risk caller language; routine scheduling blocked. "
                "No transfer confirmed.",
            )
        elif action not in CARE_LABELS or len(self.facts) < 2 or action == CARE_LABELS[0]:
            self.state, self.reason = (
                "TRIAGE_INCOMPLETE",
                "Collect/clarify symptoms and onset; no automatic appointment changes.",
            )
        elif action == CARE_LABELS[1]:
            self.state, self.reason = (
                "SELF_CARE_REVIEW",
                "Self-care prediction does not establish that clinical evaluation is unnecessary.",
            )
        else:
            self.state, self.reason = (
                "NONURGENT",
                "May request a separately authorized appointment workflow; "
                "no clinical safety certification.",
            )
        self.transitions.append(
            {
                "index": len(self.transitions) + 1,
                "trigger": trigger,
                "state": self.state,
                "reason": self.reason,
                "prediction": self.prediction,
                "facts": dict(self.facts),
                "policy_version": VERSION,
            }
        )

    def snapshot(self):
        return {
            "encounter_id": self.id,
            "state": self.state,
            "reason": self.reason,
            "prediction": self.prediction,
            "reference": self.reference,
            "facts": self.facts,
            "missing_facts": [k for k in ["symptoms", "onset"] if k not in self.facts],
            "scheduling_permitted": False,
            "scheduling_restrictions": [
                "All live writes disabled pending independent review",
                "OpenEMR availability discovery unsupported",
            ],
            "policy_version": VERSION,
            "transitions": self.transitions,
            "latest_caller_utterances": dict(self.turns),
        }

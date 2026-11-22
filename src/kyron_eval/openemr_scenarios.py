"""An optional native cancellation target; the original eight fixtures remain unchanged."""

import json

from kyron_eval.ehr.config import openemr_directory
from kyron_eval.models import CallerTurn
from kyron_eval.replay import load_scenarios


def cancellation_scenario():
    try:
        manifest = json.loads((openemr_directory() / "manifest.json").read_text())
        target = manifest["seeded_appointment_id"]
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError(
            "Seed the isolated OpenEMR clinic before choosing its cancellation scenario"
        ) from exc
    scenario = load_scenarios()[0].model_copy(deep=True)
    scenario.id = "openemr-cancel-existing"
    scenario.version = "openemr-cancellation-1.0"
    scenario.title = "Real OpenEMR · cancel seeded synthetic appointment"
    scenario.caller_goal = (
        "Cancel the explicitly identified synthetic test appointment; "
        "standard API deletes its calendar event."
    )
    scenario.script = [
        CallerTurn(
            text=(
                f"Please cancel synthetic test appointment {target}. "
                "Yes, I confirm cancellation of that appointment."
            ),
            facts={"cancel_appointment_id": target},
            confirms_cancellation=True,
        )
    ]
    scenario.expected.acceptable_outcomes = ["NOT_BOOKED"]
    scenario.expected.critical_entities = {}
    scenario.expected.explanation = (
        "Authoritative before/after appointment evidence must establish "
        "cancellation of the bound native ID."
    )
    scenario.transcript_cannot_prove = [
        "Actual OpenEMR event deletion requires independent readback.",
        (
            "The original task completion metric is "
            "scheduling/triage-specific; EHR write verification covers this cancellation."
        ),
    ]
    return scenario

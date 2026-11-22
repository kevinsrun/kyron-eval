"""Export the actual validation contracts for inspection without running Python."""

import json
from pathlib import Path

from kyron_eval.models import HumanLabel, Run, Scenario, Trace


def main():
    target = Path(__file__).resolve().parents[1] / "docs/schemas.json"
    target.write_text(
        json.dumps(
            {
                model.__name__: model.model_json_schema()
                for model in (Scenario, Trace, Run, HumanLabel)
            },
            indent=2,
        )
        + "\n"
    )
    print(target)


if __name__ == "__main__":
    main()

import argparse
import json

from kyron_eval.evaluate import CLARITY_VERSION, CLARITY_VERSIONS
from kyron_eval.storage import Store


def main():
    parser = argparse.ArgumentParser(description="Offline synthetic evaluation workbench")
    parser.add_argument("command", choices=["run", "calibrate"])
    parser.add_argument("--variant", choices=["A", "B", "both"], default="both")
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--clarity-version", default=CLARITY_VERSION, choices=CLARITY_VERSIONS)
    args = parser.parse_args()
    if args.command == "calibrate":
        from kyron_eval.calibration import save_comparison

        print(json.dumps(save_comparison(Store())["summaries"], indent=2))
        return
    variants = ["A", "B"] if args.variant == "both" else [args.variant]
    run = Store().execute(variants, args.scenario, clarity_version=args.clarity_version)
    print(
        json.dumps(
            {
                "id": run.id,
                "results": len(run.results),
                "metrics": [
                    {
                        "scenario": r.scenario.id,
                        "variant": r.trace.variant,
                        **{m.name: m.status for m in r.metrics},
                    }
                    for r in run.results
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

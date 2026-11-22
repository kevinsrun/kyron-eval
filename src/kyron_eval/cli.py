import argparse
import json

from kyron_eval.ehr_evaluate import EHR_CLARITY_VERSION
from kyron_eval.evaluate import CLARITY_VERSIONS
from kyron_eval.storage import Store


def main():
    parser = argparse.ArgumentParser(description="Offline synthetic evaluation workbench")
    parser.add_argument("command", choices=["run", "calibrate"])
    parser.add_argument("--variant", choices=["A", "B", "LLM", "both"], default="both")
    parser.add_argument("--adapter", choices=["mock", "fhir", "openemr"], default="mock")
    parser.add_argument("--fault", action="append", default=[], metavar="OPERATION=MODE")
    parser.add_argument("--scenario", action="append")
    parser.add_argument(
        "--clarity-version", choices=(*CLARITY_VERSIONS, "clarity-ehr-3.2", EHR_CLARITY_VERSION)
    )
    args = parser.parse_args()
    if args.command == "calibrate":
        from kyron_eval.calibration import save_comparison

        print(json.dumps(save_comparison(Store())["summaries"], indent=2))
        return
    variants = ["A", "B"] if args.variant == "both" else [args.variant]
    try:
        faults = dict(item.split("=", 1) for item in args.fault)
        run = Store().execute(
            variants,
            args.scenario,
            clarity_version=args.clarity_version,
            adapter_name=args.adapter,
            ehr_faults=faults,
        )
    except ValueError as exc:
        parser.error(str(exc))
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

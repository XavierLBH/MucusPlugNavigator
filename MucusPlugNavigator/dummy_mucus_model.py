"""Dummy mucus model script used to test launching inference from Slicer.

This script does not run a real model. It only proves that the Slicer module can
start an external Python process, pass arguments, wait for completion, and read
the output text.
"""

import argparse
import json
import time


def parse_args():
    """Parse command-line arguments for the dummy model."""
    parser = argparse.ArgumentParser(description="Dummy mucus model runner")
    parser.add_argument("--case-id", default="dummy_case")
    parser.add_argument("--source-volume", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--sleep-seconds", type=float, default=0.2)
    return parser.parse_args()


def main():
    """Pretend to run model inference and print a small JSON result."""
    args = parse_args()
    time.sleep(max(args.sleep_seconds, 0.0))
    result = {
        "status": "ok",
        "case_id": args.case_id,
        "source_volume": args.source_volume,
        "output": args.output,
        "message": "Dummy mucus model finished successfully.",
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

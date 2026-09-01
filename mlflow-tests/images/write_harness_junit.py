#!/usr/bin/env python3
"""Write a pytest-shaped JUnit XML file for a harness abort before pytest runs.

Jenkins collects ``*unit*.xml`` from TEST_RESULTS_DIR and publishes it to the
Test Result navigator. Without this file, pre-pytest failures (deploy, CR
Available, server-info, and similar) only fail the container exit code and
never appear as a failed ``mlflow-e2e`` testcase.
"""

from __future__ import annotations

import argparse
import os
import socket
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, ElementTree, SubElement


DEFAULT_SUITE_NAME = "mlflow-e2e"
DEFAULT_CLASSNAME = "tests.harness.TestHarnessSetup"


def harness_junit_path(results_dir: str, storage_type: str | None = None) -> str:
    """Return the JUnit path Jenkins already archives for this suite."""
    filename = (
        f"xunit_report_{storage_type}.xml" if storage_type else "xunit_report.xml"
    )
    return os.path.join(results_dir, filename)


def write_harness_error_junit(
    output: str,
    *,
    suite_name: str = DEFAULT_SUITE_NAME,
    classname: str = DEFAULT_CLASSNAME,
    test_name: str,
    message: str,
    body: str | None = None,
    hostname: str | None = None,
    replace: bool = False,
) -> bool:
    """Write a single-error JUnit document.

    Returns False when ``output`` already exists so a later pytest report is
    never overwritten, unless ``replace`` is true (used to refresh a harness
    report we just wrote, after a failure snapshot is collected).
    """
    if not replace and os.path.isfile(output) and os.path.getsize(output) > 0:
        return False

    parent = os.path.dirname(output)
    if parent:
        os.makedirs(parent, exist_ok=True)

    timestamp = datetime.now(timezone.utc).isoformat()
    testsuites = Element("testsuites", {"name": "pytest tests"})
    testsuite = SubElement(
        testsuites,
        "testsuite",
        {
            "name": suite_name,
            "errors": "1",
            "failures": "0",
            "skipped": "0",
            "tests": "1",
            "time": "0",
            "timestamp": timestamp,
            "hostname": hostname or socket.gethostname() or "mlflow-tests",
        },
    )
    testcase = SubElement(
        testsuite,
        "testcase",
        {
            "classname": classname,
            "name": test_name,
            "time": "0",
        },
    )
    error = SubElement(testcase, "error", {"message": message})
    error.text = body if body is not None else message
    ElementTree(testsuites).write(output, encoding="utf-8", xml_declaration=True)
    return True


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write a failing JUnit XML report for a pre-pytest harness error."
    )
    parser.add_argument("--output", required=True, help="Destination JUnit XML path")
    parser.add_argument(
        "--suite",
        default=DEFAULT_SUITE_NAME,
        help="testsuite name (default: mlflow-e2e)",
    )
    parser.add_argument(
        "--classname",
        default=DEFAULT_CLASSNAME,
        help="testcase classname (default: tests.harness.TestHarnessSetup)",
    )
    parser.add_argument("--name", required=True, help="testcase name")
    parser.add_argument("--message", required=True, help="error message attribute")
    parser.add_argument(
        "--body",
        default=None,
        help="error element text; defaults to --message",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="overwrite an existing JUnit file (harness snapshot refresh only)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    wrote = write_harness_error_junit(
        args.output,
        suite_name=args.suite,
        classname=args.classname,
        test_name=args.name,
        message=args.message,
        body=args.body,
        replace=args.replace,
    )
    if not wrote:
        print(
            f"WARN: not overwriting existing JUnit report at {args.output}",
            flush=True,
        )
        return 2
    print(f"Wrote harness JUnit error report: {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

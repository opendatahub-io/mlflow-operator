#!/usr/bin/env python3
"""Print the supported MLflow version from component metadata."""

from __future__ import annotations

import argparse
from pathlib import Path
import re

SUPPORTED_MLFLOW_VERSION_RE = re.compile(
    r"(?ms)^[ \t]*-[ \t]*name:[ \t]*MLflow[ \t]*$.*?^[ \t]*version:[ \t]*v?([^\t\r\n ]+)"
)
NORMALIZED_VERSION_RE = re.compile(r"^[vV]?(\d+)\.(\d+)")


def extract_supported_mlflow_version(component_metadata: Path) -> str:
    text = component_metadata.read_text(encoding="utf-8")
    match = SUPPORTED_MLFLOW_VERSION_RE.search(text)
    if match is None:
        raise ValueError(f"Unable to find MLflow version in {component_metadata}")
    return match.group(1)


def normalize_mlflow_version(version: str) -> str:
    match = NORMALIZED_VERSION_RE.match(version)
    if match is None:
        raise ValueError(f"Unable to normalize MLflow version {version!r}")
    return f"{int(match.group(1))}.{int(match.group(2))}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print the supported MLflow version from component metadata."
    )
    parser.add_argument(
        "--component-metadata",
        required=True,
        type=Path,
        help="Path to config/component_metadata.yaml",
    )
    parser.add_argument(
        "--normalized",
        action="store_true",
        help="Print the normalized x.y version instead of the raw metadata value.",
    )
    args = parser.parse_args()

    version = extract_supported_mlflow_version(args.component_metadata)
    if args.normalized:
        version = normalize_mlflow_version(version)
    print(version)


if __name__ == "__main__":
    main()

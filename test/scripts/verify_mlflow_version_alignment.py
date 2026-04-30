#!/usr/bin/env python3

import argparse
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPONENT_METADATA = REPO_ROOT / "config" / "component_metadata.yaml"
MIGRATION_GO = REPO_ROOT / "internal" / "controller" / "migration.go"


def normalize(version: str) -> str:
    version = version.strip()
    version = version.removeprefix("mlflow, version ").strip()
    version = version.lstrip("v")
    version = version.split("+", 1)[0]
    return version


def parse_component_version() -> str:
    match = re.search(r"^\s*version:\s*(\S+)\s*$", COMPONENT_METADATA.read_text(), re.MULTILINE)
    if not match:
        raise RuntimeError(f"could not find release version in {COMPONENT_METADATA}")
    return normalize(match.group(1))


def parse_supported_version() -> str:
    match = re.search(r'SupportedMLflowVersion\s*=\s*"([^"]+)"', MIGRATION_GO.read_text())
    if not match:
        raise RuntimeError(f"could not find SupportedMLflowVersion in {MIGRATION_GO}")
    return normalize(match.group(1))


def read_image_version(image: str) -> str:
    candidates = (
        [
            "docker",
            "run",
            "--rm",
            image,
            "python",
            "-c",
            "from mlflow.version import VERSION; print(VERSION)",
        ],
        [
            "docker",
            "run",
            "--rm",
            image,
            "mlflow",
            "--version",
        ],
    )
    errors = []
    for cmd in candidates:
        try:
            output = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
        except subprocess.CalledProcessError as exc:
            errors.append(str(exc))
            continue
        if output:
            return normalize(output)
    raise RuntimeError(f"image {image} did not report an MLflow version: {'; '.join(errors)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify MLflow image and operator version alignment.")
    parser.add_argument("--mlflow-image", required=True, help="MLflow container image reference to inspect")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    metadata_version = parse_component_version()
    supported_version = parse_supported_version()
    if metadata_version != supported_version:
        print(
            f"component metadata version {metadata_version} does not match SupportedMLflowVersion {supported_version}",
            file=sys.stderr,
        )
        return 1

    image_version = read_image_version(args.mlflow_image)
    if image_version != supported_version:
        print(
            f"test image {args.mlflow_image} reports MLflow {image_version}, expected {supported_version}",
            file=sys.stderr,
        )
        return 1

    print(
        f"Verified MLflow version alignment: component metadata={metadata_version}, "
        f"operator supported={supported_version}, image={args.mlflow_image} ({image_version})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

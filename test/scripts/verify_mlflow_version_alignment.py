#!/usr/bin/env python3

import argparse
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPONENT_METADATA = REPO_ROOT / "config" / "component_metadata.yaml"


def normalize(version: str) -> str:
    version = version.strip()
    version = version.removeprefix("mlflow, version ").strip()
    version = version.lstrip("v")
    version = version.split("+", 1)[0]
    return version


def parse_component_version() -> str:
    match = re.search(
        r"(?ms)^\s*-\s*name:\s*MLflow\s*$.*?^\s*version:\s*(\S+)\s*$",
        COMPONENT_METADATA.read_text(),
    )
    if not match:
        raise RuntimeError(f"could not find the MLflow release version in {COMPONENT_METADATA}")
    return normalize(match.group(1))


def parse_make_supported_version() -> str:
    try:
        output = subprocess.check_output(
            ["make", "-s", "print-supported-mlflow-version"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"could not read supported MLflow version via Makefile: {exc.output.strip() or exc}"
        ) from exc
    if not output:
        raise RuntimeError("make print-supported-mlflow-version returned empty output")
    return normalize(output)


def read_image_version(image: str) -> str:
    cmd = [
        "docker",
        "run",
        "--rm",
        image,
        "mlflow",
        "--version",
    ]
    try:
        output = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"image {image} did not report an MLflow version: {exc.output.strip() or exc}"
        ) from exc
    if not output:
        raise RuntimeError(f"image {image} did not report an MLflow version")
    output_lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not output_lines:
        raise RuntimeError(f"image {image} did not report an MLflow version")
    return normalize(output_lines[-1])


def read_image_from_params_env(params_env: Path) -> str:
    for line in params_env.read_text().splitlines():
        if line.startswith("MLFLOW_IMAGE="):
            image = line.split("=", 1)[1].strip()
            if not image:
                raise RuntimeError(f"{params_env} defines an empty MLFLOW_IMAGE")
            return image
    raise RuntimeError(f"{params_env} does not define MLFLOW_IMAGE")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify MLflow image and operator version alignment.")
    parser.add_argument(
        "--mlflow-image",
        action="append",
        default=[],
        help="MLflow container image reference to inspect (repeatable)",
    )
    parser.add_argument(
        "--params-env",
        action="append",
        default=[],
        help="params.env file containing MLFLOW_IMAGE (repeatable)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    metadata_version = parse_component_version()
    make_supported_version = parse_make_supported_version()
    if make_supported_version != metadata_version:
        print(
            f"make print-supported-mlflow-version reported {make_supported_version}, expected {metadata_version}",
            file=sys.stderr,
        )
        return 1

    images_to_verify: list[tuple[str, str]] = []
    for params_env in args.params_env:
        params_env_path = Path(params_env)
        images_to_verify.append((str(params_env_path), read_image_from_params_env(params_env_path)))
    for image in args.mlflow_image:
        images_to_verify.append((image, image))

    if not images_to_verify:
        print("at least one --mlflow-image or --params-env argument is required", file=sys.stderr)
        return 1

    verified_sources = []
    for source, image in images_to_verify:
        image_version = read_image_version(image)
        if image_version != metadata_version:
            print(
                f"test image {image} from {source} reports MLflow {image_version}, expected {metadata_version}",
                file=sys.stderr,
            )
            return 1
        verified_sources.append(f"{source} -> {image} ({image_version})")

    print(
        f"Verified MLflow version alignment: component metadata={metadata_version}, "
        f"make={make_supported_version}, "
        f"images={'; '.join(verified_sources)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

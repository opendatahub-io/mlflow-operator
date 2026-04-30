#!/usr/bin/env python3

import argparse
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_METADATA = REPO_ROOT / "config" / "component_metadata.yaml"
MIGRATION_GO = REPO_ROOT / "internal" / "controller" / "migration.go"


def normalize(version: str) -> str:
    return version.strip().lstrip("v")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update the supported MLflow version in source metadata.")
    parser.add_argument("--version", required=True, help="MLflow version to set, with or without a leading v")
    return parser.parse_args()


def replace_once(path: Path, pattern: str, replacement: str) -> None:
    content = path.read_text()
    updated, count = re.subn(pattern, replacement, content, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"expected exactly one match for {pattern!r} in {path}")
    path.write_text(updated)


def main() -> int:
    args = parse_args()
    version = normalize(args.version)

    replace_once(
        COMPONENT_METADATA,
        r"^(\s*version:\s*)v?[^\s]+(\s*)$",
        rf"\1v{version}\2",
    )
    replace_once(
        MIGRATION_GO,
        r'(SupportedMLflowVersion\s*=\s*")([^"]+)(")',
        rf'\g<1>{version}\3',
    )

    print(f"Updated MLflow version references to {version}")
    print(f"- {COMPONENT_METADATA}")
    print(f"- {MIGRATION_GO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Classify sample manifests in a directory by their parsed document kind.

Print "<kind>\t<namespace or ->\t<path>" per document declaring a kind.
Use "-" for missing namespaces because shell splitting drops empty fields.

Requires PyYAML; install with `python3 -m pip install pyyaml`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml


def _document_namespace(document: dict[str, Any]) -> str:
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        return "-"
    namespace = metadata.get("namespace")
    return namespace if isinstance(namespace, str) and namespace else "-"


def classify(directory: Path) -> int:
    for path in sorted(directory.glob("*.yaml")):
        try:
            documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except yaml.YAMLError as exc:
            print(f"classify-samples: {path} is not valid YAML: {exc}", file=sys.stderr)
            return 1
        for document in documents:
            if not isinstance(document, dict):
                continue
            kind = document.get("kind")
            if isinstance(kind, str) and kind:
                print(f"{kind}\t{_document_namespace(document)}\t{path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Directory of sample YAML files")
    args = parser.parse_args()
    if not args.directory.is_dir():
        print(f"classify-samples: {args.directory} is not a directory", file=sys.stderr)
        return 1
    return classify(args.directory)


if __name__ == "__main__":
    raise SystemExit(main())

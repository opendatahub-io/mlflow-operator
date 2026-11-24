#!/usr/bin/env python3
"""Resolve Konflux PR targets and validate repo-specific build contracts.

The shell entry point (scripts/validate-build.sh) owns the runtime checks. This
helper owns parsing so Tekton, Dockerfile and supported-version contracts are
asserted against the files Konflux actually reads instead of hardcoded guesses.
"""

from __future__ import annotations

import argparse
import glob
import json
import posixpath
import re
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
TEKTON_GLOB = ".tekton/*pull-request.yaml"
PIPELINE_PATH = "pipeline/multi-arch-container-build.yaml"
PIPELINE_DEFAULTS: dict[str, Any] = {
    "hermetic": "false",
    "build-platforms": ["linux/x86_64"],
}
ROLE_BY_REPOSITORY = {
    "mlflow-operator": "operator",
    "mlflow-tests": "tests",
}


def fail(message: str) -> None:
    print(f"❌ {message}", file=sys.stderr)
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"✅ {message}")


def info(message: str) -> None:
    print(f"   {message}")


def read_text(relative: str) -> str:
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT):
        fail(f"required file escapes the repository: {relative}")
    if not path.is_file():
        fail(f"required file is missing: {relative}")
    return path.read_text(encoding="utf-8")


def load_yaml(relative: str) -> dict[str, Any]:
    try:
        document = yaml.safe_load(read_text(relative))
    except yaml.YAMLError as exc:
        fail(f"cannot parse {relative}: {exc}")
    if not isinstance(document, dict):
        fail(f"{relative} did not parse to a YAML mapping")
    return document


def params_map(spec: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for item in spec.get("params", []) or []:
        if not isinstance(item, dict) or "name" not in item:
            fail(f"invalid Tekton param entry: {item!r}")
        params[item["name"]] = item.get("value")
    return params


def normalize_platforms(value: Any) -> list[str]:
    if value is None:
        value = PIPELINE_DEFAULTS["build-platforms"]
    if isinstance(value, str):
        platforms = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, list):
        platforms = [str(part).strip() for part in value if str(part).strip()]
    else:
        fail(f"unsupported build-platforms value: {value!r}")
    if not platforms:
        fail("build-platforms resolved to an empty list")
    return platforms


def resolve_targets() -> dict[str, dict[str, Any]]:
    files = sorted(ROOT.glob(TEKTON_GLOB))
    if not files:
        fail(f"no Konflux PR PipelineRuns matched {TEKTON_GLOB}")

    targets: dict[str, dict[str, Any]] = {}
    for path in files:
        relative = str(path.relative_to(ROOT))
        document = load_yaml(relative)
        if document.get("kind") != "PipelineRun":
            continue

        spec = document.get("spec", {}) or {}
        params = params_map(spec)
        for required in ("output-image", "dockerfile", "path-context"):
            if not params.get(required):
                fail(f"{relative}: spec.params is missing non-empty {required}")

        image = str(params["output-image"])
        repository = image.rsplit("/", 1)[-1].split("@", 1)[0].split(":", 1)[0]
        role = ROLE_BY_REPOSITORY.get(repository)
        if role is None:
            fail(
                f"{relative}: unexpected PR image {image!r}; "
                f"expected one of {sorted(ROLE_BY_REPOSITORY)}"
            )
        if role in targets:
            fail(f"duplicate Konflux PR target for role {role!r}: {relative}")

        pipeline_ref = spec.get("pipelineRef", {}) or {}
        ref_params: dict[str, str] = {}
        for item in pipeline_ref.get("params", []) or []:
            if isinstance(item, dict) and "name" in item:
                ref_params[str(item["name"])] = str(item.get("value", ""))
        if pipeline_ref.get("resolver") != "git":
            fail(f"{relative}: expected a git pipelineRef resolver")
        if not ref_params.get("url", "").endswith("odh-konflux-central.git"):
            fail(f"{relative}: unexpected pipelineRef url {ref_params.get('url')!r}")
        if ref_params.get("pathInRepo") != PIPELINE_PATH:
            fail(
                f"{relative}: unexpected pipelineRef pathInRepo "
                f"{ref_params.get('pathInRepo')!r}; expected {PIPELINE_PATH!r}"
            )

        dockerfile = str(params["dockerfile"])
        context = str(params["path-context"])
        context_path = (ROOT / context).resolve()
        dockerfile_path = (context_path / dockerfile).resolve()
        if ROOT not in context_path.parents and context_path != ROOT:
            fail(f"{relative}: path-context escapes the repository: {context}")
        if not context_path.is_dir():
            fail(f"{relative}: path-context does not exist: {context}")
        if not dockerfile_path.is_file():
            fail(
                f"{relative}: dockerfile does not exist under path-context: "
                f"{context.rstrip('/')}/{dockerfile}"
            )
        if ROOT.resolve() not in dockerfile_path.parents and dockerfile_path != ROOT.resolve():
            fail(f"{relative}: dockerfile escapes the repository: {dockerfile_path}")

        hermetic = str(params.get("hermetic", PIPELINE_DEFAULTS["hermetic"])).lower()
        platforms = normalize_platforms(params.get("build-platforms"))
        tags = params.get("additional-tags") or []
        if not isinstance(tags, list):
            fail(f"{relative}: additional-tags must be a list, got {tags!r}")

        targets[role] = {
            "role": role,
            "pipelineRun": relative,
            "image": image,
            # Tekton interprets dockerfile relative to path-context. Store both
            # values repo-relative so every later check reads the same file the
            # pipeline builds without assuming the context is the repo root.
            "dockerfile": dockerfile_path.relative_to(ROOT).as_posix(),
            "context": context_path.relative_to(ROOT).as_posix(),
            "declaredDockerfile": dockerfile,
            "declaredContext": context,
            "hermetic": hermetic,
            "platforms": platforms,
            "additionalTags": [str(tag) for tag in tags],
        }

    if set(targets) != set(ROLE_BY_REPOSITORY.values()):
        fail(
            "expected exactly one PR build target for each role "
            f"{sorted(ROLE_BY_REPOSITORY.values())}, found {sorted(targets)}"
        )
    return {role: targets[role] for role in sorted(targets)}


def emit_target_env(role: str) -> None:
    target = resolve_targets().get(role)
    if target is None:
        fail(f"unknown target role {role!r}")
    values = {
        "IMAGE": target["image"],
        "DOCKERFILE": target["dockerfile"],
        "CONTEXT": target["context"],
        "HERMETIC": target["hermetic"],
        "PLATFORMS": ",".join(target["platforms"]),
        "PIPELINE_RUN": target["pipelineRun"],
    }
    for key, value in values.items():
        text = str(value)
        if "\n" in text or "\r" in text:
            fail(f"target field {key} contains a newline")
        # The shell entry point parses these assignments with `read`, never
        # `eval`, so values are emitted unquoted and validated by the caller.
        print(f"SIM_TARGET_{key}={text}")


def dockerfile_instructions(relative: str) -> list[tuple[str, str]]:
    instructions: list[tuple[str, str]] = []
    buffer = ""
    for raw_line in read_text(relative).splitlines():
        if not buffer and (not raw_line.strip() or raw_line.lstrip().startswith("#")):
            continue
        line = raw_line.rstrip()
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        buffer += line
        parts = buffer.strip().split(None, 1)
        if parts:
            instructions.append((parts[0].upper(), parts[1] if len(parts) > 1 else ""))
        buffer = ""
    if buffer.strip():
        parts = buffer.strip().split(None, 1)
        instructions.append((parts[0].upper(), parts[1] if len(parts) > 1 else ""))
    return instructions


def builder_go_version(relative: str | None = None) -> str:
    if relative is None:
        relative = resolve_targets()["operator"]["dockerfile"]
    for command, arguments in dockerfile_instructions(relative):
        if command != "FROM":
            continue
        match = re.search(r"go-toolset:(\d+\.\d+)", arguments)
        if match:
            return match.group(1)
    fail(f"{relative}: could not find a go-toolset builder version")
    raise AssertionError("unreachable")


def parse_copy(arguments: str) -> tuple[list[str], list[str], str]:
    remainder = arguments.strip()
    flags: list[str] = []
    while True:
        match = re.match(r"--\S+", remainder)
        if match is None:
            break
        flags.append(match.group(0))
        remainder = remainder[match.end():].lstrip()
    if not remainder:
        fail(f"COPY requires at least one source and a destination: {arguments!r}")

    operands: list[str]
    if remainder.startswith("["):
        try:
            value = json.loads(remainder)
        except json.JSONDecodeError as exc:
            fail(f"cannot parse COPY JSON arguments {arguments!r}: {exc}")
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            fail(f"COPY JSON arguments must be a string array: {arguments!r}")
        operands = value
    else:
        try:
            operands = shlex.split(remainder)
        except ValueError as exc:
            fail(f"cannot parse COPY arguments {arguments!r}: {exc}")

    if len(operands) < 2:
        fail(f"COPY requires at least one source and a destination: {arguments!r}")
    return flags, operands[:-1], operands[-1]


def normalize_source(source: str) -> str:
    if source.startswith("./"):
        return source[2:]
    return source


def assert_copy_sources(relative: str, context: str) -> int:
    context_path = (ROOT / context).resolve()
    checked = 0
    for command, arguments in dockerfile_instructions(relative):
        if command not in ("COPY", "ADD"):
            continue
        flags, sources, _destination = parse_copy(arguments)
        if any(flag.startswith("--from") for flag in flags):
            continue
        for source in sources:
            if source in (".", "./"):
                checked += 1
                continue
            source_path = Path(source)
            if source_path.is_absolute():
                fail(f"{relative}: COPY source must be relative to path-context: {source}")
            if ".." in source_path.parts:
                fail(f"{relative}: COPY source escapes path-context: {source}")
            pattern = str(context_path / source)
            if any(character in source for character in "*?["):
                matches = [
                    Path(match).resolve()
                    for match in glob.glob(pattern, recursive=True)
                ]
                if not matches:
                    fail(f"{relative}: COPY source glob matched nothing: {source}")
                for match in matches:
                    if context_path not in match.parents and match != context_path:
                        fail(
                            f"{relative}: COPY glob match escapes path-context: "
                            f"{source} -> {match}"
                        )
            else:
                resolved = Path(pattern).resolve()
                if not resolved.exists():
                    fail(f"{relative}: COPY source does not exist: {source}")
                if context_path not in resolved.parents and resolved != context_path:
                    fail(f"{relative}: COPY source escapes path-context: {source}")
            checked += 1
    ok(f"{relative}: {checked} local COPY/ADD sources exist under path-context")
    return checked


def instruction_values(
    instructions: list[tuple[str, str]], command: str
) -> list[str]:
    return [arguments for name, arguments in instructions if name == command]


def json_instruction(arguments: str, label: str) -> list[Any]:
    try:
        value = json.loads(arguments)
    except json.JSONDecodeError as exc:
        fail(f"{label} is not valid JSON: {arguments!r}: {exc}")
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        fail(f"{label} must be a JSON string array: {arguments!r}")
    return value


def final_stage(instructions: list[tuple[str, str]]) -> list[tuple[str, str]]:
    indexes = [index for index, (command, _) in enumerate(instructions) if command == "FROM"]
    if not indexes:
        fail("Dockerfile has no FROM instruction")
    return instructions[indexes[-1]:]


def stage_value(
    instructions: list[tuple[str, str]], variable: str
) -> str | None:
    value: str | None = None
    for command, arguments in instructions:
        if command == "ARG":
            try:
                tokens = shlex.split(arguments)
            except ValueError:
                continue
            for token in tokens:
                name, separator, default = token.partition("=")
                if name == variable:
                    value = default if separator else None
        elif command == "ENV":
            try:
                tokens = shlex.split(arguments)
            except ValueError:
                continue
            if tokens and "=" not in tokens[0]:
                if tokens[0] == variable and len(tokens) > 1:
                    value = " ".join(tokens[1:])
                continue
            for token in tokens:
                name, separator, default = token.partition("=")
                if name == variable:
                    value = default if separator else None
    return value


def assert_fips_manager_build(
    relative: str, instructions: list[tuple[str, str]]
) -> None:
    build_index = -1
    build_arguments = ""
    for index, (command, arguments) in enumerate(instructions):
        if command != "RUN" or "go build" not in arguments:
            continue
        if "cmd/main.go" in arguments and re.search(r"-o\s+\S*manager\b", arguments):
            build_index = index
            build_arguments = arguments
            break
    if build_index < 0:
        fail(f"{relative}: no RUN step builds the packaged manager with -o manager")

    stage_start = max(
        index
        for index, (command, _arguments) in enumerate(instructions)
        if command == "FROM" and index < build_index
    )
    cgo_default = stage_value(instructions[stage_start:build_index], "CGO_ENABLED")
    if cgo_default != "1":
        fail(
            f"{relative}: effective CGO_ENABLED for the packaged /manager is "
            f"{cgo_default!r}; the FIPS branch requires an ARG/ENV default of 1"
        )

    branch = re.search(
        r"if\s+\[\s*\"?\$\{?CGO_ENABLED\}?\"?\s*=\s*\"?1\"?\s*\]\s*;\s*then"
        r"(?P<fips>.*?)\belse\b(?P<nonfips>.*?)\bfi\b",
        build_arguments,
        re.DOTALL,
    )
    if not branch:
        fail(
            f"{relative}: manager build does not select the FIPS branch with "
            "'if [ \"$CGO_ENABLED\" = \"1\" ]'"
        )
    fips = branch.group("fips")
    nonfips = branch.group("nonfips")
    if not re.search(r"\bCGO_ENABLED=1\b", fips):
        fail(f"{relative}: FIPS manager branch must build with CGO_ENABLED=1")
    if "GOEXPERIMENT=strictfipsruntime" not in fips:
        fail(f"{relative}: FIPS manager branch must set GOEXPERIMENT=strictfipsruntime")
    if "-tags strictfipsruntime" not in fips:
        fail(f"{relative}: FIPS manager branch must pass -tags strictfipsruntime")
    if "CGO_ENABLED=0" not in nonfips:
        fail(f"{relative}: the non-FIPS branch must set CGO_ENABLED=0 for local builds")
    if "GOEXPERIMENT=strictfipsruntime" in nonfips or "-tags strictfipsruntime" in nonfips:
        fail(f"{relative}: the non-FIPS branch must not inherit the FIPS build flags")
    if not re.search(r"-o\s+(?:/workspace/)?manager\b", fips):
        fail(
            f"{relative}: FIPS manager branch must write the binary later copied "
            "to /manager"
        )
    if "cmd/main.go" not in fips:
        fail(f"{relative}: FIPS manager branch must build cmd/main.go")

    workdir = "/"
    for command, arguments in instructions[stage_start:build_index]:
        if command == "WORKDIR":
            workdir = arguments.strip()
    if workdir.rstrip("/") != "/workspace":
        fail(
            f"{relative}: builder WORKDIR is {workdir!r}, so '-o manager' is not "
            "the /workspace/manager the final image copies"
        )
    ok(
        f"{relative}: effective manager build selects CGO_ENABLED=1 with "
        "GOEXPERIMENT and -tags strictfipsruntime"
    )


def assert_operator_dockerfile(relative: str) -> None:
    instructions = dockerfile_instructions(relative)
    text = read_text(relative)
    stage = final_stage(instructions)

    copies = [parse_copy(arguments) for command, arguments in stage if command == "COPY"]
    manager_copy = any(
        "--from=builder" in flags
        and sources == ["/workspace/manager"]
        and destination in (".", "/manager")
        for flags, sources, destination in copies
    )
    chart_copy = any(
        "--from=builder" in flags
        and sources == ["/workspace/charts/mlflow"]
        and destination == "charts/mlflow"
        for flags, sources, destination in copies
    )
    if not manager_copy:
        fail(f"{relative}: final image must COPY --from=builder /workspace/manager to /manager")
    if not chart_copy:
        fail(f"{relative}: final image must COPY --from=builder /workspace/charts/mlflow to charts/mlflow")

    entrypoints = instruction_values(stage, "ENTRYPOINT")
    if not entrypoints or json_instruction(entrypoints[-1], f"{relative} ENTRYPOINT") != ["/manager"]:
        fail(f'{relative}: ENTRYPOINT must be ["/manager"] in the final image')
    users = instruction_values(stage, "USER")
    if not users or users[-1].strip() != "1001":
        fail(f"{relative}: final image must run as USER 1001")
    if "registry.access.redhat.com/ubi9/ubi-minimal" not in text:
        fail(f"{relative}: runtime base image must be the UBI 9 minimal image")
    assert_fips_manager_build(relative, instructions)

    builder_copies = [
        parse_copy(arguments)
        for command, arguments in instructions
        if command == "COPY"
    ]
    copied_sources = {
        source
        for flags, sources, _destination in builder_copies
        if not any(flag.startswith("--from") for flag in flags)
        for source in sources
    }
    download_indexes = [
        index
        for index, (command, arguments) in enumerate(instructions)
        if command == "RUN" and "go mod download" in arguments
    ]
    if not download_indexes:
        fail(f"{relative}: expected a 'go mod download' layer")
    download_index = download_indexes[0]
    before_download = {
        source
        for command, arguments in instructions[:download_index]
        if command == "COPY"
        for flags, sources, _destination in [parse_copy(arguments)]
        if not any(flag.startswith("--from") for flag in flags)
        for source in sources
    }
    required = {"go.mod", "go.sum", "api/go.mod", "api/go.sum"}
    missing = sorted(required - before_download)
    if missing:
        fail(
            f"{relative}: module manifests must be COPYed before 'go mod download': "
            + ", ".join(missing)
        )
    if not required.issubset(copied_sources):
        fail(f"{relative}: missing module manifest COPY in builder stage")
    ok(f"{relative}: operator image contents, entrypoint, user and FIPS flags match the contract")


def assert_tests_dockerfile(relative: str) -> None:
    instructions = dockerfile_instructions(relative)
    stage = final_stage(instructions)
    text = read_text(relative)

    froms = instruction_values(stage, "FROM")
    if not froms or not re.search(r"ubi-minimal:\d+\.\d+", froms[-1]):
        fail(f"{relative}: expected a pinned UBI minimal runtime base image")
    entrypoints = instruction_values(stage, "ENTRYPOINT")
    expected_entrypoint = ["bash", "mlflow-tests/images/test-run.sh"]
    if not entrypoints or json_instruction(entrypoints[-1], f"{relative} ENTRYPOINT") != expected_entrypoint:
        fail(f"{relative}: ENTRYPOINT must be {expected_entrypoint!r}")
    users = instruction_values(stage, "USER")
    if not users or users[-1].strip() != "1001":
        fail(f"{relative}: final image must run as USER 1001")
    if "ENV BASH_ENV=/mlflow/mlflow-tests/.bash_env" not in text:
        fail(f"{relative}: BASH_ENV must point at the baked MLFLOW_TEST_SUPPORTED_VERSION file")
    if "uv sync --locked" not in text:
        fail(f"{relative}: dependency install must use 'uv sync --locked'")
    if "print_supported_mlflow_version.py" not in text or "--normalized" not in text:
        fail(f"{relative}: supported version must be derived with --normalized")
    if "MLFLOW_TEST_SUPPORTED_VERSION" not in text:
        fail(f"{relative}: test image must expose MLFLOW_TEST_SUPPORTED_VERSION")

    copies = [parse_copy(arguments) for command, arguments in stage if command == "COPY"]
    copied_sources = {
        normalize_source(source)
        for _flags, sources, _destination in copies
        for source in sources
    }
    required = {
        "mlflow-tests/uv.lock",
        "mlflow-tests/pyproject.toml",
        "mlflow-tests/src",
        ".github/actions/deploy/deploy.py",
        ".github/test-infra",
        ".github/workflows/trace-analytics-prepopulation.yml",
        "config",
        "scripts",
        "mlflow-tests",
    }
    missing = sorted(required - copied_sources)
    if missing:
        fail(f"{relative}: missing required COPY sources: {', '.join(missing)}")
    ok(f"{relative}: tests image entrypoint, assets and version contract match")


def dockerignore_expression(pattern: str) -> re.Pattern[str]:
    pattern = posixpath.normpath(pattern.lstrip("/"))
    pieces = ["^"]
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*":
            if pattern[index:index + 2] == "**":
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    pieces.append("(?:.*/)?")
                    index += 1
                else:
                    pieces.append(".*")
                continue
            pieces.append("[^/]*")
        elif character == "?":
            pieces.append("[^/]")
        elif character == "[":
            end = index + 1
            if end < len(pattern) and pattern[end] in "!^":
                end += 1
            if end < len(pattern) and pattern[end] == "]":
                end += 1
            while end < len(pattern) and pattern[end] != "]":
                end += 1
            if end >= len(pattern):
                pieces.append(r"\[")
            else:
                content = pattern[index + 1:end]
                if content.startswith("!"):
                    content = "^" + content[1:]
                elif content.startswith("^"):
                    content = "\\" + content
                pieces.append(f"[{content}]")
                index = end
        else:
            pieces.append(re.escape(character))
        index += 1
    pieces.append("$")
    return re.compile("".join(pieces))


def dockerignore_included(lines: list[str], path: str) -> tuple[bool, str]:
    candidates = [path]
    parent = path
    while "/" in parent:
        parent = parent.rsplit("/", 1)[0]
        candidates.append(parent)
    included = True
    deciding_rule = "<default>"
    for line in lines:
        negated = line.startswith("!")
        pattern = line[1:] if negated else line
        pattern = posixpath.normpath(pattern.lstrip("/"))
        if pattern in ("", "."):
            continue
        expression = dockerignore_expression(pattern)
        if any(expression.match(candidate) for candidate in candidates):
            included = negated
            deciding_rule = line
    return included, deciding_rule


def assert_dockerignore_contract(targets: dict[str, dict[str, Any]]) -> None:
    lines = [
        line.strip()
        for line in read_text(".dockerignore").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    required = {
        "go.mod",
        "go.sum",
        "api",
        "cmd",
        "internal",
        "charts",
        "Makefile",
        "config",
        "scripts",
        "mlflow-tests",
        ".github/actions/deploy",
        ".github/test-infra",
        ".github/workflows/trace-analytics-prepopulation.yml",
    }
    required.update(target["dockerfile"] for target in targets.values())
    missing: list[str] = []
    for path in sorted(required):
        included, rule = dockerignore_included(lines, path)
        if not included:
            missing.append(f"{path} (excluded by {rule!r})")
            continue
        if (ROOT / path).is_dir():
            for probe in (f"{path}/__probe__", f"{path}/nested/__probe__"):
                probe_included, probe_rule = dockerignore_included(lines, probe)
                if not probe_included:
                    missing.append(f"{path} children (excluded by {probe_rule!r})")
                    break
    if missing:
        fail(
            ".dockerignore excludes required build inputs: " + ", ".join(missing)
        )
    ok(
        ".dockerignore ordered rules (last match wins) include every directory "
        "and file the two Konflux Dockerfiles COPY"
    )


def go_directive(relative: str) -> tuple[str, str]:
    text = read_text(relative)
    module = re.search(r"(?m)^module\s+(\S+)", text)
    go = re.search(r"(?m)^go\s+(\d+\.\d+(?:\.\d+)?)\s*$", text)
    if not module:
        fail(f"{relative}: missing module directive")
    if not go:
        fail(f"{relative}: missing go directive")
    return module.group(1), go.group(1)


def version_tuple(value: str) -> tuple[int, int, int]:
    parts = [int(part) for part in value.split(".")]
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def assert_modules(operator_dockerfile: str) -> None:
    root_module, root_go = go_directive("go.mod")
    api_module, api_go = go_directive("api/go.mod")
    if root_module != "github.com/opendatahub-io/mlflow-operator":
        fail(f"go.mod: unexpected module path {root_module!r}")
    if api_module != "github.com/opendatahub-io/mlflow-operator/api":
        fail(f"api/go.mod: unexpected module path {api_module!r}")
    replace = re.search(
        r"(?m)^replace\s+github\.com/opendatahub-io/mlflow-operator/api\s+=>\s+\./api\s*$",
        read_text("go.mod"),
    )
    if not replace:
        fail("go.mod: expected local replace of the api module with ./api")
    for relative in ("go.sum", "api/go.sum"):
        if not (ROOT / relative).is_file() or (ROOT / relative).stat().st_size == 0:
            fail(f"{relative}: go.sum must exist and be non-empty")

    builder = builder_go_version(operator_dockerfile)
    builder_tuple = version_tuple(builder)
    for relative, directive in (("go.mod", root_go), ("api/go.mod", api_go)):
        required = version_tuple(directive)
        if builder_tuple[:2] < required[:2]:
            fail(
                f"{operator_dockerfile} builder Go {builder} is older than {relative} go {directive}; "
                "GOTOOLCHAIN=local would fail"
            )
    ok(
        f"Go modules align with builder {builder}: go.mod={root_go}, api/go.mod={api_go}"
    )


def supported_versions() -> tuple[str, str]:
    raw = subprocess.check_output(
        [
            sys.executable,
            "scripts/print_supported_mlflow_version.py",
            "--component-metadata",
            "config/component_metadata.yaml",
        ],
        cwd=ROOT,
        text=True,
    ).strip()
    normalized = subprocess.check_output(
        [
            sys.executable,
            "scripts/print_supported_mlflow_version.py",
            "--component-metadata",
            "config/component_metadata.yaml",
            "--normalized",
        ],
        cwd=ROOT,
        text=True,
    ).strip()
    if not raw or not normalized:
        fail("supported MLflow version resolved to an empty value")
    return raw, normalized


def normalize_version(value: str) -> str:
    match = re.match(r"^[vV]?(\d+)\.(\d+)", value)
    if not match:
        fail(f"cannot normalize version {value!r}")
    return f"{int(match.group(1))}.{int(match.group(2))}"


def assert_supported_versions(operator_dockerfile: str, tests_dockerfile: str) -> None:
    raw, normalized = supported_versions()
    raw_normalized = normalize_version(raw)
    if raw_normalized != normalized:
        fail(
            f"supported version helpers disagree: raw={raw!r} normalized={normalized!r}"
        )

    makefile = read_text("Makefile")
    if "print_supported_mlflow_version.py --component-metadata config/component_metadata.yaml" not in makefile:
        fail("Makefile: supported version is not sourced from config/component_metadata.yaml")
    if "SupportedMLflowVersion=" not in makefile:
        fail("Makefile: SupportedMLflowVersion ldflag is missing")

    operator_text = read_text(operator_dockerfile)
    if "make -s print-supported-mlflow-version" not in operator_text:
        fail(f"{operator_dockerfile}: supported version is not injected through make print-supported-mlflow-version")
    if "SupportedMLflowVersion=" not in operator_text:
        fail(f"{operator_dockerfile}: SupportedMLflowVersion ldflag is missing")

    tests_text = read_text(tests_dockerfile)
    if "print_supported_mlflow_version.py" not in tests_text or "--normalized" not in tests_text:
        fail("mlflow-tests image: supported version is not derived from component metadata")
    if "MLFLOW_TEST_SUPPORTED_VERSION" not in tests_text:
        fail("mlflow-tests image: MLFLOW_TEST_SUPPORTED_VERSION is not baked into BASH_ENV")

    lock = tomllib.loads(read_text("mlflow-tests/uv.lock"))
    packages = lock.get("package", [])
    mlflow_package = next(
        (package for package in packages if package.get("name") == "mlflow"),
        None,
    )
    if mlflow_package is None:
        fail("mlflow-tests/uv.lock: no mlflow package entry")
    locked_version = str(mlflow_package.get("version", ""))
    if not locked_version:
        fail("mlflow-tests/uv.lock: mlflow package has no version")
    if normalize_version(locked_version) != normalized:
        fail(
            "supported MLflow version drift: component metadata normalizes to "
            f"{normalized}, mlflow-tests/uv.lock pins {locked_version}"
        )

    minor_file = f"test_{normalized.replace('.', '_')}.py"
    for phase in ("pre_upgrade", "post_upgrade"):
        path = ROOT / "mlflow-tests" / "tests" / "upgrade" / phase / minor_file
        if not path.is_file():
            fail(
                f"supported MLflow version drift: missing {phase}/{minor_file} "
                f"for component metadata {raw}"
            )

    ok(
        f"supported MLflow version aligns across metadata ({raw}), Makefile, "
        f"Dockerfiles, uv.lock ({locked_version}) and upgrade tests"
    )


def assert_servicemonitor_contract() -> None:
    controller = read_text("internal/controller/mlflowoperator_controller.go")
    match = re.search(
        r"func\s+schemeHTTPS\(\)\s+\*monitoringv1\.Scheme\s*\{(?P<body>.*?)\n\}",
        controller,
        re.DOTALL,
    )
    if not match:
        fail("internal/controller/mlflowoperator_controller.go: schemeHTTPS() was not found")
    body = match.group("body")
    if 'monitoringv1.Scheme("https")' not in body:
        fail('operator self-metrics ServiceMonitor must use monitoringv1.Scheme("https")')
    if "SchemeHTTPS" in body:
        fail("operator self-metrics ServiceMonitor regressed to uppercase monitoringv1.SchemeHTTPS")

    for constant in (
        "mlflow-operator-controller-manager-metrics-monitor",
        "mlflow-operator-controller-manager-metrics-service",
    ):
        if constant not in controller:
            fail(f"operator metrics contract changed: missing {constant}")

    chart_monitors = []
    for path in sorted((ROOT / "charts").rglob("*.yaml")):
        resolved = path.resolve()
        if not resolved.is_relative_to(ROOT):
            fail(f"chart file escapes the repository: {path}")
        text = resolved.read_text(encoding="utf-8")
        if "kind: ServiceMonitor" not in text:
            continue
        chart_monitors.append(path)
        if not re.search(r"(?m)^\s*scheme:\s*https\s*$", text):
            fail(f"{path.relative_to(ROOT)}: ServiceMonitor scheme must be lowercase https")
        if re.search(r"(?m)^\s*scheme:\s*HTTPS\s*$", text):
            fail(f"{path.relative_to(ROOT)}: ServiceMonitor scheme must not be uppercase HTTPS")
        if not re.search(r"(?m)^\s*(?:-\s*)?path:\s*/metrics\s*$", text):
            fail(f"{path.relative_to(ROOT)}: ServiceMonitor path must be /metrics")
    if not chart_monitors:
        fail("charts/: no ServiceMonitor template found")

    crd_relative = "test/crd/monitoring.coreos.com_servicemonitors.yaml"
    crd = load_yaml(crd_relative)
    try:
        versions = crd["spec"]["versions"]
        schema = next(
            version["schema"]["openAPIV3Schema"]
            for version in versions
            if version.get("name") == "v1"
        )
        endpoint_items = schema["properties"]["spec"]["properties"]["endpoints"]["items"]
        scheme_enum = endpoint_items["properties"]["scheme"]["enum"]
    except (KeyError, StopIteration, TypeError) as exc:
        fail(f"{crd_relative}: cannot locate the ServiceMonitor endpoint scheme schema: {exc}")
    if scheme_enum != ["http", "https"]:
        fail(
            f"{crd_relative}: scheme enum must be exactly ['http', 'https'] so the "
            f"fixture reproduces the Prometheus rejection of uppercase values; got {scheme_enum!r}"
        )

    prometheus_version = re.search(
        r"^\s*github\.com/prometheus-operator/prometheus-operator/pkg/apis/monitoring\s+v([0-9.]+)$",
        read_text("go.mod"),
        re.MULTILINE,
    )
    fixture_version = str(crd.get("metadata", {}).get("annotations", {}).get("operator.prometheus.io/version", ""))
    if not prometheus_version or not fixture_version:
        fail("cannot compare the ServiceMonitor CRD fixture version with go.mod")
    if prometheus_version.group(1) != fixture_version:
        fail(
            "ServiceMonitor CRD fixture drift: go.mod uses prometheus-operator "
            f"v{prometheus_version.group(1)}, fixture declares {fixture_version}"
        )

    ok(
        "ServiceMonitor contract is lowercase https with /metrics in Go and chart; "
        "the fixture rejects uppercase"
    )


def workflow_triggers(relative: str) -> dict[str, Any]:
    document = load_yaml(relative)
    triggers = document.get("on") or document.get(True) or {}
    if not isinstance(triggers, dict):
        fail(f"{relative}: unsupported workflow trigger definition")
    return triggers


def assert_generator_delegation() -> None:
    codegen_relative = ".github/workflows/verify-codegen.yml"
    codegen = read_text(codegen_relative)
    if "pull_request" not in workflow_triggers(codegen_relative):
        fail(f"{codegen_relative}: generated-code drift handling no longer runs on pull_request")
    for command in ("make manifests", "make generate"):
        if not re.search(rf"(?m)^\s*run:\s*{re.escape(command)}\s*$", codegen):
            fail(
                "generated-code drift handling is no longer delegated to "
                f"{codegen_relative}: missing a run step for {command!r}"
            )
    if not re.search(r"(?m)^\s*git diff --exit-code\b", codegen):
        fail(f"{codegen_relative}: generated-code drift no longer fails on a diff")
    kustomize_relative = ".github/workflows/verify-kustomize.yml"
    kustomize = read_text(kustomize_relative)
    if "pull_request" not in workflow_triggers(kustomize_relative):
        fail(f"{kustomize_relative}: manifest drift handling no longer runs on pull_request")
    if not re.search(r"(?m)^\s*run:\s*make verify-manifests\s*$", kustomize):
        fail(
            "manifest build drift handling is no longer delegated to "
            f"{kustomize_relative}: missing a run step for 'make verify-manifests'"
        )
    makefile = read_text("Makefile")
    for target in ("manifests:", "generate:", "verify-manifests:"):
        if target not in makefile:
            fail(f"Makefile: generator target {target} is missing")
    ok(
        "generated-code/manifest drift remains owned by verify-codegen.yml and "
        "verify-kustomize.yml; the simulator asserts that delegation instead of re-running them"
    )


def assert_runtime_contract() -> None:
    deploy = read_text(".github/actions/deploy/deploy.py")
    for flag in ("--skip-mlflow-cr", "--mlflow-operator-image", "--namespace"):
        if flag not in deploy:
            fail(f".github/actions/deploy/deploy.py: missing {flag}")
    workflow = read_text(".github/workflows/pr-build-validation.yml")
    mlflow_images = re.findall(r"(?m)^\s*MLFLOW_IMAGE:\s*(\S+)\s*$", workflow)
    if len(mlflow_images) != 1:
        fail(
            "pr-build-validation.yml: MLFLOW_IMAGE must be defined exactly once "
            "so a job-level override cannot restore a mutable reference"
        )
    if not re.fullmatch(r"\S+@sha256:[0-9a-f]{64}", mlflow_images[0]):
        fail(
            "pr-build-validation.yml: MLFLOW_IMAGE must pin the runtime by digest "
            f"(got {mlflow_images[0]!r})"
        )
    manager = read_text("config/manager/manager.yaml")
    if "ENABLE_MLFLOW_OPERATOR_MODULE_CONTROLLER" not in manager:
        fail("config/manager/manager.yaml: module controller toggle is missing")
    if not re.search(
        r"name:\s*ENABLE_MLFLOW_OPERATOR_MODULE_CONTROLLER\s*\n\s*value:\s*\"false\"",
        manager,
    ):
        fail("config/manager/manager.yaml: module controller must stay disabled by default")
    for relative in (
        "config/samples/mlflow_v1_mlflow_minimal.yaml",
        "test/crd/monitoring.coreos.com_servicemonitors.yaml",
        "test/crd/httproutes.gateway.networking.k8s.io.yaml",
        "mlflow-tests/images/test-run.sh",
    ):
        if not (ROOT / relative).is_file():
            fail(f"runtime contract input is missing: {relative}")
    ok("Kind integration inputs exist and the module controller default stays off")


def assert_tekton_contracts() -> None:
    targets = resolve_targets()
    print("Resolved Konflux PR targets:")
    for role in ("operator", "tests"):
        target = targets[role]
        info(
            f"{role}: image={target['image']} dockerfile={target['dockerfile']} "
            f"context={target['context']} hermetic={target['hermetic']} "
            f"platforms={','.join(target['platforms'])}"
        )
        if target["hermetic"] != "false":
            fail(
                f"{role}: hermetic={target['hermetic']!r}; this simulator's offline "
                "preflight assumes the PR pipeline is non-hermetic"
            )
        normalized_platforms = {platform.replace("x86_64", "amd64") for platform in target["platforms"]}
        if normalized_platforms != {"linux/amd64"}:
            fail(
                f"{role}: build-platforms={target['platforms']!r}; expected the "
                "odh-konflux-central default linux/x86_64"
            )
        if "odh-pr-{{revision}}" not in target["additionalTags"]:
            fail(f"{role}: additional-tags must include odh-pr-{{{{revision}}}}")
    ok(
        "Tekton targets resolve to two single-platform non-hermetic images with "
        "existing Dockerfiles and build contexts"
    )


def run_static() -> None:
    print("=== Konflux static build contracts ===")
    assert_tekton_contracts()
    targets = resolve_targets()
    for role in ("operator", "tests"):
        assert_copy_sources(targets[role]["dockerfile"], targets[role]["context"])
    assert_dockerignore_contract(targets)
    assert_operator_dockerfile(targets["operator"]["dockerfile"])
    assert_tests_dockerfile(targets["tests"]["dockerfile"])
    assert_modules(targets["operator"]["dockerfile"])
    assert_supported_versions(targets["operator"]["dockerfile"], targets["tests"]["dockerfile"])
    assert_servicemonitor_contract()
    assert_generator_delegation()
    assert_runtime_contract()
    print("✅ All static build contracts passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("targets", "target-env", "go-version", "static"),
    )
    parser.add_argument("role", nargs="?")
    args = parser.parse_args()

    if args.command == "targets":
        print(json.dumps(resolve_targets(), indent=2, sort_keys=True))
    elif args.command == "target-env":
        if not args.role:
            fail("target-env requires a role (operator or tests)")
        emit_target_env(args.role)
    elif args.command == "go-version":
        print(builder_go_version())
    elif args.command == "static":
        run_static()


if __name__ == "__main__":
    main()

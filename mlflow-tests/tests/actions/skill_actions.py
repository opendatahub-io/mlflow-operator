"""Skill Registry action functions."""

import tempfile
import uuid
from pathlib import Path

import mlflow

from ..shared import TestContext


def _skill_name(test_context: TestContext) -> str:
    return test_context.active_skill_name or (
        f"test-skill-{uuid.uuid4().hex[:12]}"
    )


def action_create_skill_shell(test_context: TestContext) -> None:
    name = _skill_name(test_context)
    description = "MLflow Skill Registry E2E test Skill"
    skill = test_context.user_client.create_skill(
        name=name,
        description=description,
    )
    test_context.active_skill_name = skill.name
    test_context.active_skill_organization = skill.organization
    test_context.active_skill_description = description
    test_context.add_skill_for_cleanup(
        name=skill.name,
        organization=skill.organization,
        workspace=test_context.active_workspace,
    )


def action_get_skill(test_context: TestContext) -> None:
    skill = test_context.user_client.get_skill(
        name=test_context.active_skill_name,
        organization=test_context.active_skill_organization or "",
    )
    test_context.active_skill_name = skill.name
    test_context.active_skill_description = skill.description


def action_search_skills(test_context: TestContext) -> None:
    test_context.skill_search_results = list(
        test_context.user_client.search_skills()
    )


def action_update_skill(test_context: TestContext) -> None:
    test_context.user_client.update_skill(
        name=test_context.active_skill_name,
        organization=test_context.active_skill_organization or "",
        description="Updated Skill Registry E2E description",
    )


def action_delete_skill(test_context: TestContext) -> None:
    test_context.user_client.delete_skill(
        name=test_context.active_skill_name,
        organization=test_context.active_skill_organization or "",
    )


def action_register_local_skill(test_context: TestContext) -> None:
    """Register a local Skill directory and retain its artifact source."""
    name = _skill_name(test_context)
    test_context.active_skill_name = name
    skill_content = "# Skill Registry E2E test Skill\n"

    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir)
        skill_markdown = (
            f"---\nname: {name}\ndescription: E2E test Skill\n---\n{skill_content}"
        )
        (skill_dir / "SKILL.md").write_text(skill_markdown, encoding="utf-8")
        version = mlflow.genai.register_skill(source=str(skill_dir))

    test_context.active_skill_name = version.name
    test_context.active_skill_organization = version.organization
    test_context.active_skill_version = version.version
    test_context.active_skill_source = version.source
    test_context.expected_skill_artifact_content = skill_markdown
    test_context.add_skill_for_cleanup(
        name=version.name,
        organization=version.organization,
        workspace=test_context.active_workspace,
    )


def action_read_skill_artifact(test_context: TestContext) -> None:
    """Read SKILL.md through the MLflow artifact endpoint."""
    source = test_context.active_skill_source
    artifact_uri = getattr(source, "artifact_path", None) or str(source)
    test_context.active_skill_artifact_content = mlflow.artifacts.load_text(
        f"{artifact_uri}/SKILL.md"
    )

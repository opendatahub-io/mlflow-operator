"""Skill Registry action functions."""

import subprocess
import tempfile
import uuid
from pathlib import Path

import mlflow
from mlflow.genai import GitSource

from ..shared import ActiveSkill, TestContext


def _active_skill(test_context: TestContext) -> ActiveSkill:
    if test_context.active_skill is None or not test_context.active_skill.name:
        raise AssertionError("No active Skill is available in the test context")
    return test_context.active_skill


def _skill_name(test_context: TestContext) -> str:
    return (
        test_context.active_skill.name
        if test_context.active_skill and test_context.active_skill.name
        else f"test-skill-{uuid.uuid4().hex[:12]}"
    )


def action_create_skill_shell(test_context: TestContext) -> None:
    name = _skill_name(test_context)
    description = "MLflow Skill Registry E2E test Skill"
    skill = test_context.user_client.create_skill(
        name=name,
        description=description,
    )
    test_context.active_skill = ActiveSkill(
        name=skill.name,
        organization=skill.organization,
        description=description,
    )
    test_context.add_skill_for_cleanup(
        name=skill.name,
        organization=skill.organization,
        workspace=test_context.active_workspace,
    )


def action_get_skill(test_context: TestContext) -> None:
    active_skill = _active_skill(test_context)
    skill = test_context.user_client.get_skill(
        name=active_skill.name,
        organization=active_skill.organization or "",
    )
    active_skill.name = skill.name
    active_skill.description = skill.description


def action_search_skills(test_context: TestContext) -> None:
    test_context.skill_search_results = list(
        test_context.user_client.search_skills()
    )


def action_update_skill(test_context: TestContext) -> None:
    active_skill = _active_skill(test_context)
    test_context.user_client.update_skill(
        name=active_skill.name,
        organization=active_skill.organization or "",
        description="Updated Skill Registry E2E description",
    )


def action_delete_skill(test_context: TestContext) -> None:
    active_skill = _active_skill(test_context)
    test_context.user_client.delete_skill(
        name=active_skill.name,
        organization=active_skill.organization or "",
    )


def action_register_local_skill(
    body: str = "# Skill Registry E2E test Skill\n",
    status: str = "active",
    name: str | None = None,
):
    """Return an action that registers the next version of the active Skill from a local folder.

    ``body`` is the new version's SKILL.md text; scenarios that don't check it can omit it. The
    first call creates the Skill as version 1, under ``name`` or a random name; each later call
    adds the next version. ``status`` is the new version's status, such as "draft".
    """

    def action(test_context: TestContext) -> None:
        skill_name = name or _skill_name(test_context)
        # Set the name first, so a validator can look the Skill up if registration fails.
        test_context.active_skill = test_context.active_skill or ActiveSkill()
        test_context.active_skill.name = skill_name
        skill_markdown = (
            f"---\nname: {skill_name}\ndescription: E2E test Skill\n---\n{body}"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "SKILL.md").write_text(skill_markdown, encoding="utf-8")
            version = mlflow.genai.register_skill(source=tmpdir, status=status)

        test_context.active_skill.organization = version.organization
        test_context.active_skill.version = version.version
        test_context.active_skill.source = version.source
        test_context.expected_skill_artifact_content = skill_markdown
        test_context.add_skill_for_cleanup(
            name=version.name,
            organization=version.organization,
            workspace=test_context.active_workspace,
        )

    action.__name__ = f"action_register_local_skill_{status}"
    return action



def action_read_skill_artifact(test_context: TestContext) -> None:
    """Read SKILL.md through the MLflow artifact endpoint."""
    active_skill = _active_skill(test_context)
    source = active_skill.source
    artifact_uri = getattr(source, "artifact_path", None) or str(source)
    active_skill.artifact_content = mlflow.artifacts.load_text(
        f"{artifact_uri}/SKILL.md"
    )


def _skill_uri(skill: ActiveSkill) -> str:
    qualified = f"@{skill.organization}/{skill.name}" if skill.organization else skill.name
    return f"skills:/{qualified}"


def action_set_skill_alias(alias: str, version: int):
    """Return an action that points ``alias`` of the active Skill at ``version``."""

    def action(test_context: TestContext) -> None:
        active_skill = _active_skill(test_context)
        test_context.user_client.set_skill_alias(
            name=active_skill.name,
            organization=active_skill.organization or "",
            alias=alias,
            version=version,
        )

    action.__name__ = f"action_set_skill_alias_{alias}_to_{version}"
    return action


def action_set_skill_version_status(version: int, status: str):
    """Return an action that sets the status of a version of the active Skill."""

    def action(test_context: TestContext) -> None:
        active_skill = _active_skill(test_context)
        test_context.user_client.update_skill_version(
            name=active_skill.name,
            organization=active_skill.organization or "",
            version=version,
            status=status,
        )

    action.__name__ = f"action_set_skill_version_{version}_{status}"
    return action


def action_delete_skill_version(version: int):
    """Return an action that deletes a version of the active Skill."""

    def action(test_context: TestContext) -> None:
        active_skill = _active_skill(test_context)
        test_context.user_client.delete_skill_version(
            name=active_skill.name,
            organization=active_skill.organization or "",
            version=version,
        )

    action.__name__ = f"action_delete_skill_version_{version}"
    return action


def action_pull_skill(*, alias: str | None = None, version: int | None = None):
    """Return an action that pulls the active Skill the way an agent loads it.

    It pulls the version ``alias`` points at, or ``version``, or the latest version when neither
    is given, and keeps the pulled SKILL.md text for ``validate_pulled_skill``.
    """
    if alias:
        selector, label = f"@{alias}", f"alias_{alias}"
    elif version:
        selector, label = f"/{version}", f"version_{version}"
    else:
        selector, label = "", "latest"

    def action(test_context: TestContext) -> None:
        active_skill = _active_skill(test_context)
        with tempfile.TemporaryDirectory() as tmpdir:
            pulled = mlflow.genai.pull(
                f"{_skill_uri(active_skill)}{selector}",
                destination=Path(tmpdir) / "pulled",
            )
            active_skill.artifact_content = (Path(pulled) / "SKILL.md").read_text(
                encoding="utf-8"
            )

    action.__name__ = f"action_pull_skill_{label}"
    return action


def action_push_to_skill_repo(
    repo: Path, *, skills: dict[str, str] | None = None, readme: str | None = None
):
    """Return an action that commits to a local Git repository, the way a developer pushes.

    ``repo`` stands in for the team's Git remote; the first call creates it. ``skills`` maps each
    Skill name to its new SKILL.md text, and ``readme`` is new README text. Files that are not
    given keep their content. This touches only Git, not MLflow.
    """

    def action(test_context: TestContext) -> None:
        if not (repo / ".git").exists():
            repo.mkdir(parents=True)
            _git(repo, "init", "--quiet", "--initial-branch=main")
        for name, body in (skills or {}).items():
            (repo / name).mkdir(exist_ok=True)
            (repo / name / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: E2E test Skill\n---\n{body}", encoding="utf-8"
            )
        if readme is not None:
            (repo / "README.md").write_text(readme, encoding="utf-8")
        _git(repo, "add", "--all")
        _git(repo, "commit", "--quiet", "--message", "Update Skills")

    action.__name__ = "action_push_to_skill_repo"
    return action


def action_import_skill_repo(repo: Path):
    """Return an action that imports every Skill on branch main of a Git repository into MLflow.

    Scenario: the team's CI job runs this after each human-reviewed push to Git. ``import_skills``
    registers all Skills in one request, and reuses a Skill's existing version when nothing about
    the Skill changed.
    """

    def action(test_context: TestContext) -> None:
        versions = mlflow.genai.import_skills(source=GitSource(url=repo.as_uri(), ref="main"))
        for version in versions:
            test_context.add_skill_for_cleanup(
                name=version.name,
                organization=version.organization,
                workspace=test_context.active_workspace,
            )

    action.__name__ = "action_import_skill_repo"
    return action


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=E2E", "-c", "user.email=e2e@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )

"""Skill Registry user journeys under Kubernetes RBAC.

Extends the tests in test_skill_registry.py. Each scenario simulates a team using MLflow's Skill
Registry. A publisher (a person, or the team's CI job) registers and manages the team's Skills.
The team's code-review agent runs in CI on every pull request and loads the Skill at the
"production" alias.

A ``TestStep`` without ``user_info`` means the persona from the step before does that action.
Comments were added in places to be explicit.
"""

import logging
import tempfile
import uuid
from pathlib import Path
from typing import ClassVar

import mlflow
import pytest
from mlflow_tests.enums import KubeVerb, ResourceType

from .actions import (
    action_delete_skill,
    action_delete_skill_version,
    action_get_skill,
    action_import_skill_repo,
    action_pull_skill,
    action_push_to_skill_repo,
    action_register_local_skill,
    action_set_skill_alias,
    action_set_skill_version_status,
)
from .base import TestBase
from .constants.config import Config
from .shared import TestData, TestStep, UserInfo
from .validations import (
    validate_authentication_denied,
    validate_no_error,
    validate_pulled_skill,
    validate_skill_deleted,
    validate_skill_versions,
)

logger = logging.getLogger(__name__)

SKILL_RESOURCE = [ResourceType.SKILLS]
TEAM_A_WORKSPACE = Config.WORKSPACES[0]
TEAM_B_WORKSPACE = Config.WORKSPACES[1]
SHARED_SKILL_NAME = f"code-review-{uuid.uuid4().hex[:8]}"

# A Git repo in a temp folder stands in for the team's remote: committing to it is a push.
# Import reads Git on the machine running it and sends MLflow only metadata, so a file:// URL
# works. Pull refuses file:// sources, so this journey checks version numbers, not content.
TEAM_REPO = Path(tempfile.gettempdir()) / f"team-skills-{uuid.uuid4().hex[:8]}"
LINT_SKILL = f"lint-check-{uuid.uuid4().hex[:8]}"
SECURITY_SKILL = f"security-check-{uuid.uuid4().hex[:8]}"

# The SKILL.md text of each version. Each version's text is different, so a pulled text shows
# which version the agent got.
LINT_V1 = "# Lint check\n\nRun the linter.\n"
LINT_V2 = "# Lint check\n\nRun the linter and the formatter.\n"
SECURITY_V1 = "# Security check\n\nFlag hard-coded secrets.\n"
CODE_REVIEW_V1 = "# Code review\n\nFlag changes that add no tests.\n"
CODE_REVIEW_V2 = "# Code review\n\nFlag changes that add no tests or hard-code secrets.\n"
CODE_REVIEW_FROM_AGENT = "# Code review\n\nApprove everything.\n"
TEAM_B_CODE_REVIEW = "# Code review\n\nFlag notebooks without a fixed random seed.\n"

# Journeys that pull uploaded content need MLflow's artifact serving.
SERVES_ARTIFACTS = Config.SERVE_ARTIFACTS or (
    Config.ARTIFACT_STORAGE == "file" and not Config.ARTIFACTS_SERVER
)


def _publisher(workspace: str) -> UserInfo:
    """A person or CI job that publishes the team's Skills and manages their versions."""
    return UserInfo(
        workspace=workspace,
        verbs=[
            KubeVerb.CREATE,
            KubeVerb.GET,
            KubeVerb.LIST,
            KubeVerb.UPDATE,
            KubeVerb.DELETE,
        ],
        resource_types=SKILL_RESOURCE,
    )


def _review_agent(workspace: str) -> UserInfo:
    """The team's code-review agent: a program in a pod, such as a CI pipeline step.

    It authenticates with this ServiceAccount's token, and only reads Skills to load them.
    """
    return UserInfo(
        workspace=workspace,
        verbs=[KubeVerb.GET, KubeVerb.LIST],
        resource_types=SKILL_RESOURCE,
    )


@pytest.mark.SkillRegistry
@pytest.mark.smoke
class TestSkillRegistryJourneys(TestBase):
    test_scenarios: ClassVar[list[TestData]] = [
        # Use case: the team keeps its Skills in a Git repository, and developers push changes to
        # it. After each push, a CI job outside MLflow imports the repository into MLflow.
        TestData(
            test_name=(
                "Developers push Skills to Git and CI imports them after each push; "
                "only a changed Skill gets a new version"
            ),
            workspace_to_use=TEAM_A_WORKSPACE,
            test_steps=[
                # A developer pushes two Skills to the team's repository. A push touches only Git,
                # so this step needs no MLflow persona.
                TestStep(
                    action_func=action_push_to_skill_repo(
                        TEAM_REPO, skills={LINT_SKILL: LINT_V1, SECURITY_SKILL: SECURITY_V1}
                    ),
                    validate_func=validate_no_error,
                ),
                # The CI job runs with the publisher persona. Its first import registers both
                # Skills as v1.
                TestStep(
                    action_func=action_import_skill_repo(TEAM_REPO),
                    validate_func=validate_skill_versions({LINT_SKILL: [1], SECURITY_SKILL: [1]}),
                    user_info=_publisher(TEAM_A_WORKSPACE),
                ),
                # A developer pushes a change to the lint Skill only.
                TestStep(
                    action_func=action_push_to_skill_repo(TEAM_REPO, skills={LINT_SKILL: LINT_V2}),
                    validate_func=validate_no_error,
                ),
                # CI imports again: the lint Skill gets v2, and the security Skill keeps v1.
                TestStep(
                    action_func=action_import_skill_repo(TEAM_REPO),
                    validate_func=validate_skill_versions(
                        {LINT_SKILL: [1, 2], SECURITY_SKILL: [1]}
                    ),
                ),
                # A developer pushes a README change, which changes no Skill.
                TestStep(
                    action_func=action_push_to_skill_repo(TEAM_REPO, readme="Team Skills"),
                    validate_func=validate_no_error,
                ),
                # CI imports again and adds no version.
                TestStep(
                    action_func=action_import_skill_repo(TEAM_REPO),
                    validate_func=validate_skill_versions(
                        {LINT_SKILL: [1, 2], SECURITY_SKILL: [1]}
                    ),
                ),
            ],
        ),
    ]

    if SERVES_ARTIFACTS:
        test_scenarios += [
            TestData(
                test_name="Publisher releases a Skill and the review agent follows the production alias",
                workspace_to_use=TEAM_A_WORKSPACE,
                test_steps=[
                    # The publisher registers v1 and points "production" at it.
                    TestStep(
                        action_func=action_register_local_skill(CODE_REVIEW_V1),
                        validate_func=validate_no_error,
                        user_info=_publisher(TEAM_A_WORKSPACE),
                    ),
                    TestStep(
                        action_func=action_set_skill_alias("production", version=1),
                        validate_func=validate_no_error,
                    ),
                    # The review agent runs in CI on every pull request, and each run pulls
                    # "production". It gets v1.
                    TestStep(
                        action_func=action_pull_skill(alias="production"),
                        validate_func=validate_pulled_skill(CODE_REVIEW_V1),
                        user_info=_review_agent(TEAM_A_WORKSPACE),
                    ),
                    # The publisher adds v2 as a draft.
                    TestStep(
                        action_func=action_register_local_skill(CODE_REVIEW_V2, status="draft"),
                        validate_func=validate_no_error,
                        user_info=_publisher(TEAM_A_WORKSPACE),
                    ),
                    # The agent's latest is still v1, because v2 is only a draft.
                    TestStep(
                        action_func=action_pull_skill(),
                        validate_func=validate_pulled_skill(CODE_REVIEW_V1),
                        user_info=_review_agent(TEAM_A_WORKSPACE),
                    ),
                    # The publisher activates v2 and moves "production" to it.
                    TestStep(
                        action_func=action_set_skill_version_status(2, "active"),
                        validate_func=validate_no_error,
                        user_info=_publisher(TEAM_A_WORKSPACE),
                    ),
                    TestStep(
                        action_func=action_set_skill_alias("production", version=2),
                        validate_func=validate_no_error,
                    ),
                    # The agent's next run gets v2, through "production" and as the latest.
                    TestStep(
                        action_func=action_pull_skill(alias="production"),
                        validate_func=validate_pulled_skill(CODE_REVIEW_V2),
                        user_info=_review_agent(TEAM_A_WORKSPACE),
                    ),
                    TestStep(
                        action_func=action_pull_skill(),
                        validate_func=validate_pulled_skill(CODE_REVIEW_V2),
                    ),
                    # Still the agent: it can read Skills but cannot register versions or move
                    # aliases.
                    TestStep(
                        action_func=action_register_local_skill(CODE_REVIEW_FROM_AGENT),
                        validate_func=validate_authentication_denied,
                    ),
                    TestStep(
                        action_func=action_set_skill_alias("production", version=1),
                        validate_func=validate_authentication_denied,
                    ),
                ],
            ),
            TestData(
                test_name="Publisher withdraws a bad Skill version and the review agent falls back",
                workspace_to_use=TEAM_A_WORKSPACE,
                test_steps=[
                    # The publisher registers v1 and v2, and points "production" at v2.
                    TestStep(
                        action_func=action_register_local_skill(CODE_REVIEW_V1),
                        validate_func=validate_no_error,
                        user_info=_publisher(TEAM_A_WORKSPACE),
                    ),
                    TestStep(
                        action_func=action_register_local_skill(CODE_REVIEW_V2),
                        validate_func=validate_no_error,
                    ),
                    TestStep(
                        action_func=action_set_skill_alias("production", version=2),
                        validate_func=validate_no_error,
                    ),
                    # The agent's runs use v2.
                    TestStep(
                        action_func=action_pull_skill(alias="production"),
                        validate_func=validate_pulled_skill(CODE_REVIEW_V2),
                        user_info=_review_agent(TEAM_A_WORKSPACE),
                    ),
                    # v2 turns out to be bad. The publisher moves "production" back to v1, then
                    # withdraws v2: an active version must be deprecated before it is deleted.
                    TestStep(
                        action_func=action_set_skill_alias("production", version=1),
                        validate_func=validate_no_error,
                        user_info=_publisher(TEAM_A_WORKSPACE),
                    ),
                    TestStep(
                        action_func=action_set_skill_version_status(2, "deprecated"),
                        validate_func=validate_no_error,
                    ),
                    TestStep(
                        action_func=action_delete_skill_version(2),
                        validate_func=validate_no_error,
                    ),
                    # The agent's next run gets v1 through "production", and v2 no longer exists.
                    TestStep(
                        action_func=action_pull_skill(alias="production"),
                        validate_func=validate_pulled_skill(CODE_REVIEW_V1),
                        user_info=_review_agent(TEAM_A_WORKSPACE),
                    ),
                    TestStep(
                        action_func=action_pull_skill(version=2),
                        validate_func=validate_skill_deleted,
                    ),
                    # The publisher deletes the whole Skill, and the agent can no longer find it.
                    TestStep(
                        action_func=action_delete_skill,
                        validate_func=validate_no_error,
                        user_info=_publisher(TEAM_A_WORKSPACE),
                    ),
                    TestStep(
                        action_func=action_get_skill,
                        validate_func=validate_skill_deleted,
                        user_info=_review_agent(TEAM_A_WORKSPACE),
                    ),
                ],
            ),
            TestData(
                test_name="Teams in two workspaces keep separate Skills with the same name",
                workspace_to_use=TEAM_A_WORKSPACE,
                test_steps=[
                    # Team A's and team B's publishers each register a Skill with the same name.
                    TestStep(
                        action_func=action_register_local_skill(
                            CODE_REVIEW_V1, name=SHARED_SKILL_NAME
                        ),
                        validate_func=validate_no_error,
                        user_info=_publisher(TEAM_A_WORKSPACE),
                    ),
                    TestStep(
                        action_func=action_register_local_skill(
                            TEAM_B_CODE_REVIEW, name=SHARED_SKILL_NAME
                        ),
                        validate_func=validate_no_error,
                        workspace_to_use=TEAM_B_WORKSPACE,
                        user_info=_publisher(TEAM_B_WORKSPACE),
                    ),
                    # Team B's agent gets team B's Skill, and is denied in team A's workspace.
                    TestStep(
                        action_func=action_pull_skill(),
                        validate_func=validate_pulled_skill(TEAM_B_CODE_REVIEW),
                        user_info=_review_agent(TEAM_B_WORKSPACE),
                    ),
                    TestStep(
                        action_func=action_get_skill,
                        validate_func=validate_authentication_denied,
                        workspace_to_use=TEAM_A_WORKSPACE,
                    ),
                    # Team A's publisher deletes team A's Skill; team B's agent still gets team B's.
                    TestStep(
                        action_func=action_delete_skill,
                        validate_func=validate_no_error,
                        user_info=_publisher(TEAM_A_WORKSPACE),
                    ),
                    TestStep(
                        action_func=action_pull_skill(),
                        validate_func=validate_pulled_skill(TEAM_B_CODE_REVIEW),
                        workspace_to_use=TEAM_B_WORKSPACE,
                        user_info=_review_agent(TEAM_B_WORKSPACE),
                    ),
                ],
            ),
        ]

    @pytest.mark.parametrize(
        "test_data",
        test_scenarios,
        ids=lambda scenario: scenario.test_name,
    )
    def test_skill_journey(self, test_data):
        logger.info("Starting journey: %s", test_data.test_name)
        self.test_context.active_workspace = test_data.workspace_to_use
        mlflow.set_workspace(test_data.workspace_to_use)
        self._execute_test_steps(test_data=test_data)

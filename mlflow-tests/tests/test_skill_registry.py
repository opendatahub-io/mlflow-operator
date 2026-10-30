"""Skill Registry Kubernetes authorization tests."""

import logging
import uuid
from typing import ClassVar

import mlflow
import pytest
from mlflow_tests.enums import KubeVerb, ResourceType

from .actions import (
    action_create_skill_shell,
    action_delete_skill,
    action_get_skill,
    action_read_skill_artifact,
    action_register_local_skill,
    action_search_skills,
    action_update_skill,
)
from .base import TestBase
from .constants.config import Config
from .shared import ActiveSkill, TestData, TestStep, UserInfo
from .validations import (
    validate_authentication_denied,
    validate_authentication_denied_or_resource_not_found,
    validate_no_error,
    validate_skill_artifact_read,
    validate_skill_artifact_registered,
    validate_skill_artifact_registration_unavailable,
    validate_skill_created,
    validate_skill_deleted,
    validate_skill_retrieved,
    validate_skill_search_excludes_other_workspace,
    validate_skill_search_includes_active_skill,
    validate_skill_updated,
)

logger = logging.getLogger(__name__)

SKILL_RESOURCE = [ResourceType.SKILLS]
WORKSPACE = Config.WORKSPACES[0]
OTHER_WORKSPACE = Config.WORKSPACES[1]
SCOPED_SKILL_NAME = f"scoped-skill-{uuid.uuid4().hex[:12]}"
OTHER_GET_SKILL_NAME = f"other-get-skill-{uuid.uuid4().hex[:12]}"
OTHER_UPDATE_SKILL_NAME = f"other-update-skill-{uuid.uuid4().hex[:12]}"
OTHER_DELETE_SKILL_NAME = f"other-delete-skill-{uuid.uuid4().hex[:12]}"
SCOPED_UPDATE_SKILL_NAME = f"scoped-update-skill-{uuid.uuid4().hex[:12]}"
SCOPED_DELETE_SKILL_NAME = f"scoped-delete-skill-{uuid.uuid4().hex[:12]}"
SEARCH_SKILL_NAME = f"search-skill-{uuid.uuid4().hex[:12]}"


def _seed_skill_name(name: str):
    def action(test_context):
        test_context.active_skill = ActiveSkill(name=name)

    action.__name__ = f"seed_skill_name_{name}"
    return action


@pytest.mark.SkillRegistry
@pytest.mark.smoke
class TestSkillRegistry(TestBase):
    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name="User with CREATE can create a bare Skill via the direct client API",
            user_info=UserInfo(
                workspace=WORKSPACE,
                verbs=[KubeVerb.CREATE],
                resource_types=SKILL_RESOURCE,
            ),
            workspace_to_use=WORKSPACE,
            test_steps=TestStep(
                action_func=action_create_skill_shell,
                validate_func=validate_skill_created,
            ),
        ),
        TestData(
            test_name="User with UPDATE cannot create a bare Skill via the direct client API without CREATE",
            user_info=UserInfo(
                workspace=WORKSPACE,
                verbs=[KubeVerb.UPDATE],
                resource_types=SKILL_RESOURCE,
            ),
            workspace_to_use=WORKSPACE,
            test_steps=TestStep(
                action_func=action_create_skill_shell,
                validate_func=validate_authentication_denied,
            ),
        ),
        TestData(
            test_name="User with GET can retrieve a Skill",
            workspace_to_use=WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=action_create_skill_shell,
                    validate_func=validate_skill_created,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.CREATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
                TestStep(
                    action_func=action_get_skill,
                    validate_func=validate_skill_retrieved,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.GET],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
            ],
        ),
        TestData(
            test_name="User without GET cannot retrieve a Skill",
            workspace_to_use=WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=action_create_skill_shell,
                    validate_func=validate_skill_created,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.CREATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
                TestStep(
                    action_func=action_get_skill,
                    validate_func=validate_authentication_denied,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.LIST],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
            ],
        ),
        TestData(
            test_name="User with LIST can search Skills",
            workspace_to_use=WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=action_create_skill_shell,
                    validate_func=validate_skill_created,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.CREATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
                TestStep(
                    action_func=action_search_skills,
                    validate_func=validate_skill_search_includes_active_skill,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.LIST],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
            ],
        ),
        TestData(
            test_name="User without LIST cannot search Skills",
            user_info=UserInfo(
                workspace=WORKSPACE,
                verbs=[KubeVerb.GET],
                resource_types=SKILL_RESOURCE,
            ),
            workspace_to_use=WORKSPACE,
            test_steps=TestStep(
                action_func=action_search_skills,
                validate_func=validate_authentication_denied,
            ),
        ),
        TestData(
            test_name="User with UPDATE can update a Skill",
            workspace_to_use=WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=action_create_skill_shell,
                    validate_func=validate_skill_created,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.CREATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
                TestStep(
                    action_func=action_update_skill,
                    validate_func=validate_no_error,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.UPDATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
            ],
        ),
        TestData(
            test_name="User without UPDATE cannot update a Skill",
            workspace_to_use=WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=action_create_skill_shell,
                    validate_func=validate_skill_created,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.CREATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
                TestStep(
                    action_func=action_update_skill,
                    validate_func=validate_authentication_denied,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.GET],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
            ],
        ),
        TestData(
            test_name="User with DELETE can delete a Skill",
            workspace_to_use=WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=action_create_skill_shell,
                    validate_func=validate_skill_created,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.CREATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
                TestStep(
                    action_func=action_delete_skill,
                    validate_func=validate_no_error,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.DELETE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
            ],
        ),
        TestData(
            test_name="User without DELETE cannot delete a Skill",
            workspace_to_use=WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=action_create_skill_shell,
                    validate_func=validate_skill_created,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.CREATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
                TestStep(
                    action_func=action_delete_skill,
                    validate_func=validate_authentication_denied,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.GET],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
            ],
        ),
        TestData(
            test_name="User cannot retrieve a Skill from another workspace",
            workspace_to_use=WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=action_create_skill_shell,
                    validate_func=validate_skill_created,
                    workspace_to_use=WORKSPACE,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.CREATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
                TestStep(
                    action_func=action_get_skill,
                    validate_func=validate_authentication_denied_or_resource_not_found,
                    workspace_to_use=OTHER_WORKSPACE,
                    user_info=UserInfo(
                        workspace=OTHER_WORKSPACE,
                        verbs=[KubeVerb.GET],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
            ],
        ),
        TestData(
            test_name="Skill search excludes Skills from another workspace",
            workspace_to_use=WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=action_create_skill_shell,
                    validate_func=validate_skill_created,
                    workspace_to_use=WORKSPACE,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.CREATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
                TestStep(
                    action_func=action_search_skills,
                    validate_func=validate_skill_search_excludes_other_workspace,
                    workspace_to_use=OTHER_WORKSPACE,
                    user_info=UserInfo(
                        workspace=OTHER_WORKSPACE,
                        verbs=[KubeVerb.LIST],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
            ],
        ),
        TestData(
            test_name="Named GET permission allows the named Skill",
            workspace_to_use=WORKSPACE,
            test_steps=[
                TestStep(action_func=_seed_skill_name(SCOPED_SKILL_NAME)),
                TestStep(
                    action_func=action_create_skill_shell,
                    validate_func=validate_skill_created,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.CREATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
                TestStep(
                    action_func=action_get_skill,
                    validate_func=validate_skill_retrieved,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.GET],
                        resource_types=SKILL_RESOURCE,
                        resource_names={
                            ResourceType.SKILLS: [SCOPED_SKILL_NAME]
                        },
                    ),
                ),
            ],
        ),
        TestData(
            test_name="Named GET permission denies a different Skill",
            workspace_to_use=WORKSPACE,
            test_steps=[
                TestStep(action_func=_seed_skill_name(OTHER_GET_SKILL_NAME)),
                TestStep(
                    action_func=action_create_skill_shell,
                    validate_func=validate_skill_created,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.CREATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
                TestStep(
                    action_func=action_get_skill,
                    validate_func=validate_authentication_denied,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.GET],
                        resource_types=SKILL_RESOURCE,
                        resource_names={
                            ResourceType.SKILLS: [SCOPED_SKILL_NAME]
                        },
                    ),
                ),
            ],
        ),
        TestData(
            test_name="Named UPDATE permission denies a different Skill",
            workspace_to_use=WORKSPACE,
            test_steps=[
                TestStep(action_func=_seed_skill_name(OTHER_UPDATE_SKILL_NAME)),
                TestStep(
                    action_func=action_create_skill_shell,
                    validate_func=validate_skill_created,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.CREATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
                TestStep(
                    action_func=action_update_skill,
                    validate_func=validate_authentication_denied,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.UPDATE],
                        resource_types=SKILL_RESOURCE,
                        resource_names={
                            ResourceType.SKILLS: [SCOPED_SKILL_NAME]
                        },
                    ),
                ),
            ],
        ),
        TestData(
            test_name="Named DELETE permission denies a different Skill",
            workspace_to_use=WORKSPACE,
            test_steps=[
                TestStep(action_func=_seed_skill_name(OTHER_DELETE_SKILL_NAME)),
                TestStep(
                    action_func=action_create_skill_shell,
                    validate_func=validate_skill_created,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.CREATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
                TestStep(
                    action_func=action_delete_skill,
                    validate_func=validate_authentication_denied,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.DELETE],
                        resource_types=SKILL_RESOURCE,
                        resource_names={
                            ResourceType.SKILLS: [SCOPED_SKILL_NAME]
                        },
                    ),
                ),
            ],
        ),
        TestData(
            test_name="Named UPDATE permission allows the matching Skill",
            workspace_to_use=WORKSPACE,
            test_steps=[
                TestStep(action_func=_seed_skill_name(SCOPED_UPDATE_SKILL_NAME)),
                TestStep(
                    action_func=action_create_skill_shell,
                    validate_func=validate_skill_created,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.CREATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
                TestStep(
                    action_func=action_update_skill,
                    validate_func=validate_no_error,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.UPDATE],
                        resource_types=SKILL_RESOURCE,
                        resource_names={
                            ResourceType.SKILLS: [SCOPED_UPDATE_SKILL_NAME]
                        },
                    ),
                ),
            ],
        ),
        TestData(
            test_name="Named DELETE permission allows the matching Skill",
            workspace_to_use=WORKSPACE,
            test_steps=[
                TestStep(action_func=_seed_skill_name(SCOPED_DELETE_SKILL_NAME)),
                TestStep(
                    action_func=action_create_skill_shell,
                    validate_func=validate_skill_created,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.CREATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
                TestStep(
                    action_func=action_delete_skill,
                    validate_func=validate_no_error,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.DELETE],
                        resource_types=SKILL_RESOURCE,
                        resource_names={
                            ResourceType.SKILLS: [SCOPED_DELETE_SKILL_NAME]
                        },
                    ),
                ),
            ],
        ),
        TestData(
            test_name="Named GET permission does not grant unrestricted Skill search",
            workspace_to_use=WORKSPACE,
            test_steps=[
                TestStep(action_func=_seed_skill_name(SEARCH_SKILL_NAME)),
                TestStep(
                    action_func=action_create_skill_shell,
                    validate_func=validate_skill_created,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.CREATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
                TestStep(
                    action_func=action_search_skills,
                    validate_func=validate_authentication_denied,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.GET],
                        resource_types=SKILL_RESOURCE,
                        resource_names={
                            ResourceType.SKILLS: [SCOPED_SKILL_NAME]
                        },
                    ),
                ),
            ],
        ),
        TestData(
            test_name="User can complete the Skill lifecycle",
            user_info=UserInfo(
                workspace=WORKSPACE,
                verbs=[
                    KubeVerb.CREATE,
                    KubeVerb.GET,
                    KubeVerb.LIST,
                    KubeVerb.UPDATE,
                    KubeVerb.DELETE,
                ],
                resource_types=SKILL_RESOURCE,
            ),
            workspace_to_use=WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=action_create_skill_shell,
                    validate_func=validate_skill_created,
                ),
                TestStep(
                    action_func=action_get_skill,
                    validate_func=validate_skill_retrieved,
                ),
                TestStep(
                    action_func=action_update_skill,
                    validate_func=validate_no_error,
                ),
                TestStep(
                    action_func=action_get_skill,
                    validate_func=validate_skill_updated,
                ),
                TestStep(
                    action_func=action_search_skills,
                    validate_func=validate_skill_search_includes_active_skill,
                ),
                TestStep(
                    action_func=action_delete_skill,
                    validate_func=validate_no_error,
                ),
                TestStep(
                    action_func=action_get_skill,
                    validate_func=validate_skill_deleted,
                ),
            ],
        ),
    ]

    if Config.effective_serve_artifacts():
        test_scenarios.extend(
            [
                TestData(
                    test_name="User with GET can read Skill artifact content",
                    workspace_to_use=WORKSPACE,
                    test_steps=[
                        TestStep(
                            action_func=action_register_local_skill,
                            validate_func=validate_skill_artifact_registered,
                            user_info=UserInfo(
                                workspace=WORKSPACE,
                                verbs=[KubeVerb.UPDATE],
                                resource_types=SKILL_RESOURCE,
                            ),
                        ),
                        TestStep(
                            action_func=action_read_skill_artifact,
                            validate_func=validate_skill_artifact_read,
                            user_info=UserInfo(
                                workspace=WORKSPACE,
                                verbs=[KubeVerb.GET],
                                resource_types=SKILL_RESOURCE,
                            ),
                        ),
                    ],
                ),
                TestData(
                    test_name="User without GET cannot read Skill artifact content",
                    workspace_to_use=WORKSPACE,
                    test_steps=[
                        TestStep(
                            action_func=action_register_local_skill,
                            validate_func=validate_skill_artifact_registered,
                            user_info=UserInfo(
                                workspace=WORKSPACE,
                                verbs=[KubeVerb.UPDATE],
                                resource_types=SKILL_RESOURCE,
                            ),
                        ),
                        TestStep(
                            action_func=action_read_skill_artifact,
                            validate_func=validate_authentication_denied,
                            user_info=UserInfo(
                                workspace=WORKSPACE,
                                verbs=[KubeVerb.LIST],
                                resource_types=SKILL_RESOURCE,
                            ),
                        ),
                    ],
                ),
                TestData(
                    test_name="User with CREATE cannot register a local Skill without UPDATE",
                    workspace_to_use=WORKSPACE,
                    test_steps=TestStep(
                        action_func=action_register_local_skill,
                        validate_func=validate_authentication_denied,
                        user_info=UserInfo(
                            workspace=WORKSPACE,
                            verbs=[KubeVerb.CREATE],
                            resource_types=SKILL_RESOURCE,
                        ),
                    ),
                ),
            ]
        )
    else:
        test_scenarios.append(
            TestData(
                test_name="Local Skill registration is unavailable without artifact serving",
                workspace_to_use=WORKSPACE,
                test_steps=TestStep(
                    action_func=action_register_local_skill,
                    validate_func=validate_skill_artifact_registration_unavailable,
                    user_info=UserInfo(
                        workspace=WORKSPACE,
                        verbs=[KubeVerb.CREATE, KubeVerb.GET, KubeVerb.UPDATE],
                        resource_types=SKILL_RESOURCE,
                    ),
                ),
            )
        )

    @pytest.mark.parametrize(
        "test_data",
        test_scenarios,
        ids=lambda scenario: scenario.test_name,
    )
    def test_skill_registry(self, create_user_with_permissions, test_data):
        logger.info("Starting test: %s", test_data.test_name)

        if test_data.user_info:
            user_info = create_user_with_permissions(
                workspace=test_data.user_info.workspace,
                verbs=test_data.user_info.verbs,
                resource_types=test_data.user_info.resource_types,
                subresources=test_data.user_info.subresources,
                resource_names=test_data.user_info.resource_names,
            )
            self.test_context.active_user = user_info
            self.test_context.user_client = user_info.client

        if test_data.workspace_to_use:
            self.test_context.active_workspace = test_data.workspace_to_use
            mlflow.set_workspace(test_data.workspace_to_use)

        self._execute_test_steps(test_data=test_data)

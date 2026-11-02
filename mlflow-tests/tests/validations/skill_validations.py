"""Skill Registry validation functions."""

from mlflow.exceptions import MlflowException

from ..shared import TestContext
from ..shared.error_models import ErrorCode
from .validation_utils import (
    validate_resource_retrieved_or_created,
)


def validate_skill_created(test_context: TestContext) -> None:
    validate_resource_retrieved_or_created(
        test_context=test_context,
        resource_field="active_skill",
        resource_type="Skill",
        operation="creation",
    )


def validate_skill_retrieved(test_context: TestContext) -> None:
    validate_resource_retrieved_or_created(
        test_context=test_context,
        resource_field="active_skill",
        resource_type="Skill",
        operation="retrieval",
    )


def validate_skill_updated(test_context: TestContext) -> None:
    if test_context.last_error is not None:
        raise AssertionError(
            "Skill retrieval after update failed: "
            f"{test_context.last_error.error.code} - "
            f"{test_context.last_error.error.message}"
        )

    expected_description = "Updated Skill Registry E2E description"
    actual_description = (
        test_context.active_skill.description
        if test_context.active_skill
        else None
    )
    if actual_description != expected_description:
        raise AssertionError(
            "Skill update was not persisted: expected description "
            f"{expected_description!r}, got "
            f"{actual_description!r}"
        )


def validate_skill_deleted(test_context: TestContext) -> None:
    if test_context.last_error is None:
        raise AssertionError(
            f"Skill {test_context.active_skill.name if test_context.active_skill else None!r} "
            "was still retrievable after deletion"
        )

    if test_context.last_error.error.code != ErrorCode.RESOURCE_NOT_FOUND:
        raise AssertionError(
            "Skill deletion verification failed: expected RESOURCE_NOT_FOUND, got "
            f"{test_context.last_error.error.code} - "
            f"{test_context.last_error.error.message}"
        )


def validate_skill_search_excludes_other_workspace(
    test_context: TestContext,
) -> None:
    if test_context.last_error is not None:
        raise AssertionError(
            f"Skill search failed: {test_context.last_error.error.message}"
        )

    returned_names = {
        skill.name for skill in test_context.skill_search_results or []
    }
    active_skill_name = (
        test_context.active_skill.name if test_context.active_skill else None
    )
    if active_skill_name in returned_names:
        raise AssertionError(
            f"Skill {active_skill_name!r} from another workspace "
            "was returned by search"
        )


def validate_skill_search_includes_active_skill(
    test_context: TestContext,
) -> None:
    if test_context.last_error is not None:
        raise AssertionError(
            f"Skill search failed: {test_context.last_error.error.message}"
        )

    returned_names = {
        skill.name for skill in test_context.skill_search_results or []
    }
    active_skill_name = (
        test_context.active_skill.name if test_context.active_skill else None
    )
    if active_skill_name not in returned_names:
        raise AssertionError(
            f"Skill {active_skill_name!r} was not returned by search"
        )


def validate_skill_artifact_registered(test_context: TestContext) -> None:
    if test_context.last_error is not None:
        raise AssertionError(
            f"Skill registration failed: {test_context.last_error.error.message}"
        )

    source = test_context.active_skill.source if test_context.active_skill else None
    artifact_uri = getattr(source, "artifact_path", None) or str(source or "")
    if not artifact_uri.startswith("mlflow-artifacts:/skills/"):
        raise AssertionError(
            f"Expected an MLflow Skill artifact source, got {source!r}"
        )


def validate_skill_artifact_registration_unavailable(
    test_context: TestContext,
) -> None:
    """Validate local registration failure when artifact serving is disabled."""
    if test_context.last_error is None:
        raise AssertionError(
            "Local Skill registration unexpectedly succeeded without artifact serving"
        )

    message = test_context.last_error.error.message
    if "does not serve artifacts" not in message:
        raise AssertionError(
            "Expected the server artifact-serving capability error, got: "
            f"{test_context.last_error.error.code} - {message}"
        )

    try:
        versions = list(
            test_context.admin_client.search_skill_versions(
                name=(
                    test_context.active_skill.name
                    if test_context.active_skill
                    else ""
                ),
                organization=(
                    test_context.active_skill.organization
                    if test_context.active_skill
                    else ""
                )
                or "",
            )
        )
    except MlflowException as error:
        if "RESOURCE_DOES_NOT_EXIST" in str(error) or "does not exist" in str(error).lower():
            return
        raise AssertionError(
            "Failed to verify that disabled local registration created no version: "
            f"{error}"
        ) from error

    if versions:
        raise AssertionError(
            "Local registration created Skill versions even though artifact serving was disabled: "
            f"{versions!r}"
        )


def validate_skill_artifact_read(test_context: TestContext) -> None:
    if test_context.last_error is not None:
        raise AssertionError(
            f"Skill artifact read failed: {test_context.last_error.error.message}"
        )

    if (
        (
            test_context.active_skill.artifact_content
            if test_context.active_skill
            else None
        )
        != test_context.expected_skill_artifact_content
    ):
        raise AssertionError("Downloaded Skill artifact content did not match")



def validate_pulled_skill(expected_body: str):
    """Return a validator that checks the last pull got the version with this SKILL.md text."""

    def validate(test_context: TestContext) -> None:
        content = test_context.active_skill.artifact_content
        if not content or not content.endswith(expected_body):
            raise AssertionError(
                f"Pulled SKILL.md does not end with {expected_body!r}: {content!r}"
            )

    validate.__name__ = "validate_pulled_skill"
    return validate


def validate_skill_versions(expected: dict[str, list[int]]):
    """Return a validator that checks each named Skill has exactly these version numbers."""

    def validate(test_context: TestContext) -> None:
        for name, expected_versions in expected.items():
            versions = sorted(
                version.version
                for version in test_context.user_client.search_skill_versions(name=name)
            )
            if versions != expected_versions:
                raise AssertionError(
                    f"Skill {name!r} has versions {versions}, expected {expected_versions}"
                )

    validate.__name__ = "validate_skill_versions"
    return validate

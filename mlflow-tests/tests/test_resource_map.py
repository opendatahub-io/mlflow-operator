import pytest

from mlflow_tests.enums import ResourceType

from .shared.resource_map import (
    PRIMARY_RESOURCE_REF,
    SECONDARY_RESOURCE_REF,
    resolve_resource_name_refs,
)


@pytest.fixture(scope="module", autouse=True)
def create_experiments_and_runs() -> dict:
    """Override the integration bootstrap fixture for helper-level tests."""
    return {}


def test_resolve_resource_name_refs_mixes_placeholders_and_literals() -> None:
    resource_map = {
        ResourceType.EXPERIMENTS: {
            "workspace1": {
                "primary": {"id": "1", "name": "exp-a"},
                "secondary": {"id": "2", "name": "exp-b"},
            }
        }
    }

    resolved = resolve_resource_name_refs(
        resource_map,
        "workspace1",
        {
            ResourceType.EXPERIMENTS: [
                PRIMARY_RESOURCE_REF,
                "literal-exp",
                SECONDARY_RESOURCE_REF,
            ]
        },
    )

    assert resolved == {
        ResourceType.EXPERIMENTS: ["exp-a", "literal-exp", "exp-b"]
    }

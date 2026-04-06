"""Helpers for baseline test resources that come in primary/secondary pairs.

Each workspace fixture creates two baseline resources per type:
- `primary`: the allowed/default resource for positive read-path tests
- `secondary`: a different resource in the same workspace for negative name-scope tests
"""

from typing import Any

from mlflow_tests.enums import ResourceType

# Placeholders used by test declarations before the fixture resolves real names.
PRIMARY_RESOURCE_REF = "__primary__"
SECONDARY_RESOURCE_REF = "__secondary__"
# Slots used in the resource map for the two baseline resources per workspace.
PRIMARY_RESOURCE_SLOT = "primary"
SECONDARY_RESOURCE_SLOT = "secondary"


def get_resource_entry(
    resource_map: dict[ResourceType, dict[str, Any]],
    resource_type: ResourceType,
    workspace: str,
    slot: str = PRIMARY_RESOURCE_SLOT,
) -> dict[str, Any]:
    """Return the requested baseline resource entry for a workspace and slot."""
    workspace_resources = resource_map.get(resource_type, {})
    resource_slots = workspace_resources.get(workspace)
    if not isinstance(resource_slots, dict):
        raise KeyError(
            f"No {resource_type.value} resources recorded for workspace '{workspace}'"
        )

    resource_entry = resource_slots.get(slot)
    if not isinstance(resource_entry, dict):
        raise KeyError(
            f"No {slot} {resource_type.value} resource recorded for workspace '{workspace}'"
        )
    if "name" not in resource_entry:
        raise KeyError(
            f"Resource entry for {slot} {resource_type.value} in workspace '{workspace}' is missing required 'name'"
        )

    return resource_entry


def resolve_resource_name_refs(
    resource_map: dict[ResourceType, dict[str, Any]],
    workspace: str,
    resource_names: dict[ResourceType, list[str]] | None,
) -> dict[ResourceType, list[str]]:
    """Resolve placeholder resource names against the session baseline resources."""
    resolved_resource_names: dict[ResourceType, list[str]] = {}
    for resource_type, names in (resource_names or {}).items():
        resolved_names = []
        for name in names:
            # These placeholders resolve to the Kubernetes RBAC resourceName. For the
            # resource types covered here, that is the human-readable MLflow resource
            # name (for example the experiment name or registered model name), not the ID.
            if name == PRIMARY_RESOURCE_REF:
                resolved_names.append(
                    get_resource_entry(resource_map, resource_type, workspace)[
                        "name"
                    ]
                )
            elif name == SECONDARY_RESOURCE_REF:
                resolved_names.append(
                    get_resource_entry(
                        resource_map,
                        resource_type,
                        workspace,
                        slot=SECONDARY_RESOURCE_SLOT,
                    )["name"]
                )
            else:
                resolved_names.append(name)
        resolved_resource_names[resource_type] = resolved_names

    return resolved_resource_names

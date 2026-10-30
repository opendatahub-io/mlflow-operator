"""Shared MLflow upgrade scenario state for 3.17-only additions."""

from __future__ import annotations

# Seeded before the upgrade and verified after it. The name is static because the pre-upgrade
# and post-upgrade phases run as separate pytest processes.
SKILL_REGISTRY_STATE = {
    "name": "upgrade-test-skill",
    "description": "Static upgrade-test Skill",
    "skill_tags": {"upgrade-tag-key": "upgrade-tag-value"},
    "version_tags": {"upgrade-version-tag-key": "upgrade-version-tag-value"},
    "alias": "production",
    "versions": [
        {"body": "# Upgrade test Skill\n\nVersion 1 instructions.\n"},
        {"body": "# Upgrade test Skill\n\nVersion 2 instructions.\n"},
    ],
}

# Registered from a nonexistent OCI image. The server stores only the source metadata and never
# fetches it, so this Skill needs neither a reachable registry nor MLflow artifact serving.
SKILL_REGISTRY_OCI_STATE = {
    "name": "upgrade-test-oci-skill",
    "image": "registry.example.invalid/upgrade-tests/skill:v1",
    "subpath": "skills/upgrade",
    "digest": "8185db4679617948f154b6f317ecdbdbe1d36a32cdfc018288a33efbdf192c38",
}

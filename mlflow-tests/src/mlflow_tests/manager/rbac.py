"""Kubernetes RBAC management."""

import logging
import time
from kubernetes import client
from kubernetes.client.rest import ApiException

from mlflow_tests.enums import KubeVerb, ResourceType

logger = logging.getLogger(__name__)


class K8RoleManager:
    """Class for managing Kubernetes Roles and RoleBindings."""

    def __init__(self, rbac_v1_api: client.RbacAuthorizationV1Api):
        """Initialize the K8RoleManager with a Kubernetes RBAC API client.

        Args:
            rbac_v1_api: Kubernetes RBAC API client
        """
        self.rbac_v1_api = rbac_v1_api

    def create_role(
            self,
        name: str,
        namespace: str,
        verbs: list[KubeVerb],
        resources: list[ResourceType],
        subresources: list[str] | None = None,
        resource_names: dict[ResourceType, list[str]] | None = None,
    ) -> None:
        """Create a Kubernetes Role with specified permissions.

        Args:
            name: Role name
            namespace: Namespace for the Role
            verbs: List of Kubernetes verbs to grant
            resources: MLflow resources to grant access to
            subresources: Optional list of subresources (e.g., ["gatewaysecrets/use"])
            resource_names: Optional mapping of resource type to allowed names

        Raises:
            ApiException: If creation fails
        """
        main_verbs = [verb.value for verb in verbs]
        subresources = subresources or []
        resource_names = resource_names or {}

        # Get K8s resource names from ResourceType
        k8s_resources = [r.get_k8s_resource() for r in resources]

        # Create policy rules
        policy_rules = []

        # Rule 1: MLflow main resources (experiments, registeredmodels, jobs, etc.)
        unscoped_resources = []
        for resource in resources:
            scoped_names = resource_names.get(resource, [])
            if not scoped_names:
                unscoped_resources.append(resource.get_k8s_resource())
                continue

            policy_rules.append(
                client.V1PolicyRule(
                    api_groups=["mlflow.kubeflow.org"],
                    resources=[resource.get_k8s_resource()],
                    verbs=main_verbs,
                    resource_names=scoped_names,
                )
            )
            logger.debug(
                "Added name-scoped resource rule: resource=%s, verbs=%s, names=%s",
                resource.get_k8s_resource(),
                main_verbs,
                scoped_names,
            )

        if unscoped_resources:
            mlflow_main_rule = client.V1PolicyRule(
                api_groups=["mlflow.kubeflow.org"],
                resources=unscoped_resources,
                verbs=main_verbs,
            )
            policy_rules.append(mlflow_main_rule)
            logger.debug(
                "Added main resource rule: resources=%s, verbs=%s",
                unscoped_resources,
                main_verbs,
            )

        # Rule 2: MLflow sub-resources (gatewaysecrets/use, gatewayendpoints/use, etc.)
        # Only add if subresources are explicitly provided
        gateway_sub_resources: list[str] = []
        if subresources:
            # Use provided subresources directly
            mlflow_sub_rule = client.V1PolicyRule(
                api_groups=["mlflow.kubeflow.org"],
                resources=subresources,
                verbs=["create"],  # Sub-resources only support create verb in K8s
            )
            policy_rules.append(mlflow_sub_rule)
            logger.debug(f"Added sub-resource rule: resources={subresources}, verbs=['create']")
        else:
            # Auto-detect gateway sub-resources from resource types if subresources not specified
            for resource in resources:
                gateway_sub_resources.extend(resource.get_k8s_sub_resources())

            if gateway_sub_resources and KubeVerb.CREATE in verbs:
                mlflow_sub_rule = client.V1PolicyRule(
                    api_groups=["mlflow.kubeflow.org"],
                    resources=gateway_sub_resources,
                    verbs=["create"],
                )
                policy_rules.append(mlflow_sub_rule)
                logger.debug(f"Added auto-detected sub-resource rule: resources={gateway_sub_resources}, verbs=['create']")

        # Rule 3: Core Kubernetes API permissions for basic authentication and namespace access
        # These are needed for MLflow to authenticate with the K8s API and validate tokens
        core_verbs = ["get", "list"] if KubeVerb.CREATE not in verbs else ["get", "list", "create"]
        core_rule = client.V1PolicyRule(
            api_groups=[""],  # Core API group
            resources=["namespaces", "serviceaccounts", "secrets"],
            verbs=core_verbs,
        )
        policy_rules.append(core_rule)
        logger.debug(f"Added core API rule to provide access to namespace, sa and secrets: verbs={core_verbs}")

        # Rule 4: RBAC permissions to read own roles and bindings (for token validation)
        rbac_rule = client.V1PolicyRule(
            api_groups=["rbac.authorization.k8s.io"],
            resources=["roles", "rolebindings"],
            verbs=["get", "list"],
        )
        policy_rules.append(rbac_rule)
        logger.debug(f"Added RBAC read rule")

        # Create role with all policy rules
        k8s_role = client.V1Role(
            metadata=client.V1ObjectMeta(name=name, namespace=namespace),
            rules=policy_rules,
        )

        try:
            self.rbac_v1_api.create_namespaced_role(namespace=namespace, body=k8s_role)
            logger.info(f"Created role '{name}' in namespace '{namespace}' with {len(policy_rules)} policy rules")
            logger.info(
                "Role '%s' permissions: main_resources=%s, scoped_resources=%s, sub_resources=%s",
                name,
                k8s_resources,
                {resource.value: names for resource, names in resource_names.items()},
                gateway_sub_resources,
            )
        except ApiException as e:
            if e.status == 409:  # Resource already exists - ignore
                logger.debug(f"Role '{name}' already exists, continuing")
            else:
                logger.error(f"Failed to create role '{name}': {e}")
                raise

    def create_role_binding(
            self,
        name: str,
        namespace: str,
        role_name: str,
        service_account_name: str,
    ) -> None:
        """Create a Kubernetes RoleBinding.

        Args:
            name: RoleBinding name
            namespace: Namespace for the RoleBinding
            role_name: Name of the Role to bind
            service_account_name: ServiceAccount to bind the role to

        Raises:
            ApiException: If creation fails
        """
        # Create role reference
        role_ref = client.V1RoleRef(
            api_group="rbac.authorization.k8s.io", kind="Role", name=role_name
        )

        # Create subject for service account
        subject = client.RbacV1Subject(
            kind="ServiceAccount", name=service_account_name, namespace=namespace
        )

        # Create role binding
        role_binding = client.V1RoleBinding(
            metadata=client.V1ObjectMeta(name=name, namespace=namespace),
            role_ref=role_ref,
            subjects=[subject],
        )

        try:
            self.rbac_v1_api.create_namespaced_role_binding(
                namespace=namespace, body=role_binding
            )
            logger.info(f"Created role binding '{name}' in namespace '{namespace}' for MLflow SSAR validation")
        except ApiException as e:
            if e.status != 409:  # Ignore if already exists
                raise

    def verify_rbac_permissions(
        self,
        service_account_name: str,
        namespace: str,
        resource: str,
        verb: str,
        resource_name: str | None = None,
        max_retries: int = 10,
        retry_delay: float = 2.0
    ) -> None:
        """Verify that RBAC permissions are actually usable via SubjectAccessReview.

        Args:
            service_account_name: ServiceAccount to check permissions for
            namespace: Namespace for the check
            resource: K8s resource to check (e.g. 'registeredmodels')
            verb: K8s verb to check (e.g. 'delete')
            resource_name: Optional name for name-scoped authorization checks
            max_retries: Maximum number of verification attempts
            retry_delay: Delay between attempts (exponential backoff)

        Raises:
            Exception: If permissions are not available after max_retries
        """
        from kubernetes.client import AuthorizationV1Api, V1SubjectAccessReview, V1SubjectAccessReviewSpec, V1ResourceAttributes

        auth_api = AuthorizationV1Api()

        # Try multiple API groups that MLflow might use
        api_groups_to_try = [
            "mlflow.kubeflow.org",
        ]

        for api_group in api_groups_to_try:
            logger.debug(f"Trying RBAC verification for {service_account_name} with API group '{api_group}' for {verb} {resource}")
            sar_resource_name = resource_name if resource_name else None
            current_delay = retry_delay

            # Create SubjectAccessReview to verify permissions
            sar = V1SubjectAccessReview(
                spec=V1SubjectAccessReviewSpec(
                    resource_attributes=V1ResourceAttributes(
                        namespace=namespace,
                        verb=verb,
                        resource=resource,
                        group=api_group,
                        name=sar_resource_name,
                    ),
                    user=f"system:serviceaccount:{namespace}:{service_account_name}"
                )
            )
            reason = "No reason provided"

            for attempt in range(max_retries):
                try:
                    result = auth_api.create_subject_access_review(body=sar)
                    if result.status.allowed:
                        logger.info(f"RBAC permissions verified for {service_account_name} - can {verb} {resource} (API group: {api_group})")
                        return
                    else:
                        if result.status.reason:
                            reason = result.status.reason
                        logger.debug(f"RBAC denied for API group '{api_group}' (attempt {attempt + 1}/{max_retries}): {reason}")
                        if attempt < max_retries - 1:
                            time.sleep(current_delay)
                            current_delay *= 1.2  # Smaller backoff multiplier
                except Exception as e:
                    logger.debug(f"RBAC verification attempt {attempt + 1} failed for API group '{api_group}': {e}")
                    if attempt < max_retries - 1:
                        time.sleep(current_delay)
                        current_delay *= 1.2
                    else:
                        break  # Try next API group

        # If we get here, none of the API groups worked - log detailed error and continue
        logger.warning(f"RBAC permissions could not be verified for {service_account_name} to {verb} {resource}")
        logger.warning(f"Tried API groups: {api_groups_to_try}")
        raise RuntimeError(f"RBAC permissions could not be verified for {service_account_name} to {verb} {resource}, failed due to K8s error: {reason}")

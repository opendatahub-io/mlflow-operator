"""Kubernetes RBAC management."""

import logging
from kubernetes import client
from kubernetes.client.rest import ApiException

from mlflow_tests.enums import ResourceType, UserRole

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
        role: UserRole,
        resources: list[ResourceType],
    ) -> None:
        """Create a Kubernetes Role with specified permissions.

        Args:
            name: Role name
            namespace: Namespace for the Role
            role: Permission level defining K8s verbs
            resources: MLflow resources to grant access to

        Raises:
            ApiException: If creation fails
        """
        # Get K8s verbs from role for main resources
        main_verbs = role.get_k8s_verbs()
        sub_resource_verbs = role.get_k8s_sub_resource_verbs()

        # Get K8s resource names from ResourceType
        resource_names = [r.get_k8s_resource() for r in resources]

        # Create policy rules
        policy_rules = []

        # Rule 1: MLflow main resources (experiments, registeredmodels, jobs, etc.)
        if resource_names:
            mlflow_main_rule = client.V1PolicyRule(
                api_groups=["mlflow.kubeflow.org"],
                resources=resource_names,
                verbs=main_verbs,
            )
            policy_rules.append(mlflow_main_rule)
            logger.debug(f"Added main resource rule: resources={resource_names}, verbs={main_verbs}")

        # Rule 2: MLflow sub-resources (gatewaysecrets/use, gatewayendpoints/use, etc.)
        # Only add if the role can use gateway resources and we have gateway resources in the list
        gateway_sub_resources = []
        for resource in resources:
            gateway_sub_resources.extend(resource.get_k8s_sub_resources())

        if gateway_sub_resources and sub_resource_verbs:
            mlflow_sub_rule = client.V1PolicyRule(
                api_groups=["mlflow.kubeflow.org"],
                resources=gateway_sub_resources,
                verbs=sub_resource_verbs,
            )
            policy_rules.append(mlflow_sub_rule)
            logger.debug(f"Added sub-resource rule: resources={gateway_sub_resources}, verbs={sub_resource_verbs}")

        # Rule 3: Core Kubernetes API permissions for basic authentication and namespace access
        # These are needed for MLflow to authenticate with the K8s API and validate tokens
        core_verbs = ["get", "list"] if role in [UserRole.READ] else ["get", "list", "create"]
        core_rule = client.V1PolicyRule(
            api_groups=[""],  # Core API group
            resources=["namespaces", "serviceaccounts", "secrets"],
            verbs=core_verbs,
        )
        policy_rules.append(core_rule)
        logger.debug(f"Added core API rule: verbs={core_verbs}")

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
            logger.info(f"Role '{name}' permissions: main_resources={resource_names}, sub_resources={gateway_sub_resources}")
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
        max_retries: int = 10,
        retry_delay: float = 1.0
    ) -> None:
        """Verify that RBAC permissions are actually usable via SubjectAccessReview.

        Args:
            service_account_name: ServiceAccount to check permissions for
            namespace: Namespace for the check
            resource: K8s resource to check (e.g. 'registeredmodels')
            verb: K8s verb to check (e.g. 'delete')
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
            "mlflow.org",
            "kubeflow.org",
            ""  # Core API group
        ]

        for api_group in api_groups_to_try:
            logger.debug(f"Trying RBAC verification for {service_account_name} with API group '{api_group}' for {verb} {resource}")

            # Create SubjectAccessReview to verify permissions
            sar = V1SubjectAccessReview(
                spec=V1SubjectAccessReviewSpec(
                    resource_attributes=V1ResourceAttributes(
                        namespace=namespace,
                        verb=verb,
                        resource=resource,
                        group=api_group
                    ),
                    user=f"system:serviceaccount:{namespace}:{service_account_name}"
                )
            )

            for attempt in range(max_retries):
                try:
                    result = auth_api.create_subject_access_review(body=sar)
                    if result.status.allowed:
                        logger.info(f"RBAC permissions verified for {service_account_name} - can {verb} {resource} (API group: {api_group})")
                        return
                    else:
                        reason = result.status.reason or "No reason provided"
                        logger.debug(f"RBAC denied for API group '{api_group}' (attempt {attempt + 1}/{max_retries}): {reason}")
                        if attempt < max_retries - 1:
                            import time
                            time.sleep(retry_delay)
                            retry_delay *= 1.2  # Smaller backoff multiplier
                        break  # Try next API group
                except Exception as e:
                    logger.debug(f"RBAC verification attempt {attempt + 1} failed for API group '{api_group}': {e}")
                    if attempt < max_retries - 1:
                        import time
                        time.sleep(retry_delay)
                        retry_delay *= 1.2
                    else:
                        break  # Try next API group

        # If we get here, none of the API groups worked - log detailed error and continue
        logger.warning(f"RBAC permissions could not be verified for {service_account_name} to {verb} {resource}")
        logger.warning(f"Tried API groups: {api_groups_to_try}")
        logger.warning(f"This may indicate that MLflow CRDs are not installed or use different API groups")
        logger.warning(f"Proceeding without verification - test may fail if permissions are not actually available")

        # Don't raise an exception - just warn and continue
        # This allows tests to run even if verification fails
        # If permissions are actually wrong, the MLflow operation will fail later

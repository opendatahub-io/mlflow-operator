/*
Copyright 2025.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package controller

const (
	// ResourceName is the base name used for MLflow resources (deployments, services, etc.)
	ResourceName = "mlflow"
	// ClusterRoleName is the name of the shared ClusterRole used by all MLflow instances
	ClusterRoleName = "mlflow"
	// ClusterRoleBindingName is the name of the shared ClusterRoleBinding used by all MLflow instances
	ClusterRoleBindingName = "mlflow"
	// GCClusterRBACName is the currently effective singleton GC ClusterRole/ClusterRoleBinding name.
	GCClusterRBACName = "mlflow-gc"
	// ServiceAccountName is the name of the service account for MLflow deployments
	ServiceAccountName = "mlflow-sa"
	// GCServiceAccountName is the name of the service account for the GC CronJob
	GCServiceAccountName = "mlflow-gc-sa"
	// TraceArchivalServiceAccountName is the name of the service account for the trace archival CronJob
	TraceArchivalServiceAccountName = "mlflow-trace-archival-sa"
	// TLSSecretName is the default name for the TLS secret used by the MLflow server
	TLSSecretName = "mlflow-tls"
	// StaticPrefix is the URL prefix for MLflow when deployed via the operator
	StaticPrefix = "/mlflow"

	// PlatformTrustedCABundleConfigMapName is the well-known ConfigMap name for platform CA bundle
	PlatformTrustedCABundleConfigMapName = "odh-trusted-ca-bundle"

	// NamespaceWorkspaceLabelKey is the label on Namespaces that opt in to MLflow workspace RBAC
	NamespaceWorkspaceLabelKey = "opendatahub.io/global-mlflow-workspace"
	// ManagedByLabelKey is the standard Kubernetes label for resource ownership
	ManagedByLabelKey = "app.kubernetes.io/managed-by"
	// ManagedByLabelValue identifies this operator as the manager
	ManagedByLabelValue = "mlflow-operator"
	// AuthCRName is the singleton Auth CR name
	AuthCRName = "auth"
	// ViewClusterRoleBaseName is the base name of the mlflow-view aggregate ClusterRole (before kustomize namePrefix)
	ViewClusterRoleBaseName = "mlflow-view"
	// EditClusterRoleBaseName is the base name of the mlflow-edit aggregate ClusterRole (before kustomize namePrefix)
	EditClusterRoleBaseName = "mlflow-edit"
	// RoleBindingViewName is the name of the view RoleBinding in workspace namespaces
	RoleBindingViewName = "odh-group-mlflow-view"
	// RoleBindingEditName is the name of the edit RoleBinding in workspace namespaces
	RoleBindingEditName = "odh-group-mlflow-edit"
)

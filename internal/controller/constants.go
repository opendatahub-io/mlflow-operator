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
	// ModeRHOAI represents Red Hat OpenShift AI deployment mode
	ModeRHOAI = "rhoai"
	// ModeOpenDataHub represents OpenDataHub deployment mode
	ModeOpenDataHub = "opendatahub"

	// NamespaceRHOAI is the namespace for RHOAI deployments
	NamespaceRHOAI = "redhat-ods-applications"
	// NamespaceOpenDataHub is the namespace for OpenDataHub deployments
	NamespaceOpenDataHub = "opendatahub"

	// ServiceAccountName is the name of the service account for MLflow deployments
	ServiceAccountName = "mlflow-sa"
	// ClusterRoleName is the name of the ClusterRole for namespace listing
	ClusterRoleName = ServiceAccountName + "-list-namespaces"
	// ClusterRoleBindingName is the name of the ClusterRoleBinding for namespace listing
	ClusterRoleBindingName = ServiceAccountName + "-list-namespaces"
)

// GetNamespaceForMode returns the appropriate namespace based on the deployment mode
func GetNamespaceForMode(mode string) string {
	switch mode {
	case ModeRHOAI:
		return NamespaceRHOAI
	case ModeOpenDataHub:
		return NamespaceOpenDataHub
	default:
		return NamespaceOpenDataHub
	}
}

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
	// ServiceAccountName is the name of the service account for MLflow deployments
	ServiceAccountName = "mlflow-sa"
	// TLSSecretName is the default name for the TLS secret used by the MLflow server
	TLSSecretName = "mlflow-tls"
	// StaticPrefix is the URL prefix for MLflow when deployed via the operator
	StaticPrefix = "/mlflow"

	// PlatformTrustedCABundleConfigMapName is the well-known ConfigMap name for platform CA bundle
	// This is a contract with ODH Platform: https://github.com/opendatahub-io/architecture-decision-records/pull/28
	PlatformTrustedCABundleConfigMapName = "odh-trusted-ca-bundle"

	// PlatformTrustedCABundleVolumeName is the volume name for the platform CA bundle
	PlatformTrustedCABundleVolumeName = "platform-ca-bundle"

	// PlatformTrustedCABundleKey is the key in the platform CA bundle ConfigMap that contains the main CA bundle
	PlatformTrustedCABundleKey = "ca-bundle.crt"

	// PlatformTrustedCABundleExtraKey is the key for additional platform-specific CA certificates
	PlatformTrustedCABundleExtraKey = "odh-ca-bundle.crt"

	// PlatformTrustedCABundleMountPath is where the platform CA bundle is mounted
	PlatformTrustedCABundleMountPath = "/etc/pki/tls/certs/platform"

	// PlatformTrustedCABundleFilePath is the full file path to the main platform CA bundle
	PlatformTrustedCABundleFilePath = PlatformTrustedCABundleMountPath + "/" + PlatformTrustedCABundleKey

	// PlatformTrustedCABundleExtraFilePath is the full file path to additional platform CA certificates
	PlatformTrustedCABundleExtraFilePath = PlatformTrustedCABundleMountPath + "/" + PlatformTrustedCABundleExtraKey

	// CustomCABundleMountPath is the path where user-provided CA bundles are mounted
	CustomCABundleMountPath = "/etc/pki/tls/certs/custom-ca-bundle.crt"

	// CombinedCABundleMountPath is the directory where the init container writes the combined CA bundle
	CombinedCABundleMountPath = "/etc/pki/tls/certs/combined"

	// CombinedCABundleFilePath is the full path to the combined CA bundle file
	CombinedCABundleFilePath = CombinedCABundleMountPath + "/ca-bundle.crt"

	// SystemCABundlePath is the default system CA bundle path on RHEL-based images
	SystemCABundlePath = "/etc/pki/tls/certs/ca-bundle.crt"
)

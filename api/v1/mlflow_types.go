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

package v1

import (
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// MLflowSpec defines the desired state of MLflow
type MLflowSpec struct {
	// KubeRbacProxy specifies the kube-rbac-proxy sidecar configuration
	// +optional
	KubeRbacProxy *KubeRbacProxyConfig `json:"kubeRbacProxy,omitempty"`

	// OpenShift specifies OpenShift-specific configuration
	// +optional
	OpenShift *OpenShiftConfig `json:"openShift,omitempty"`

	// Image specifies the MLflow container image
	// +optional
	Image *ImageConfig `json:"image,omitempty"`

	// Replicas is the number of MLflow pods to run
	// +kubebuilder:default=1
	// +kubebuilder:validation:Minimum=1
	// +optional
	Replicas *int32 `json:"replicas,omitempty"`

	// Resources specifies the compute resources for the MLflow container
	// +optional
	Resources *corev1.ResourceRequirements `json:"resources,omitempty"`

	// Storage specifies the persistent storage configuration.
	// Only required if using file-based or SQLite backend/registry stores or file-based artifacts.
	// Not needed when using remote storage (S3, PostgreSQL, etc.).
	// When omitted, no PVC will be created - ensure backendStoreUri, registryStoreUri,
	// and artifactsDestination point to remote storage.
	// +optional
	Storage *StorageConfig `json:"storage,omitempty"`

	// BackendStoreURI is the URI for the MLflow backend store (metadata).
	// Supported schemes: file://, sqlite://, mysql://, postgresql://, etc.
	// Examples:
	//   - "sqlite:////mlflow/mlflow.db" (requires Storage to be configured)
	//   - "postgresql://user:pass@host:5432/db" (no Storage needed)
	//   - "mysql://user:pass@host:3306/db" (no Storage needed)
	// +kubebuilder:default="sqlite:////mlflow/mlflow.db"
	// +optional
	BackendStoreURI *string `json:"backendStoreUri,omitempty"`

	// RegistryStoreURI is the URI for the MLflow registry store (model registry metadata).
	// Supported schemes: file://, sqlite://, mysql://, postgresql://, etc.
	// Examples:
	//   - "sqlite:////mlflow/mlflow.db" (requires Storage to be configured)
	//   - "postgresql://user:pass@host:5432/db" (no Storage needed)
	// If omitted, defaults to the same value as backendStoreUri.
	// +optional
	RegistryStoreURI *string `json:"registryStoreUri,omitempty"`

	// ArtifactsDestination is the destination for MLflow artifacts (models, plots, files).
	// Supported schemes: file://, s3://, gs://, wasbs://, hdfs://, etc.
	// Examples:
	//   - "file:///mlflow/artifacts" (requires Storage to be configured)
	//   - "s3://my-bucket/mlflow/artifacts" (no Storage needed)
	//   - "gs://my-bucket/mlflow/artifacts" (no Storage needed)
	// +kubebuilder:default="file:///mlflow/artifacts"
	// +optional
	ArtifactsDestination *string `json:"artifactsDestination,omitempty"`

	// ServeArtifacts determines whether MLflow should serve artifacts.
	// When enabled, adds the --serve-artifacts flag to the MLflow server.
	// This allows clients to log and retrieve artifacts through the MLflow server's REST API
	// instead of directly accessing the artifact storage.
	// +kubebuilder:default=true
	// +optional
	ServeArtifacts *bool `json:"serveArtifacts,omitempty"`

	// Workers is the number of gunicorn worker processes for the MLflow server.
	// Note: This is different from pod replicas. Each pod will run this many worker processes.
	// Defaults to 1. For high-traffic deployments, consider increasing pod replicas instead.
	// +kubebuilder:default=1
	// +kubebuilder:validation:Minimum=1
	// +optional
	Workers *int32 `json:"workers,omitempty"`

	// Env is a list of environment variables to set in the MLflow container
	// +optional
	Env []corev1.EnvVar `json:"env,omitempty"`

	// EnvFrom is a list of sources to populate environment variables in the MLflow container
	// +optional
	EnvFrom []corev1.EnvFromSource `json:"envFrom,omitempty"`

	// PodSecurityContext specifies the security context for the MLflow pod
	// +optional
	PodSecurityContext *corev1.PodSecurityContext `json:"podSecurityContext,omitempty"`

	// SecurityContext specifies the security context for the MLflow container
	// +optional
	SecurityContext *corev1.SecurityContext `json:"securityContext,omitempty"`

	// NodeSelector is a selector which must be true for the pod to fit on a node
	// +optional
	NodeSelector map[string]string `json:"nodeSelector,omitempty"`

	// Tolerations are the pod's tolerations
	// +optional
	Tolerations []corev1.Toleration `json:"tolerations,omitempty"`

	// Affinity specifies the pod's scheduling constraints
	// +optional
	Affinity *corev1.Affinity `json:"affinity,omitempty"`
}

// KubeRbacProxyConfig contains kube-rbac-proxy sidecar configuration
type KubeRbacProxyConfig struct {
	// Enabled determines whether kube-rbac-proxy sidecar should be deployed
	// +optional
	Enabled *bool `json:"enabled,omitempty"`

	// Image specifies the kube-rbac-proxy container image configuration
	// +optional
	Image *ImageConfig `json:"image,omitempty"`

	// Resources specifies the compute resources for the kube-rbac-proxy container
	// +optional
	Resources *corev1.ResourceRequirements `json:"resources,omitempty"`

	// TLS specifies TLS certificate configuration for kube-rbac-proxy
	// +optional
	TLS *TLSConfig `json:"tls,omitempty"`
}

// TLSConfig contains TLS certificate configuration
type TLSConfig struct {
	// SecretName is the name of the secret containing tls.crt and tls.key
	// Defaults to "mlflow-tls"
	// +optional
	SecretName *string `json:"secretName,omitempty"`

	// UpstreamCAFile is the path to the CA certificate file for validating the upstream MLflow server
	// Defaults to "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt" when using OpenShift serving-cert
	// For non-OpenShift deployments, set this to the path where UpstreamCASecret will be mounted (e.g., "/etc/tls/upstream-ca/ca.crt")
	// +optional
	UpstreamCAFile *string `json:"upstreamCAFile,omitempty"`

	// UpstreamCASecret is the name of a secret containing the CA certificate for validating the upstream MLflow server
	// Required for non-OpenShift deployments when using kube-rbac-proxy
	// The secret should contain a key "ca.crt" with the CA certificate
	// This secret will be mounted at /etc/tls/upstream-ca/
	// +optional
	UpstreamCASecret *string `json:"upstreamCASecret,omitempty"`
}

// OpenShiftConfig contains OpenShift-specific configuration
type OpenShiftConfig struct {
	// ServingCert configures OpenShift service-ca-operator integration for automatic TLS certificate provisioning
	// +optional
	ServingCert *ServingCertConfig `json:"servingCert,omitempty"`
}

// ServingCertConfig contains OpenShift service-ca configuration
type ServingCertConfig struct {
	// Enabled determines whether to use OpenShift service-ca-operator for certificate provisioning
	// When enabled, adds service.beta.openshift.io/serving-cert-secret-name annotation to the service
	// +optional
	Enabled *bool `json:"enabled,omitempty"`

	// SecretName is the name of the secret where service-ca-operator will store the certificate
	// Defaults to "mlflow-tls"
	// +optional
	SecretName *string `json:"secretName,omitempty"`
}

// ImageConfig contains container image configuration
type ImageConfig struct {
	// Image is the container image (includes tag)
	// +optional
	Image *string `json:"image,omitempty"`

	// PullPolicy is the image pull policy
	// +kubebuilder:validation:Enum=Always;IfNotPresent;Never
	// +optional
	PullPolicy *corev1.PullPolicy `json:"pullPolicy,omitempty"`
}

// StorageConfig contains persistent storage configuration
type StorageConfig struct {
	// Size is the size of the persistent volume claim
	// +kubebuilder:default="10Gi"
	// +optional
	Size *resource.Quantity `json:"size,omitempty"`

	// StorageClassName is the storage class for the PVC
	// If empty, the default storage class will be used
	// +optional
	StorageClassName *string `json:"storageClassName,omitempty"`

	// AccessMode is the access mode for the PVC
	// +kubebuilder:default="ReadWriteOnce"
	// +kubebuilder:validation:Enum=ReadWriteOnce;ReadWriteMany;ReadOnlyMany
	// +optional
	AccessMode *corev1.PersistentVolumeAccessMode `json:"accessMode,omitempty"`
}

// MLflowStatus defines the observed state of MLflow.
type MLflowStatus struct {

	// conditions represent the current state of the MLflow resource.
	// Each condition has a unique type and reflects the status of a specific aspect of the resource.
	//
	// Standard condition types include:
	// - "Available": the resource is fully functional
	// - "Progressing": the resource is being created or updated
	// - "Degraded": the resource failed to reach or maintain its desired state
	//
	// The status of each condition is one of True, False, or Unknown.
	// +listType=map
	// +listMapKey=type
	// +optional
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:scope=Cluster
// +kubebuilder:validation:XValidation:rule="self.metadata.name == 'mlflow'",message="MLflow resource name must be 'mlflow'"

// MLflow is the Schema for the mlflows API
type MLflow struct {
	metav1.TypeMeta `json:",inline"`

	// metadata is a standard object metadata
	// +optional
	metav1.ObjectMeta `json:"metadata,omitzero"`

	// spec defines the desired state of MLflow
	// +required
	Spec MLflowSpec `json:"spec"`

	// status defines the observed state of MLflow
	// +optional
	Status MLflowStatus `json:"status,omitzero"`
}

// +kubebuilder:object:root=true

// MLflowList contains a list of MLflow
type MLflowList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitzero"`
	Items           []MLflow `json:"items"`
}

func init() {
	SchemeBuilder.Register(&MLflow{}, &MLflowList{})
}

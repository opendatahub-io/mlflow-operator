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
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// MLflowSpec defines the desired state of MLflow
// +kubebuilder:validation:XValidation:rule="has(self.defaultArtifactRoot) || (has(self.serveArtifacts) && self.serveArtifacts)",message="defaultArtifactRoot must be set when serveArtifacts is not true"
// +kubebuilder:validation:XValidation:rule="!has(self.defaultArtifactRoot) || !self.defaultArtifactRoot.startsWith('file://') || (has(self.serveArtifacts) && self.serveArtifacts)",message="serveArtifacts must be enabled when defaultArtifactRoot uses file-based storage (file:// prefix)"
// +kubebuilder:validation:XValidation:rule="(has(self.backendStoreUri) && size(self.backendStoreUri) > 0) || has(self.backendStoreUriFrom)",message="backendStoreUri or backendStoreUriFrom must be set"
// +kubebuilder:validation:XValidation:rule="!(has(self.backendStoreUri) && has(self.backendStoreUriFrom))",message="backendStoreUri and backendStoreUriFrom are mutually exclusive"
// +kubebuilder:validation:XValidation:rule="!(has(self.registryStoreUri) && has(self.registryStoreUriFrom))",message="registryStoreUri and registryStoreUriFrom are mutually exclusive"
// +kubebuilder:validation:XValidation:rule="!has(self.backendStoreUri) || (!self.backendStoreUri.startsWith('sqlite://') && !self.backendStoreUri.startsWith('file://')) || has(self.storage)",message="storage must be configured when using file-based backend store (sqlite:// or file:// prefix)"
// +kubebuilder:validation:XValidation:rule="!has(self.registryStoreUri) || (!self.registryStoreUri.startsWith('sqlite://') && !self.registryStoreUri.startsWith('file://')) || has(self.storage)",message="storage must be configured when using file-based registry store (sqlite:// or file:// prefix)"
// +kubebuilder:validation:XValidation:rule="!has(self.artifactsDestination) || !self.artifactsDestination.startsWith('file://') || has(self.storage)",message="storage must be configured when artifactsDestination uses file-based storage (file:// prefix)"
// +kubebuilder:validation:XValidation:rule="!has(self.artifactsDestination) || !self.artifactsDestination.startsWith('file://') || (has(self.serveArtifacts) && self.serveArtifacts)",message="serveArtifacts must be enabled when artifactsDestination uses file-based storage (file:// prefix)"
type MLflowSpec struct {
	// Image specifies the MLflow container image.
	// If not specified, use the default image
	// via the MLFLOW_IMAGE environment variable in the operator.
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

	// ServiceAccountName is the name of the ServiceAccount to use for the MLflow pod.
	// If not specified, a default ServiceAccount will be "mlflow-sa"
	// +kubebuilder:default="mlflow-sa"
	// +optional
	ServiceAccountName *string `json:"serviceAccountName,omitempty"`

	// Storage specifies the persistent storage configuration using standard PVC spec.
	// Only required if using SQLite backend/registry stores or file-based artifacts.
	// Not needed when using remote storage (S3, PostgreSQL, etc.).
	// When omitted, no PVC will be created - ensure backendStoreUri, registryStoreUri,
	// and artifactsDestination point to remote storage.
	// Example:
	//   storage:
	//     accessModes: ["ReadWriteOnce"]
	//     resources:
	//       requests:
	//         storage: 10Gi
	//     storageClassName: fast-ssd
	// +optional
	Storage *corev1.PersistentVolumeClaimSpec `json:"storage,omitempty"`

	// BackendStoreURI is the URI for the MLflow backend store (metadata).
	// Supported schemes: file://, sqlite://, mysql://, postgresql://, etc.
	// Examples:
	//   - "sqlite:////mlflow/mlflow.db" (requires Storage to be configured)
	// Note: For URIs containing credentials, prefer using BackendStoreURIFrom for security.
	// This must be set explicitly unless BackendStoreURIFrom is provided.
	// +optional
	BackendStoreURI *string `json:"backendStoreUri,omitempty"`

	// BackendStoreURIFrom is a reference to a secret containing the backend store URI.
	// Use this instead of BackendStoreURI when the URI contains credentials.
	// Takes precedence over BackendStoreURI if both are specified.
	// +optional
	BackendStoreURIFrom *corev1.SecretKeySelector `json:"backendStoreUriFrom,omitempty"`

	// RegistryStoreURI is the URI for the MLflow registry store (model registry metadata).
	// Supported schemes: file://, sqlite://, mysql://, postgresql://, etc.
	// Examples:
	//   - "sqlite:////mlflow/mlflow.db" (requires Storage to be configured)
	// If omitted, defaults to the same value as backendStoreUri.
	// Note: For URIs containing credentials, prefer using RegistryStoreURIFrom for security.
	// +optional
	RegistryStoreURI *string `json:"registryStoreUri,omitempty"`

	// RegistryStoreURIFrom is a reference to a secret containing the registry store URI.
	// Use this instead of RegistryStoreURI when the URI contains credentials.
	// Takes precedence over RegistryStoreURI if both are specified.
	// +optional
	RegistryStoreURIFrom *corev1.SecretKeySelector `json:"registryStoreUriFrom,omitempty"`

	// ArtifactsDestination is the server-side destination for MLflow artifacts (models, plots, files).
	// This setting only applies when ServeArtifacts is enabled. When ServeArtifacts is disabled,
	// this field is ignored and clients access artifact storage directly.
	// Supported schemes: file://, s3://, gs://, wasbs://, hdfs://, etc.
	// Examples:
	//   - "file:///mlflow/artifacts" (requires Storage to be configured)
	//   - "s3://my-bucket/mlflow/artifacts" (no Storage needed)
	//   - "gs://my-bucket/mlflow/artifacts" (no Storage needed)
	// If not specified when ServeArtifacts is enabled, defaults to "file:///mlflow/artifacts"
	//
	// For cloud storage authentication, use EnvFrom to inject credentials from secrets or configmaps.
	// Example for S3:
	//   envFrom:
	//   - secretRef:
	//       name: aws-credentials  # Contains AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
	// Example for GCS:
	//   envFrom:
	//   - secretRef:
	//       name: gcp-credentials  # Contains GOOGLE_APPLICATION_CREDENTIALS path
	// +optional
	ArtifactsDestination *string `json:"artifactsDestination,omitempty"`

	// DefaultArtifactRoot is the default artifact root path for MLflow runs on the server.
	// This is required when serveArtifacts is false.
	// Supported schemes: file://, s3://, gs://, wasbs://, hdfs://, etc.
	// Examples:
	//   - "s3://my-bucket/mlflow/artifacts"
	//   - "gs://my-bucket/mlflow/artifacts"
	//   - "file:///mlflow/artifacts"
	// +optional
	DefaultArtifactRoot *string `json:"defaultArtifactRoot,omitempty"`

	// ServeArtifacts determines whether MLflow should serve artifacts.
	// When enabled, adds the --serve-artifacts flag to the MLflow server and uses ArtifactsDestination
	// to configure where artifacts are stored. This allows clients to log and retrieve artifacts
	// through the MLflow server's REST API instead of directly accessing the artifact storage.
	// When disabled, ArtifactsDestination is ignored and clients must have direct access to artifact storage.
	// +kubebuilder:default=false
	// +optional
	ServeArtifacts *bool `json:"serveArtifacts,omitempty"`

	// Workers is the number of uvicorn worker processes for the MLflow server.
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

	// PodLabels are labels to add only to the MLflow pod, not to other resources.
	// Use this for pod-specific labels like version, component-specific metadata, etc.
	// For labels that should be applied to all resources (Service, Deployment, etc.), use commonLabels in values.yaml.
	// +optional
	PodLabels map[string]string `json:"podLabels,omitempty"`

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

	// CABundleConfigMap specifies a ConfigMap containing a CA certificate bundle.
	// The bundle will be mounted into the MLflow container and configured for use
	// with TLS connections (e.g. PostgreSQL SSL, S3 with custom certificates).
	// +optional
	CABundleConfigMap *CABundleConfigMapSpec `json:"caBundleConfigMap,omitempty"`
}

// CABundleConfigMapSpec specifies a ConfigMap containing CA certificates.
// All .crt and .pem files in the ConfigMap will be included in the combined CA bundle.
type CABundleConfigMapSpec struct {
	// Name is the name of the ConfigMap containing CA certificates
	// +kubebuilder:validation:Required
	// +kubebuilder:validation:MinLength=1
	Name string `json:"name"`
}

// ImageConfig contains container image configuration
type ImageConfig struct {
	// Image is the container image (includes tag)
	// +optional
	Image *string `json:"image,omitempty"`

	// ImagePullPolicy is the image pull policy.
	// If not specified, uses Kubernetes defaults (IfNotPresent for most images, Always for :latest tag).
	// +kubebuilder:validation:Enum=Always;IfNotPresent;Never
	// +optional
	ImagePullPolicy *corev1.PullPolicy `json:"imagePullPolicy,omitempty"`
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

// MLflowConfigSpec defines the desired configuration for MLflow workspaces within a namespace.
type MLflowConfigSpec struct {
	// ArtifactRootPath is an optional relative path from the bucket root specified in
	// the ArtifactRootSecret. When provided, this path is appended to the bucket URI
	// from the secret to form the resolved artifact root.
	//
	// Example:
	//   artifactRootSecret: "ds-team-s3-connection-secret"  # Contains bucket: ds-team-bucket
	//   artifactRootPath: "experiments"
	//   resolved artifact root: s3://ds-team-bucket/experiments
	//
	// +optional
	ArtifactRootPath *string `json:"artifactRootPath,omitempty"`

	// ArtifactRootSecret is the name of a Secret in this namespace that contains
	// credentials and bucket information for accessing the artifact storage.
	//
	// The Secret must have the required keys for s3 compatible storage:
	// Example Secret:
	//   apiVersion: v1
	//   kind: Secret
	//   metadata:
	//     name: ds-team-s3-connection-secret
	//     namespace: ds-team-namespace
	//   data:
	//     AWS_ACCESS_KEY_ID: <base64-encoded>
	//     AWS_SECRET_ACCESS_KEY: <base64-encoded>
	//     AWS_S3_BUCKET: <base64-encoded>
	//     AWS_S3_ENDPOINT: <base64-encoded>
	//     AWS_DEFAULT_REGION: <base64-encoded>  # Optional (default region is not always required, e.g. minio)
	//
	// +kubebuilder:validation:MinLength=1
	ArtifactRootSecret string `json:"artifactRootSecret"`
}

// +kubebuilder:object:root=true
// +kubebuilder:resource:scope=Namespaced
// +kubebuilder:validation:XValidation:rule="self.metadata.name == 'mlflow'",message="MLflowConfig resource name must be 'mlflow'"

// MLflowConfig is a namespace-scoped configuration resource that allows
// Kubernetes namespace owners to override the default artifact storage
// for their namespace.
type MLflowConfig struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	// spec defines the desired MLflow configuration for this namespace.
	// +required
	Spec MLflowConfigSpec `json:"spec"`
}

// +kubebuilder:object:root=true

// MLflowConfigList contains a list of MLflowConfig resources.
type MLflowConfigList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []MLflowConfig `json:"items"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:scope=Cluster
// +kubebuilder:validation:XValidation:rule="self.metadata.name == 'mlflow'",message="MLflow resource name must be 'mlflow'"
// +kubebuilder:validation:XValidation:rule="self.metadata.name.size() <= 40",message="MLflow resource name must be at most 40 characters to ensure generated resource names stay within Kubernetes 63-character limit"

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

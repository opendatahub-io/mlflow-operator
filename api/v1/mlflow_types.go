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
	networkingv1 "k8s.io/api/networking/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// MLflowSpec defines the desired state of MLflow
// +kubebuilder:validation:XValidation:rule="has(self.defaultArtifactRoot) || (has(self.serveArtifacts) && self.serveArtifacts)",message="defaultArtifactRoot must be set when serveArtifacts is not true"
// +kubebuilder:validation:XValidation:rule="!has(self.defaultArtifactRoot) || !self.defaultArtifactRoot.startsWith('file://') || (has(self.serveArtifacts) && self.serveArtifacts)",message="serveArtifacts must be enabled when defaultArtifactRoot uses file-based storage (file:// prefix)"
// +kubebuilder:validation:XValidation:rule="(has(self.backendStoreUri) && size(self.backendStoreUri) > 0) || (has(self.backendStoreUriFrom) && size(self.backendStoreUriFrom.name) > 0 && size(self.backendStoreUriFrom.key) > 0)",message="backendStoreUri or backendStoreUriFrom must be set"
// +kubebuilder:validation:XValidation:rule="!(has(self.backendStoreUri) && has(self.backendStoreUriFrom))",message="backendStoreUri and backendStoreUriFrom are mutually exclusive"
// +kubebuilder:validation:XValidation:rule="!(has(self.registryStoreUri) && has(self.registryStoreUriFrom))",message="registryStoreUri and registryStoreUriFrom are mutually exclusive"
// +kubebuilder:validation:XValidation:rule="!has(self.registryStoreUriFrom) || (size(self.registryStoreUriFrom.name) > 0 && size(self.registryStoreUriFrom.key) > 0)",message="registryStoreUriFrom.name and registryStoreUriFrom.key must be non-empty when registryStoreUriFrom is set"
// +kubebuilder:validation:XValidation:rule="!has(self.backendStoreUri) || self.backendStoreUri.startsWith('sqlite://') || self.backendStoreUri.startsWith('sqlite+') || self.backendStoreUri.startsWith('postgresql://') || self.backendStoreUri.startsWith('postgresql+') || self.backendStoreUri.startsWith('mysql://') || self.backendStoreUri.startsWith('mysql+')",message="backendStoreUri must use a supported SQL metadata store URI scheme"
// +kubebuilder:validation:XValidation:rule="!has(self.registryStoreUri) || self.registryStoreUri.startsWith('sqlite://') || self.registryStoreUri.startsWith('sqlite+') || self.registryStoreUri.startsWith('postgresql://') || self.registryStoreUri.startsWith('postgresql+') || self.registryStoreUri.startsWith('mysql://') || self.registryStoreUri.startsWith('mysql+')",message="registryStoreUri must use a supported SQL metadata store URI scheme"
// +kubebuilder:validation:XValidation:rule="!has(self.backendStoreUri) || (!self.backendStoreUri.startsWith('sqlite://') && !self.backendStoreUri.startsWith('file://')) || has(self.storage)",message="storage must be configured when using file-based backend store (sqlite:// or file:// prefix)"
// +kubebuilder:validation:XValidation:rule="!has(self.registryStoreUri) || (!self.registryStoreUri.startsWith('sqlite://') && !self.registryStoreUri.startsWith('file://')) || has(self.storage)",message="storage must be configured when using file-based registry store (sqlite:// or file:// prefix)"
// +kubebuilder:validation:XValidation:rule="!has(self.artifactsDestination) || !self.artifactsDestination.startsWith('file://') || has(self.storage)",message="storage must be configured when artifactsDestination uses file-based storage (file:// prefix)"
// +kubebuilder:validation:XValidation:rule="!has(self.artifactsDestination) || !self.artifactsDestination.startsWith('file://') || (has(self.serveArtifacts) && self.serveArtifacts)",message="serveArtifacts must be enabled when artifactsDestination uses file-based storage (file:// prefix)"
// +kubebuilder:validation:XValidation:rule="!has(self.env) || self.env.all(e, e.name != 'MLFLOW_SERVER_DISABLE_SECURITY_MIDDLEWARE')",message="setting the MLFLOW_SERVER_DISABLE_SECURITY_MIDDLEWARE environment variable is not allowed"
// +kubebuilder:validation:XValidation:rule="!has(self.env) || self.env.all(e, e.name != 'MLFLOW_SERVER_ENABLE_JOB_EXECUTION')",message="setting the MLFLOW_SERVER_ENABLE_JOB_EXECUTION environment variable is not allowed; the operator manages job execution lifecycle"
// +kubebuilder:validation:XValidation:rule="!has(self.networkPolicyEgressRules) || self.networkPolicyEgressRules.all(r, (has(r.ports) && size(r.ports) > 0) || (has(r.to) && size(r.to) > 0))",message="each networkPolicyEgressRules entry must specify at least one port or one destination"
// +kubebuilder:validation:XValidation:rule="!has(self.networkPolicyAdditionalEgressRules) || self.networkPolicyAdditionalEgressRules.all(r, (has(r.ports) && size(r.ports) > 0) || (has(r.to) && size(r.to) > 0))",message="each networkPolicyAdditionalEgressRules entry must specify at least one port or one destination"
// +kubebuilder:validation:XValidation:rule="!has(self.resourceClaims) || self.resourceClaims.all(c, ((has(c.resourceClaimName) && size(c.resourceClaimName) > 0) != (has(c.resourceClaimTemplateName) && size(c.resourceClaimTemplateName) > 0)))",message="each resourceClaims entry must set exactly one non-empty value: resourceClaimName or resourceClaimTemplateName"
// +kubebuilder:validation:XValidation:rule="!has(self.traceArchival) || !has(self.traceArchival.location) || !self.traceArchival.location.startsWith('file://') || has(self.storage)",message="storage must be configured when traceArchival.location uses file-based storage (file:// prefix)"
// +kubebuilder:validation:XValidation:rule="!has(self.traceArchival) || !has(self.traceArchival.enabled) || self.traceArchival.enabled == false || (has(self.traceArchival.schedule) && size(self.traceArchival.schedule) > 0)",message="traceArchival.schedule is required when traceArchival.enabled is true"
// +kubebuilder:validation:XValidation:rule="!has(self.traceArchival) || !has(self.traceArchival.enabled) || self.traceArchival.enabled == false || (has(self.traceArchival.location) && size(self.traceArchival.location) > 0)",message="traceArchival.location is required when traceArchival.enabled is true"
// +kubebuilder:validation:XValidation:rule="!has(self.traceArchival) || !has(self.traceArchival.enabled) || self.traceArchival.enabled == false || (has(self.traceArchival.retention) && size(self.traceArchival.retention) > 0)",message="traceArchival.retention is required when traceArchival.enabled is true"
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

	// Migration controls operator-managed database migration orchestration.
	// Add the presence-based mlflow.opendatahub.io/force-migrate annotation to
	// trigger a one-shot rerun; the annotation value is ignored. If a finished
	// Job already exists for the current desired generation, the operator deletes
	// it before creating the replacement Job for that forced rerun.
	// +kubebuilder:default:={mode:Automatic}
	// +optional
	Migration *MLflowMigrationConfig `json:"migration,omitempty"`

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
	// Inline backendStoreUri values intentionally support only sqlite:// and
	// postgresql://.
	// Examples:
	//   - "sqlite:////mlflow/mlflow.db" (requires Storage to be configured)
	// Operator-managed migration only supports SQL backend store URIs.
	// Note: For URIs containing credentials, prefer using BackendStoreURIFrom for security.
	// This must be set explicitly unless BackendStoreURIFrom is provided.
	// +optional
	BackendStoreURI *string `json:"backendStoreUri,omitempty"`

	// BackendStoreURIFrom is a reference to a secret containing the backend store URI.
	// Use this instead of BackendStoreURI when the URI contains credentials.
	// Mutually exclusive with BackendStoreURI - the API rejects specs that set both.
	// +optional
	BackendStoreURIFrom *corev1.SecretKeySelector `json:"backendStoreUriFrom,omitempty"`

	// RegistryStoreURI is the URI for the MLflow registry store (model registry metadata).
	// Inline registryStoreUri values intentionally support only sqlite:// and
	// postgresql://.
	// Examples:
	//   - "sqlite:////mlflow/mlflow.db" (requires Storage to be configured)
	// If omitted, defaults to the same value as backendStoreUri.
	// Operator-managed migration only supports SQL registry store URIs.
	// Note: For URIs containing credentials, prefer using RegistryStoreURIFrom for security.
	// +optional
	RegistryStoreURI *string `json:"registryStoreUri,omitempty"`

	// RegistryStoreURIFrom is a reference to a secret containing the registry store URI.
	// Use this instead of RegistryStoreURI when the URI contains credentials.
	// Mutually exclusive with RegistryStoreURI - the API rejects specs that set both.
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

	// ExtraAllowedOrigins is a list of additional origins to allow for CORS requests.
	// The operator preconfigures safe defaults including Kubernetes service names,
	// the data science gateway domain, and localhost.
	// Use this field to add additional origins beyond the defaults.
	// Each entry should be a full origin (scheme://host[:port]), e.g. "https://my-app.example.com".
	// +kubebuilder:validation:MaxItems=64
	// +optional
	ExtraAllowedOrigins []string `json:"extraAllowedOrigins,omitempty"`

	// WorkspaceLabelSelector is a label selector used to determine which namespaces are exposed
	// as MLflow workspaces when using the Kubernetes workspace provider.
	// +optional
	WorkspaceLabelSelector *metav1.LabelSelector `json:"workspaceLabelSelector,omitempty"`

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
	// +kubebuilder:validation:XValidation:rule="self.all(key, size(self[key]) <= 63)",message="label values must be 63 characters or less"
	PodLabels map[string]string `json:"podLabels,omitempty"`

	// PodAnnotations are annotations to add only to the MLflow pod, not to other resources.
	// Use this for pod-specific annotations like Prometheus scraping or sidecar configuration.
	// +optional
	PodAnnotations map[string]string `json:"podAnnotations,omitempty"`

	// PodSecurityContext specifies the security context for the MLflow pod
	// +optional
	PodSecurityContext *corev1.PodSecurityContext `json:"podSecurityContext,omitempty"`

	// SecurityContext specifies the security context for the MLflow container
	// +optional
	SecurityContext *corev1.SecurityContext `json:"securityContext,omitempty"`

	// NodeSelector is a selector which must be true for the pod to fit on a node
	// +optional
	// +kubebuilder:validation:XValidation:rule="self.all(key, size(self[key]) <= 63)",message="label values must be 63 characters or less"
	NodeSelector map[string]string `json:"nodeSelector,omitempty"`

	// Tolerations are the pod's tolerations
	// +optional
	Tolerations []corev1.Toleration `json:"tolerations,omitempty"`

	// Affinity specifies the pod's scheduling constraints
	// +optional
	Affinity *corev1.Affinity `json:"affinity,omitempty"`

	// ResourceClaims defines which ResourceClaims must be allocated
	// and reserved before the Pod is allowed to start. The resources
	// will be made available to those containers which consume them
	// by name.
	//
	// This is an alpha field and requires enabling the
	// DynamicResourceAllocation feature gate.
	//
	// +patchMergeKey=name
	// +patchStrategy=merge,retainKeys
	// +listType=map
	// +listMapKey=name
	// +featureGate=DynamicResourceAllocation
	// +optional
	ResourceClaims []corev1.PodResourceClaim `json:"resourceClaims,omitempty" patchStrategy:"merge,retainKeys" patchMergeKey:"name"`

	// CABundleConfigMap specifies a ConfigMap containing a CA certificate bundle.
	// The bundle will be mounted into the MLflow container and configured for use
	// with TLS connections (e.g. PostgreSQL SSL, S3 with custom certificates).
	// +optional
	CABundleConfigMap *CABundleConfigMapSpec `json:"caBundleConfigMap,omitempty"`

	// NetworkPolicyEgressRules, when non-empty, replaces the entire default
	// egress block of the MLflow NetworkPolicy. The caller is responsible
	// for including DNS, HTTPS, database, and storage rules as needed.
	// When empty or omitted, the operator renders sensible defaults (DNS,
	// HTTPS, PostgreSQL, MySQL, and S3-compatible storage ports).
	// Rules from NetworkPolicyAdditionalEgressRules are still appended
	// after this list.
	// +optional
	// +kubebuilder:validation:MaxItems=32
	NetworkPolicyEgressRules []networkingv1.NetworkPolicyEgressRule `json:"networkPolicyEgressRules,omitempty"`

	// NetworkPolicyAdditionalEgressRules specifies additional egress rules
	// appended after the base egress rules (either the defaults or the
	// custom rules from NetworkPolicyEgressRules). Use this field when
	// connecting to services on non-standard ports.
	// +optional
	// +kubebuilder:validation:MaxItems=32
	NetworkPolicyAdditionalEgressRules []networkingv1.NetworkPolicyEgressRule `json:"networkPolicyAdditionalEgressRules,omitempty"`

	// GarbageCollection configures a CronJob that permanently deletes soft-deleted
	// MLflow resources (runs, experiments, and logged models) along with their artifacts.
	// Resources must be soft-deleted first (e.g. via the UI or API) before garbage
	// collection will remove them. Use OlderThan to restrict deletion to resources
	// that have been in the deleted state for a minimum duration.
	// +optional
	GarbageCollection *GarbageCollectionSpec `json:"garbageCollection,omitempty"`

	// TraceArchival configures trace archival, which moves older trace span
	// payloads from the SQL tracking store to a configured artifact location.
	// When enabled, the operator creates a CronJob that runs the standalone
	// archival module and mounts the archival config into the MLflow server
	// so the UI can surface archival status. The server's built-in scheduler
	// stays disabled; the CronJob handles execution externally.
	// +optional
	TraceArchival *TraceArchivalSpec `json:"traceArchival,omitempty"`
}

// CABundleConfigMapSpec specifies a ConfigMap containing CA certificates.
// All .crt and .pem files in the ConfigMap will be included in the combined CA bundle.
type CABundleConfigMapSpec struct {
	// Name is the name of the ConfigMap containing CA certificates
	// +kubebuilder:validation:Required
	// +kubebuilder:validation:MinLength=1
	Name string `json:"name"`
}

// GarbageCollectionSpec configures periodic garbage collection via `mlflow gc`.
// The CronJob permanently removes soft-deleted runs, experiments, and logged models
// along with their associated artifacts from the configured backend and artifact stores.
type GarbageCollectionSpec struct {
	// Schedule is the cron expression for when garbage collection runs
	// (e.g., "0 2 * * 0" for weekly at 2 AM on Sunday).
	// +kubebuilder:validation:Required
	// +kubebuilder:validation:MinLength=1
	Schedule string `json:"schedule"`

	// OlderThan restricts garbage collection to resources that have been in the
	// deleted state for at least this duration (e.g., "30d", "7d12h").
	// If not specified, all soft-deleted resources are permanently removed regardless of age.
	// +kubebuilder:validation:MinLength=1
	// +kubebuilder:validation:Pattern=`^(\d+(\.\d+)?d)?(\d+(\.\d+)?h)?(\d+(\.\d+)?m)?(\d+(\.\d+)?s)?$`
	// +optional
	OlderThan *string `json:"olderThan,omitempty"`

	// Resources for the garbage collection Job container.
	// +optional
	Resources *corev1.ResourceRequirements `json:"resources,omitempty"`
}

// TraceArchivalSpec configures trace archival via a CronJob that runs the
// standalone archival module. The archival config is also mounted into the
// MLflow server so the UI can surface archival status.
type TraceArchivalSpec struct {
	// Enabled toggles trace archival. When true, the operator creates a
	// CronJob that runs the standalone archival module and mounts the
	// archival config into the MLflow server for UI awareness.
	// +kubebuilder:default=false
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// Schedule is the cron expression for when archival runs
	// (e.g., "*/5 * * * *" for every 5 minutes).
	// Required when enabled is true.
	// +kubebuilder:validation:MinLength=1
	// +kubebuilder:validation:MaxLength=256
	// +optional
	Schedule *string `json:"schedule,omitempty"`

	// Location is the artifact repository URI where archived span payloads
	// are stored. Supports s3://, gs://, wasbs://, file:// (file:// requires
	// Storage). Required when enabled is true.
	// +kubebuilder:validation:MaxLength=2048
	// +optional
	Location *string `json:"location,omitempty"`

	// Retention is the age threshold after which span payloads are archived
	// out of the tracking store. Uses the duration grammar <int><unit> where
	// unit is m, h, or d. Examples: "30d", "12h", "360m".
	// Required when enabled is true.
	// +kubebuilder:validation:Pattern=`^\d+[mhd]$`
	// +kubebuilder:validation:MaxLength=16
	// +optional
	Retention *string `json:"retention,omitempty"`

	// MaxTracesPerPass limits the number of traces archived in a single pass.
	// +kubebuilder:default=1000
	// +kubebuilder:validation:Minimum=1
	// +optional
	MaxTracesPerPass *int32 `json:"maxTracesPerPass,omitempty"`

	// LongRetentionAllowlist contains experiment IDs whose longer-than-broader
	// retention values may be honored. Experiments not in this list use the
	// broader server or workspace retention even if they set a longer value.
	// +kubebuilder:validation:MaxItems=64
	// +optional
	LongRetentionAllowlist []string `json:"longRetentionAllowlist,omitempty"`

	// Resources for the trace archival CronJob container.
	// +optional
	Resources *corev1.ResourceRequirements `json:"resources,omitempty"`
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

// MLflowMigrationConfig controls operator-managed database migration behavior.
type MLflowMigrationConfig struct {
	// Mode controls how the operator runs database migration orchestration.
	// Automatic runs the operator-managed migration flow when bootstrap or
	// version detection indicates it is needed. Always forces the
	// operator-managed migration flow for each new desired generation, meaning
	// each new revision of the MLflow resource after the desired state changes,
	// before the MLflow Deployment is scaled back up.
	// +kubebuilder:default=Automatic
	// +kubebuilder:validation:Enum=Automatic;Always
	// +optional
	Mode MLflowMigrateMode `json:"mode,omitempty"`

	// TTLSecondsAfterFinished controls how long Kubernetes retains finished
	// operator-managed migration Jobs before TTL cleanup may delete them. When
	// omitted, the operator defaults this to 86400 seconds (24 hours) so admins
	// have time to inspect migration logs after upgrades, including in shared
	// namespaces such as redhat-ods-applications. Values below 3600 seconds are
	// rejected so the controller has time to observe terminal Job state before
	// TTL cleanup can remove the Job.
	// +kubebuilder:validation:Minimum=3600
	// +optional
	TTLSecondsAfterFinished *int32 `json:"ttlSecondsAfterFinished,omitempty"`
}

// MLflowMigrateMode controls operator-managed database migration behavior.
type MLflowMigrateMode string

const (
	// MLflowMigrateAutomatic runs the migration flow when the operator determines it is needed.
	MLflowMigrateAutomatic MLflowMigrateMode = "Automatic"
	// MLflowMigrateAlways forces the migration flow before replicas are restored.
	MLflowMigrateAlways MLflowMigrateMode = "Always"
)

// MLflowAddressStatus holds an addressable endpoint for the managed MLflow deployment.
type MLflowAddressStatus struct {
	// url is the in-cluster HTTPS URL for the managed MLflow Service.
	// +optional
	// +kubebuilder:validation:MaxLength=2048
	URL string `json:"url,omitempty"`
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
	// - "Migration": the operator-managed migration state for the current observed generation
	//
	// The status of each condition is one of True, False, or Unknown.
	// +listType=map
	// +listMapKey=type
	// +optional
	Conditions []metav1.Condition `json:"conditions,omitempty"`

	// url is the externally reachable MLflow URL exposed through the data science gateway.
	// +optional
	// +kubebuilder:validation:MaxLength=2048
	URL string `json:"url,omitempty"`

	// address holds the internal addressable endpoint for the managed MLflow Service.
	// +optional
	Address *MLflowAddressStatus `json:"address,omitempty"`

	// version records the installed MLflow version.
	// +optional
	// +kubebuilder:validation:MaxLength=64
	Version string `json:"version,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:scope=Cluster
// +kubebuilder:printcolumn:name="Available",type="string",JSONPath=".status.conditions[?(@.type=='Available')].status"
// +kubebuilder:printcolumn:name="Progressing",type="string",JSONPath=".status.conditions[?(@.type=='Progressing')].status"
// +kubebuilder:printcolumn:name="Version",type="string",JSONPath=".status.version"
// +kubebuilder:printcolumn:name="URL",type="string",priority=1,JSONPath=".status.url"
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

// SetMigrationProgress records an in-progress migration state for the current
// MLflow generation across the standard rollout conditions and the dedicated
// Migration condition.
func (m *MLflow) SetMigrationProgress(reason, message string) {
	meta.SetStatusCondition(&m.Status.Conditions, metav1.Condition{
		Type:    "Available",
		Status:  metav1.ConditionFalse,
		Reason:  reason,
		Message: message,
	})
	meta.SetStatusCondition(&m.Status.Conditions, metav1.Condition{
		Type:    "Progressing",
		Status:  metav1.ConditionTrue,
		Reason:  reason,
		Message: message,
	})
	meta.SetStatusCondition(&m.Status.Conditions, metav1.Condition{
		Type:               "Migration",
		Status:             metav1.ConditionUnknown,
		ObservedGeneration: m.Generation,
		Reason:             reason,
		Message:            message,
	})
}

// SetMigrationFailure records a terminal migration failure for the current
// MLflow generation.
func (m *MLflow) SetMigrationFailure(reason, message string) {
	meta.SetStatusCondition(&m.Status.Conditions, metav1.Condition{
		Type:    "Available",
		Status:  metav1.ConditionFalse,
		Reason:  reason,
		Message: message,
	})
	meta.SetStatusCondition(&m.Status.Conditions, metav1.Condition{
		Type:    "Progressing",
		Status:  metav1.ConditionFalse,
		Reason:  reason,
		Message: message,
	})
	meta.SetStatusCondition(&m.Status.Conditions, metav1.Condition{
		Type:               "Migration",
		Status:             metav1.ConditionFalse,
		ObservedGeneration: m.Generation,
		Reason:             reason,
		Message:            message,
	})
}

// SetMigrationError records a non-terminal controller-side migration error for
// the current MLflow generation. The Migration condition remains Unknown so the
// controller can continue normal retry behavior.
func (m *MLflow) SetMigrationError(reason, message string) {
	meta.SetStatusCondition(&m.Status.Conditions, metav1.Condition{
		Type:    "Available",
		Status:  metav1.ConditionFalse,
		Reason:  reason,
		Message: message,
	})
	meta.SetStatusCondition(&m.Status.Conditions, metav1.Condition{
		Type:    "Progressing",
		Status:  metav1.ConditionFalse,
		Reason:  reason,
		Message: message,
	})
	meta.SetStatusCondition(&m.Status.Conditions, metav1.Condition{
		Type:               "Migration",
		Status:             metav1.ConditionUnknown,
		ObservedGeneration: m.Generation,
		Reason:             reason,
		Message:            message,
	})
}

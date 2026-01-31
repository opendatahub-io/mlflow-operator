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

import (
	"bytes"
	"fmt"
	"io"
	"path/filepath"

	"helm.sh/helm/v3/pkg/chart"
	"helm.sh/helm/v3/pkg/chart/loader"
	"helm.sh/helm/v3/pkg/chartutil"
	"helm.sh/helm/v3/pkg/engine"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/util/yaml"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	"github.com/opendatahub-io/mlflow-operator/internal/config"
)

const (
	defaultMLflowImage     = "quay.io/opendatahub/mlflow:odh-stable"
	defaultStorageSize     = "2Gi"
	defaultBackendStoreURI = "sqlite:////mlflow/mlflow.db"
	defaultArtifactsDest   = "file:///mlflow/artifacts"
)

// getResourceSuffix returns the resource suffix for naming MLflow resources.
// Returns empty string for CR named "mlflow", otherwise returns "-{crname}".
// All resources are named as "mlflow{{ suffix }}".
func getResourceSuffix(mlflowName string) string {
	if mlflowName == ResourceName {
		return ""
	}
	return "-" + mlflowName
}

// HelmRenderer handles rendering of Helm charts
type HelmRenderer struct {
	chartPath string
}

// RenderOptions contains additional context needed for rendering
type RenderOptions struct {
	// PlatformTrustedCABundleExists indicates if the platform CA bundle ConfigMap exists in the target namespace
	PlatformTrustedCABundleExists bool
}

// NewHelmRenderer creates a new HelmRenderer
func NewHelmRenderer(chartPath string) *HelmRenderer {
	return &HelmRenderer{
		chartPath: chartPath,
	}
}

// RenderChart renders the Helm chart with the given values
func (h *HelmRenderer) RenderChart(mlflow *mlflowv1.MLflow, namespace string, opts RenderOptions) ([]*unstructured.Unstructured, error) {
	// Load the Helm chart
	loadedChart, err := loader.Load(h.chartPath)
	if err != nil {
		return nil, fmt.Errorf("failed to load chart: %w", err)
	}

	values, err := h.mlflowToHelmValues(mlflow, namespace, opts)
	if err != nil {
		return nil, fmt.Errorf("failed to convert MLflow spec to Helm values: %w", err)
	}

	// Render the chart
	rendered, err := h.renderTemplates(loadedChart, values, namespace)
	if err != nil {
		return nil, fmt.Errorf("failed to render templates: %w", err)
	}

	return rendered, nil
}

// mlflowToHelmValues converts MLflow CR spec to Helm values
func (h *HelmRenderer) mlflowToHelmValues(mlflow *mlflowv1.MLflow, namespace string, opts RenderOptions) (map[string]interface{}, error) {
	values := make(map[string]interface{})

	values["namespace"] = namespace

	// Resource suffix for unique naming - empty string for singleton "mlflow" CR, "-<name>" for others
	// All resources will be named like "mlflow{{ .Values.resourceSuffix }}"
	values["resourceSuffix"] = getResourceSuffix(mlflow.Name)

	values["commonLabels"] = map[string]interface{}{
		"component": "mlflow",
	}

	if len(mlflow.Spec.PodLabels) > 0 {
		podLabels := make(map[string]interface{})
		for k, v := range mlflow.Spec.PodLabels {
			podLabels[k] = v
		}
		values["podLabels"] = podLabels
	}

	cfg := config.GetConfig()
	tlsSecretName := TLSSecretName

	tlsValues := map[string]interface{}{
		"secretName": tlsSecretName,
	}

	values["tls"] = tlsValues

	// User-provided CA bundle configuration
	if mlflow.Spec.CABundleConfigMap != nil {
		values["caBundleConfigMap"] = map[string]interface{}{
			"enabled": true,
			"name":    mlflow.Spec.CABundleConfigMap.Name,
			"key":     mlflow.Spec.CABundleConfigMap.Key,
		}
	}

	// Enable ODH trusted CA bundle if ConfigMap exists in the target namespace
	// This is mounted alongside any user-provided bundle for maximum compatibility
	values["platformCABundle"] = map[string]interface{}{
		"enabled":       opts.PlatformTrustedCABundleExists,
		"configMapName": PlatformTrustedCABundleConfigMapName,
		"volumeName":    PlatformTrustedCABundleVolumeName,
		"mountPath":     PlatformTrustedCABundleMountPath,
		"filePath":      PlatformTrustedCABundleFilePath,
		"extraFilePath": PlatformTrustedCABundleExtraFilePath,
	}

	// Determine if we need the CA bundle init container
	// The init container combines system CAs with any custom/platform CA bundles
	caBundlesEnabled := mlflow.Spec.CABundleConfigMap != nil || opts.PlatformTrustedCABundleExists

	// CA bundle configuration - the final bundle that combines system + platform + custom CAs
	// When enabled, an init container creates a single PEM file containing all CA certificates
	values["caBundle"] = map[string]interface{}{
		"enabled":          caBundlesEnabled,
		"mountPath":        CombinedCABundleMountPath,
		"filePath":         CombinedCABundleFilePath,
		"systemBundlePath": SystemCABundlePath,
	}

	// Use config from environment variables as default, can be overridden by CR spec
	mlflowImage := cfg.MLflowImage
	if mlflowImage == "" {
		mlflowImage = defaultMLflowImage
	}
	var imagePullPolicy *string

	if mlflow.Spec.Image != nil {
		if mlflow.Spec.Image.Image != nil {
			mlflowImage = *mlflow.Spec.Image.Image
		}
		if mlflow.Spec.Image.ImagePullPolicy != nil {
			policy := string(*mlflow.Spec.Image.ImagePullPolicy)
			imagePullPolicy = &policy
		}
	}

	imageValues := map[string]interface{}{
		"name": mlflowImage,
	}
	if imagePullPolicy != nil {
		imageValues["imagePullPolicy"] = *imagePullPolicy
	}
	values["image"] = imageValues

	replicas := int32(1)
	if mlflow.Spec.Replicas != nil {
		replicas = *mlflow.Spec.Replicas
	}
	values["replicaCount"] = replicas

	if mlflow.Spec.Resources != nil {
		resourcesMap, err := runtime.DefaultUnstructuredConverter.ToUnstructured(mlflow.Spec.Resources)
		if err != nil {
			return nil, fmt.Errorf("failed to convert resources: %w", err)
		}
		values["resources"] = resourcesMap
	}

	// Storage - only enabled if explicitly configured
	// This allows users to use remote storage (S3, PostgreSQL, etc.) without PVC
	storageEnabled := false
	storageSize := defaultStorageSize
	storageClassName := ""
	accessMode := string(corev1.ReadWriteOnce)

	if mlflow.Spec.Storage != nil {
		// If Storage is specified, enable it
		storageEnabled = true

		// Extract size from Resources.Requests[storage]
		if mlflow.Spec.Storage.Resources.Requests != nil {
			if storageQuantity, ok := mlflow.Spec.Storage.Resources.Requests[corev1.ResourceStorage]; ok {
				storageSize = storageQuantity.String()
			}
		}

		// Extract storage class name
		if mlflow.Spec.Storage.StorageClassName != nil {
			storageClassName = *mlflow.Spec.Storage.StorageClassName
		}

		// Extract the first access mode from an array (we only use one for simplicity)
		if len(mlflow.Spec.Storage.AccessModes) > 0 {
			accessMode = string(mlflow.Spec.Storage.AccessModes[0])
		}
	}

	values["storage"] = map[string]interface{}{
		"enabled":          storageEnabled,
		"size":             storageSize,
		"storageClassName": storageClassName,
		"accessMode":       accessMode,
	}

	backendStoreURI := defaultBackendStoreURI
	artifactsDest := defaultArtifactsDest

	// BackendStoreURI: prefer secret ref over direct value
	var backendStoreURIFrom map[string]interface{}
	if mlflow.Spec.BackendStoreURIFrom != nil {
		backendStoreURIFrom = map[string]interface{}{
			"secretKeyRef": map[string]interface{}{
				"name": mlflow.Spec.BackendStoreURIFrom.Name,
				"key":  mlflow.Spec.BackendStoreURIFrom.Key,
			},
		}
		if mlflow.Spec.BackendStoreURIFrom.Optional != nil {
			backendStoreURIFrom["secretKeyRef"].(map[string]interface{})["optional"] = *mlflow.Spec.BackendStoreURIFrom.Optional
		}
	} else if mlflow.Spec.BackendStoreURI != nil {
		backendStoreURI = *mlflow.Spec.BackendStoreURI
	}

	// RegistryStoreURI: defaults to backendStoreUri when omitted (per API contract)
	// Prefer secret ref over direct value
	var registryStoreURIFrom map[string]interface{}
	registryStoreURI := backendStoreURI // Default to backend URI
	if mlflow.Spec.RegistryStoreURIFrom != nil {
		registryStoreURIFrom = map[string]interface{}{
			"secretKeyRef": map[string]interface{}{
				"name": mlflow.Spec.RegistryStoreURIFrom.Name,
				"key":  mlflow.Spec.RegistryStoreURIFrom.Key,
			},
		}
		if mlflow.Spec.RegistryStoreURIFrom.Optional != nil {
			registryStoreURIFrom["secretKeyRef"].(map[string]interface{})["optional"] = *mlflow.Spec.RegistryStoreURIFrom.Optional
		}
	} else if mlflow.Spec.RegistryStoreURI != nil {
		registryStoreURI = *mlflow.Spec.RegistryStoreURI
	} else if backendStoreURIFrom != nil {
		// Registry isn't set, but backend uses secret ref - use the same secret for registry
		registryStoreURIFrom = backendStoreURIFrom
	}
	// Otherwise registryStoreURI already defaults to backendStoreURI

	if mlflow.Spec.ArtifactsDestination != nil {
		artifactsDest = *mlflow.Spec.ArtifactsDestination
	}

	// DefaultArtifactRoot: only set if user explicitly specifies it. This is required when
	// serveArtifacts is false.
	// When unset, MLflow uses intelligent defaults when serveArtifacts is true:
	var defaultArtifactRoot string
	if mlflow.Spec.DefaultArtifactRoot != nil {
		defaultArtifactRoot = *mlflow.Spec.DefaultArtifactRoot
	}

	// Wildcard to allow all hosts
	allowedHosts := []string{"*"}

	// Defaults to false, but MUST be true when using file-based artifact storage
	serveArtifacts := false
	if mlflow.Spec.ServeArtifacts != nil {
		serveArtifacts = *mlflow.Spec.ServeArtifacts
	}

	workers := int32(1)
	if mlflow.Spec.Workers != nil {
		workers = *mlflow.Spec.Workers
	}

	mlflowConfig := map[string]interface{}{
		"backendStoreUri":      backendStoreURI,
		"registryStoreUri":     registryStoreURI,
		"artifactsDestination": artifactsDest,
		"defaultArtifactRoot":  defaultArtifactRoot,
		"enableWorkspaces":     true,
		"workspaceStoreUri":    "kubernetes://",
		"serveArtifacts":       serveArtifacts,
		"workers":              workers,
		"port":                 8443,
		"allowedHosts":         allowedHosts,
		"staticPrefix":         StaticPrefix, // Hardcoded for operator deployments
	}

	// Add secret references if provided
	if backendStoreURIFrom != nil {
		mlflowConfig["backendStoreUriFrom"] = backendStoreURIFrom
	}
	if registryStoreURIFrom != nil {
		mlflowConfig["registryStoreUriFrom"] = registryStoreURIFrom
	}

	values["mlflow"] = mlflowConfig

	env := make([]interface{}, 0, len(mlflow.Spec.Env))

	// Add custom env vars from spec
	for i, e := range mlflow.Spec.Env {
		envMap, err := runtime.DefaultUnstructuredConverter.ToUnstructured(&e)
		if err != nil {
			return nil, fmt.Errorf("failed to convert env[%d]: %w", i, err)
		}
		env = append(env, envMap)
	}

	values["env"] = env

	if len(mlflow.Spec.EnvFrom) > 0 {
		envFrom := make([]interface{}, 0, len(mlflow.Spec.EnvFrom))
		for i, ef := range mlflow.Spec.EnvFrom {
			envFromMap, err := runtime.DefaultUnstructuredConverter.ToUnstructured(&ef)
			if err != nil {
				return nil, fmt.Errorf("failed to convert envFrom[%d]: %w", i, err)
			}
			envFrom = append(envFrom, envFromMap)
		}
		values["envFrom"] = envFrom
	}

	serviceAccountName := ServiceAccountName
	if mlflow.Spec.ServiceAccountName != nil {
		serviceAccountName = *mlflow.Spec.ServiceAccountName
	}
	values["serviceAccount"] = map[string]interface{}{
		"create": true,
		"name":   serviceAccountName,
	}

	// Add OpenShift service-ca annotation for automatic cert provisioning
	serviceAnnotations := map[string]interface{}{
		"service.beta.openshift.io/serving-cert-secret-name": tlsSecretName,
	}

	values["service"] = map[string]interface{}{
		"type":        "ClusterIP",
		"port":        8443,
		"annotations": serviceAnnotations,
	}

	if mlflow.Spec.PodSecurityContext != nil {
		// Convert PodSecurityContext to map
		// For now, we'll pass through the whole object as-is
		// Helm templates will handle the YAML marshaling
		values["podSecurityContext"] = mlflow.Spec.PodSecurityContext
	} else {
		values["podSecurityContext"] = map[string]interface{}{
			"runAsNonRoot": true,
			"seccompProfile": map[string]interface{}{
				"type": "RuntimeDefault",
			},
		}
	}

	if mlflow.Spec.SecurityContext != nil {
		values["securityContext"] = mlflow.Spec.SecurityContext
	} else {
		values["securityContext"] = map[string]interface{}{
			"allowPrivilegeEscalation": false,
			"readOnlyRootFilesystem":   false,
		}
	}

	if len(mlflow.Spec.NodeSelector) > 0 {
		values["nodeSelector"] = mlflow.Spec.NodeSelector
	} else {
		values["nodeSelector"] = map[string]string{}
	}

	if len(mlflow.Spec.Tolerations) > 0 {
		values["tolerations"] = mlflow.Spec.Tolerations
	} else {
		values["tolerations"] = []corev1.Toleration{}
	}

	if mlflow.Spec.Affinity != nil {
		values["affinity"] = mlflow.Spec.Affinity
	} else {
		values["affinity"] = map[string]interface{}{}
	}

	return values, nil
}

// renderTemplates renders the Helm templates with the given values
func (h *HelmRenderer) renderTemplates(c *chart.Chart, values map[string]interface{}, namespace string) ([]*unstructured.Unstructured, error) {
	// Create release options
	releaseOptions := chartutil.ReleaseOptions{
		Name:      "mlflow",
		Namespace: namespace,
		IsInstall: true,
	}

	// Generate values with built-in objects
	valuesToRender, err := chartutil.ToRenderValues(c, values, releaseOptions, nil)
	if err != nil {
		return nil, fmt.Errorf("failed to prepare values: %w", err)
	}

	// Render templates
	renderedTemplates, err := engine.Render(c, valuesToRender)
	if err != nil {
		return nil, fmt.Errorf("failed to render templates: %w", err)
	}

	// Parse rendered YAML into unstructured objects
	var objects []*unstructured.Unstructured
	for name, content := range renderedTemplates {
		// Skip empty files and notes
		if len(content) == 0 || filepath.Base(name) == "NOTES.txt" {
			continue
		}

		// Parse YAML documents (may contain multiple documents separated by ---)
		decoder := yaml.NewYAMLOrJSONDecoder(bytes.NewBufferString(content), 4096)
		for {
			obj := &unstructured.Unstructured{}
			err := decoder.Decode(obj)
			if err != nil {
				// io.EOF is expected - it means we've reached the end of the YAML stream
				if err == io.EOF {
					break
				}
				// Any other error is a real problem (e.g., malformed YAML)
				return nil, fmt.Errorf("failed to decode template %s: %w", name, err)
			}

			// Skip empty objects
			if len(obj.Object) == 0 {
				continue
			}

			objects = append(objects, obj)
		}
	}

	return objects, nil
}

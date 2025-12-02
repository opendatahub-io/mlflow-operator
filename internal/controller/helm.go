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
	"strings"

	"helm.sh/helm/v3/pkg/chart"
	"helm.sh/helm/v3/pkg/chart/loader"
	"helm.sh/helm/v3/pkg/chartutil"
	"helm.sh/helm/v3/pkg/engine"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/util/yaml"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	"github.com/opendatahub-io/mlflow-operator/internal/config"
)

const (
	defaultMLflowImage        = "quay.io/opendatahub/mlflow:main"
	defaultKubeRbacProxyImage = "quay.io/opendatahub/odh-kube-auth-proxy:latest"
	defaultStorageSize        = "10Gi"
	defaultBackendStoreURI    = "sqlite:////mlflow/mlflow.db"
	defaultRegistryStoreURI   = "sqlite:////mlflow/mlflow.db"
	defaultArtifactsDest      = "file:///mlflow/artifacts"
)

// HelmRenderer handles rendering of Helm charts
type HelmRenderer struct {
	chartPath string
}

// NewHelmRenderer creates a new HelmRenderer
func NewHelmRenderer(chartPath string) *HelmRenderer {
	return &HelmRenderer{
		chartPath: chartPath,
	}
}

// RenderChart renders the Helm chart with the given values
func (h *HelmRenderer) RenderChart(mlflow *mlflowv1.MLflow, namespace string) ([]*unstructured.Unstructured, error) {
	// Load the Helm chart
	loadedChart, err := loader.Load(h.chartPath)
	if err != nil {
		return nil, fmt.Errorf("failed to load chart: %w", err)
	}

	// Convert MLflow spec to Helm values
	values := h.mlflowToHelmValues(mlflow, namespace)

	// Render the chart
	rendered, err := h.renderTemplates(loadedChart, values, namespace)
	if err != nil {
		return nil, fmt.Errorf("failed to render templates: %w", err)
	}

	return rendered, nil
}

// mlflowToHelmValues converts MLflow CR spec to Helm values
// nolint:gocyclo // This function is complex by nature as it maps many CR fields to Helm values
func (h *HelmRenderer) mlflowToHelmValues(mlflow *mlflowv1.MLflow, namespace string) map[string]interface{} {
	values := make(map[string]interface{})

	// Namespace
	values["namespace"] = namespace

	// Common labels
	values["commonLabels"] = map[string]interface{}{
		"mlflow-cr": mlflow.Name,
		"component": "mlflow",
	}

	// Kube RBAC Proxy configuration
	cfg := config.GetConfig()
	kubeRbacProxyEnabled := false
	kubeRbacProxyImage := cfg.KubeAuthProxyImage
	if kubeRbacProxyImage == "" {
		kubeRbacProxyImage = defaultKubeRbacProxyImage
	}
	kubeRbacProxyPullPolicy := string(corev1.PullIfNotPresent)
	tlsSecretName := "mlflow-tls"
	upstreamCAFile := "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt"
	var upstreamCASecret *string

	if mlflow.Spec.KubeRbacProxy != nil {
		// Check if explicitly enabled
		if mlflow.Spec.KubeRbacProxy.Enabled != nil {
			kubeRbacProxyEnabled = *mlflow.Spec.KubeRbacProxy.Enabled
		}

		// Image configuration
		if mlflow.Spec.KubeRbacProxy.Image != nil {
			if mlflow.Spec.KubeRbacProxy.Image.Image != nil {
				kubeRbacProxyImage = *mlflow.Spec.KubeRbacProxy.Image.Image
			}
			if mlflow.Spec.KubeRbacProxy.Image.PullPolicy != nil {
				kubeRbacProxyPullPolicy = string(*mlflow.Spec.KubeRbacProxy.Image.PullPolicy)
			}
		}

		// TLS configuration
		if mlflow.Spec.KubeRbacProxy.TLS != nil {
			if mlflow.Spec.KubeRbacProxy.TLS.SecretName != nil {
				tlsSecretName = *mlflow.Spec.KubeRbacProxy.TLS.SecretName
			}
			if mlflow.Spec.KubeRbacProxy.TLS.UpstreamCAFile != nil {
				upstreamCAFile = *mlflow.Spec.KubeRbacProxy.TLS.UpstreamCAFile
			}
			if mlflow.Spec.KubeRbacProxy.TLS.UpstreamCASecret != nil {
				upstreamCASecret = mlflow.Spec.KubeRbacProxy.TLS.UpstreamCASecret
			}
		}
	}

	// Parse image into a repository and tag for Helm
	kubeRbacProxyRepo, kubeRbacProxyTag := h.splitImage(kubeRbacProxyImage)

	tlsValues := map[string]interface{}{
		"secretName":     tlsSecretName,
		"upstreamCAFile": upstreamCAFile,
	}
	if upstreamCASecret != nil {
		tlsValues["upstreamCASecret"] = *upstreamCASecret
	}

	kubeRbacProxyValues := map[string]interface{}{
		"enabled": kubeRbacProxyEnabled,
		"image": map[string]interface{}{
			"repository": kubeRbacProxyRepo,
			"tag":        kubeRbacProxyTag,
			"pullPolicy": kubeRbacProxyPullPolicy,
		},
		"tls": tlsValues,
	}

	// KubeRbacProxy resources
	if mlflow.Spec.KubeRbacProxy != nil && mlflow.Spec.KubeRbacProxy.Resources != nil {
		kubeRbacProxyValues["resources"] = h.convertResources(mlflow.Spec.KubeRbacProxy.Resources)
	} else {
		kubeRbacProxyValues["resources"] = map[string]interface{}{
			"requests": map[string]interface{}{
				"cpu":    "100m",
				"memory": "256Mi",
			},
			"limits": map[string]interface{}{
				"cpu":    "100m",
				"memory": "256Mi",
			},
		}
	}

	values["kubeRbacProxy"] = kubeRbacProxyValues

	// OpenShift configuration
	servingCertEnabled := false
	servingCertSecretName := tlsSecretName // Use same secret name as kube-rbac-proxy by default

	if mlflow.Spec.OpenShift != nil && mlflow.Spec.OpenShift.ServingCert != nil {
		if mlflow.Spec.OpenShift.ServingCert.Enabled != nil {
			servingCertEnabled = *mlflow.Spec.OpenShift.ServingCert.Enabled
		}
		if mlflow.Spec.OpenShift.ServingCert.SecretName != nil {
			servingCertSecretName = *mlflow.Spec.OpenShift.ServingCert.SecretName
		}
	}

	values["openShift"] = map[string]interface{}{
		"servingCert": map[string]interface{}{
			"enabled":    servingCertEnabled,
			"secretName": servingCertSecretName,
		},
	}

	// Image configuration
	// Use config from environment variables as default, can be overridden by CR spec
	mlflowImage := cfg.MLflowImage
	if mlflowImage == "" {
		mlflowImage = defaultMLflowImage
	}
	imagePullPolicy := string(corev1.PullAlways)

	if mlflow.Spec.Image != nil {
		if mlflow.Spec.Image.Image != nil {
			mlflowImage = *mlflow.Spec.Image.Image
		}
		if mlflow.Spec.Image.PullPolicy != nil {
			imagePullPolicy = string(*mlflow.Spec.Image.PullPolicy)
		}
	}

	// Parse image into repository and tag for Helm
	imageRepo, imageTag := h.splitImage(mlflowImage)

	values["image"] = map[string]interface{}{
		"repository": imageRepo,
		"tag":        imageTag,
		"pullPolicy": imagePullPolicy,
	}

	// Replicas
	replicas := int32(1)
	if mlflow.Spec.Replicas != nil {
		replicas = *mlflow.Spec.Replicas
	}
	values["replicaCount"] = replicas

	// Resources
	if mlflow.Spec.Resources != nil {
		values["resources"] = h.convertResources(mlflow.Spec.Resources)
	} else {
		values["resources"] = map[string]interface{}{
			"requests": map[string]interface{}{
				"cpu":    "250m",
				"memory": "512Mi",
			},
			"limits": map[string]interface{}{
				"cpu":    "1",
				"memory": "1Gi",
			},
		}
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
		if mlflow.Spec.Storage.Size != nil {
			storageSize = mlflow.Spec.Storage.Size.String()
		}
		if mlflow.Spec.Storage.StorageClassName != nil {
			storageClassName = *mlflow.Spec.Storage.StorageClassName
		}
		if mlflow.Spec.Storage.AccessMode != nil {
			accessMode = string(*mlflow.Spec.Storage.AccessMode)
		}
	}

	values["storage"] = map[string]interface{}{
		"enabled":          storageEnabled,
		"size":             storageSize,
		"storageClassName": storageClassName,
		"accessMode":       accessMode,
	}

	// MLflow configuration
	backendStoreURI := defaultBackendStoreURI
	registryStoreURI := defaultRegistryStoreURI
	artifactsDest := defaultArtifactsDest

	if mlflow.Spec.BackendStoreURI != nil {
		backendStoreURI = *mlflow.Spec.BackendStoreURI
	}
	if mlflow.Spec.RegistryStoreURI != nil {
		registryStoreURI = *mlflow.Spec.RegistryStoreURI
	}
	if mlflow.Spec.ArtifactsDestination != nil {
		artifactsDest = *mlflow.Spec.ArtifactsDestination
	}

	// Build allowed hosts list for MLflow
	allowedHosts := []string{
		"*",                            // Wildcard to allow all hosts
		"mlflow",                       // Service name
		"mlflow." + namespace,          // Service in namespace
		"mlflow." + namespace + ".svc", // Full service name
		"mlflow." + namespace + ".svc.cluster.local", // FQDN
		"localhost", // Localhost
		"127.0.0.1", // Localhost IP
	}

	// ServeArtifacts configuration
	serveArtifacts := true
	if mlflow.Spec.ServeArtifacts != nil {
		serveArtifacts = *mlflow.Spec.ServeArtifacts
	}

	// Workers configuration
	workers := int32(1)
	if mlflow.Spec.Workers != nil {
		workers = *mlflow.Spec.Workers
	}

	values["mlflow"] = map[string]interface{}{
		"backendStoreUri":      backendStoreURI,
		"registryStoreUri":     registryStoreURI,
		"artifactsDestination": artifactsDest,
		"enableWorkspaces":     true,
		"workspaceStoreUri":    "kubernetes://",
		"serveArtifacts":       serveArtifacts,
		"workers":              workers,
		"port":                 9443,
		"allowedHosts":         allowedHosts,
	}

	// Environment variables
	env := []map[string]interface{}{
		{
			"name":  "HOME",
			"value": "/tmp",
		},
		{
			"name":  "MLFLOW_K8S_AUTH_AUTHORIZATION_MODE",
			"value": "subject_access_review",
		},
	}

	// Add custom env vars from spec
	for _, e := range mlflow.Spec.Env {
		envVar := map[string]interface{}{
			"name": e.Name,
		}
		if e.Value != "" {
			envVar["value"] = e.Value
		}
		if e.ValueFrom != nil {
			envVar["valueFrom"] = h.convertEnvVarSource(e.ValueFrom)
		}
		env = append(env, envVar)
	}

	values["env"] = env

	// EnvFrom
	if len(mlflow.Spec.EnvFrom) > 0 {
		envFrom := make([]map[string]interface{}, 0, len(mlflow.Spec.EnvFrom))
		for _, ef := range mlflow.Spec.EnvFrom {
			envFromItem := make(map[string]interface{})
			if ef.ConfigMapRef != nil {
				envFromItem["configMapRef"] = map[string]interface{}{
					"name": ef.ConfigMapRef.Name,
				}
			}
			if ef.SecretRef != nil {
				envFromItem["secretRef"] = map[string]interface{}{
					"name": ef.SecretRef.Name,
				}
			}
			envFrom = append(envFrom, envFromItem)
		}
		values["envFrom"] = envFrom
	}

	// Service account and RBAC
	values["serviceAccount"] = map[string]interface{}{
		"create": true,
		"name":   ServiceAccountName,
	}
	values["rbac"] = map[string]interface{}{
		"create": true,
	}

	// Service
	values["service"] = map[string]interface{}{
		"type":       "ClusterIP",
		"port":       8443,
		"directPort": 9443,
	}

	// Pod Security Context
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

	// Container Security Context
	if mlflow.Spec.SecurityContext != nil {
		values["securityContext"] = mlflow.Spec.SecurityContext
	} else {
		values["securityContext"] = map[string]interface{}{
			"allowPrivilegeEscalation": false,
			"readOnlyRootFilesystem":   false,
		}
	}

	// Node Selector
	if len(mlflow.Spec.NodeSelector) > 0 {
		values["nodeSelector"] = mlflow.Spec.NodeSelector
	} else {
		values["nodeSelector"] = map[string]string{}
	}

	// Tolerations
	if len(mlflow.Spec.Tolerations) > 0 {
		values["tolerations"] = mlflow.Spec.Tolerations
	} else {
		values["tolerations"] = []corev1.Toleration{}
	}

	// Affinity
	if mlflow.Spec.Affinity != nil {
		values["affinity"] = mlflow.Spec.Affinity
	} else {
		values["affinity"] = map[string]interface{}{}
	}

	return values
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

// convertResources converts Kubernetes ResourceRequirements to Helm values format
func (h *HelmRenderer) convertResources(resources *corev1.ResourceRequirements) map[string]interface{} {
	result := make(map[string]interface{})

	if resources.Requests != nil {
		requests := make(map[string]interface{})
		if cpu, ok := resources.Requests[corev1.ResourceCPU]; ok {
			requests["cpu"] = cpu.String()
		}
		if memory, ok := resources.Requests[corev1.ResourceMemory]; ok {
			requests["memory"] = memory.String()
		}
		result["requests"] = requests
	}

	if resources.Limits != nil {
		limits := make(map[string]interface{})
		if cpu, ok := resources.Limits[corev1.ResourceCPU]; ok {
			limits["cpu"] = cpu.String()
		}
		if memory, ok := resources.Limits[corev1.ResourceMemory]; ok {
			limits["memory"] = memory.String()
		}
		result["limits"] = limits
	}

	return result
}

// convertEnvVarSource converts EnvVarSource to Helm values format
func (h *HelmRenderer) convertEnvVarSource(source *corev1.EnvVarSource) map[string]interface{} {
	result := make(map[string]interface{})

	if source.SecretKeyRef != nil {
		result["secretKeyRef"] = map[string]interface{}{
			"name": source.SecretKeyRef.Name,
			"key":  source.SecretKeyRef.Key,
		}
	}
	if source.ConfigMapKeyRef != nil {
		result["configMapKeyRef"] = map[string]interface{}{
			"name": source.ConfigMapKeyRef.Name,
			"key":  source.ConfigMapKeyRef.Key,
		}
	}

	return result
}

// splitImage splits an image string into repository and tag/digest
// Handles both tag-based (image:tag) and digest-based (image@sha256:...) references
// If no tag or digest is specified, returns "latest" as the tag
func (h *HelmRenderer) splitImage(image string) (string, string) {
	// Handle digest references (image@sha256:...)
	if idx := strings.Index(image, "@"); idx != -1 {
		return image[:idx], image[idx+1:]
	}

	parts := strings.Split(image, ":")
	if len(parts) == 1 {
		return parts[0], "latest"
	}
	// Handle images with port numbers (e.g., registry.com:5000/image:tag)
	// Find the last colon which should be the tag separator
	lastColon := strings.LastIndex(image, ":")
	if lastColon == -1 {
		return image, "latest"
	}
	return image[:lastColon], image[lastColon+1:]
}

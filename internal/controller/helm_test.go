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
	"testing"

	"github.com/onsi/gomega"   // nolint:staticcheck // Named import for gomega.NewWithT; dual import for readability
	. "github.com/onsi/gomega" // Dot import for matchers like HaveOccurred
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

const (
	deploymentKind = "Deployment"

	// CA bundle test constants - these match values from values.yaml and deployment.yaml
	caCombinedVolume = "combined-ca-bundle"
	caCombinedBundle = "/etc/pki/tls/certs/combined/ca-bundle.crt"
)

func TestMlflowToHelmValues_Storage(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name           string
		mlflow         *mlflowv1.MLflow
		wantEnabled    bool
		wantSize       string
		wantClassName  string
		wantAccessMode string
	}{
		{
			name: "storage not configured - should be disabled",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			wantEnabled:    false,
			wantSize:       defaultStorageSize,
			wantClassName:  "",
			wantAccessMode: "ReadWriteOnce",
		},
		{
			name: "storage configured with defaults",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					Storage: &corev1.PersistentVolumeClaimSpec{},
				},
			},
			wantEnabled:    true,
			wantSize:       defaultStorageSize,
			wantClassName:  "",
			wantAccessMode: "ReadWriteOnce",
		},
		{
			name: "storage configured with custom values",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					Storage: &corev1.PersistentVolumeClaimSpec{
						AccessModes:      []corev1.PersistentVolumeAccessMode{corev1.ReadWriteMany},
						StorageClassName: ptr("fast-ssd"),
						Resources: corev1.VolumeResourceRequirements{
							Requests: corev1.ResourceList{
								corev1.ResourceStorage: resource.MustParse("20Gi"),
							},
						},
					},
				},
			},
			wantEnabled:    true,
			wantSize:       "20Gi",
			wantClassName:  "fast-ssd",
			wantAccessMode: "ReadWriteMany",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := gomega.NewWithT(t)

			values, err := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace", RenderOptions{})
			g.Expect(err).NotTo(HaveOccurred())

			storage, ok := values["storage"].(map[string]interface{})
			if !ok {
				t.Fatal("storage not found in values or wrong type")
			}

			if got := storage["enabled"].(bool); got != tt.wantEnabled {
				t.Errorf("storage.enabled = %v, want %v", got, tt.wantEnabled)
			}

			if got := storage["size"].(string); got != tt.wantSize {
				t.Errorf("storage.size = %v, want %v", got, tt.wantSize)
			}

			if got := storage["storageClassName"].(string); got != tt.wantClassName {
				t.Errorf("storage.storageClassName = %v, want %v", got, tt.wantClassName)
			}

			if got := storage["accessMode"].(string); got != tt.wantAccessMode {
				t.Errorf("storage.accessMode = %v, want %v", got, tt.wantAccessMode)
			}
		})
	}
}

func TestMlflowToHelmValues_Image(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name           string
		mlflow         *mlflowv1.MLflow
		wantName       string
		wantPullPolicy string // empty string means pullPolicy should not be set
	}{
		{
			name: "image not configured - should use config defaults",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			// pullPolicy should not be set when not explicitly provided
			wantPullPolicy: "",
		},
		{
			name: "image with custom values",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					Image: &mlflowv1.ImageConfig{
						Image:           ptr("custom/mlflow:v2.0.0"),
						ImagePullPolicy: ptr(corev1.PullIfNotPresent),
					},
				},
			},
			wantName:       "custom/mlflow:v2.0.0",
			wantPullPolicy: "IfNotPresent",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := gomega.NewWithT(t)

			values, err := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace", RenderOptions{})
			g.Expect(err).NotTo(HaveOccurred())

			image, ok := values["image"].(map[string]interface{})
			if !ok {
				t.Fatal("image not found in values or wrong type")
			}

			if tt.wantName != "" {
				if got := image["name"].(string); got != tt.wantName {
					t.Errorf("image.name = %v, want %v", got, tt.wantName)
				}
			}

			if tt.wantPullPolicy != "" {
				if got, ok := image["imagePullPolicy"].(string); !ok || got != tt.wantPullPolicy {
					t.Errorf("image.imagePullPolicy = %v, want %v", got, tt.wantPullPolicy)
				}
			} else {
				if _, exists := image["imagePullPolicy"]; exists {
					t.Errorf("image.imagePullPolicy should not be set but found: %v", image["imagePullPolicy"])
				}
			}
		})
	}
}

func TestMlflowToHelmValues_MLflowConfig(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name                     string
		mlflow                   *mlflowv1.MLflow
		wantBackendStoreURI      string
		wantRegistryStoreURI     string
		wantArtifactsDestination string
		wantDefaultArtifactRoot  string
		wantServeArtifacts       bool
		wantWorkers              int32
		wantBackendSecretRef     bool
		wantRegistrySecretRef    bool
	}{
		{
			name: "mlflow config not set - should use defaults",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			wantBackendStoreURI:      defaultBackendStoreURI,
			wantRegistryStoreURI:     defaultBackendStoreURI, // Registry defaults to backend
			wantArtifactsDestination: defaultArtifactsDest,
			wantDefaultArtifactRoot:  "", // Empty - let MLflow use its intelligent defaults
			wantServeArtifacts:       false,
			wantWorkers:              1,
			wantBackendSecretRef:     false,
			wantRegistrySecretRef:    false,
		},
		{
			name: "mlflow config with custom URIs",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      ptr("postgresql://host/db"),
					RegistryStoreURI:     ptr("postgresql://host/registry"),
					ArtifactsDestination: ptr("s3://bucket/artifacts"),
				},
			},
			wantBackendStoreURI:      "postgresql://host/db",
			wantRegistryStoreURI:     "postgresql://host/registry",
			wantArtifactsDestination: "s3://bucket/artifacts",
			wantDefaultArtifactRoot:  "", // Empty - let MLflow use its intelligent defaults
			wantServeArtifacts:       false,
			wantWorkers:              1,
			wantBackendSecretRef:     false,
			wantRegistrySecretRef:    false,
		},
		{
			name: "registry defaults to backend when omitted",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      ptr("postgresql://host/db"),
					ArtifactsDestination: ptr("s3://bucket/artifacts"),
					// RegistryStoreURI intentionally omitted
				},
			},
			wantBackendStoreURI:      "postgresql://host/db",
			wantRegistryStoreURI:     "postgresql://host/db", // Should default to backend
			wantArtifactsDestination: "s3://bucket/artifacts",
			wantDefaultArtifactRoot:  "", // Empty - let MLflow use its intelligent defaults
			wantServeArtifacts:       false,
			wantWorkers:              1,
			wantBackendSecretRef:     false,
			wantRegistrySecretRef:    false,
		},
		{
			name: "mlflow config with custom serveArtifacts and workers",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts: ptr(false),
					Workers:        ptr(int32(4)),
				},
			},
			wantBackendStoreURI:      defaultBackendStoreURI,
			wantRegistryStoreURI:     defaultBackendStoreURI, // Registry defaults to backend
			wantArtifactsDestination: defaultArtifactsDest,
			wantDefaultArtifactRoot:  "", // Empty - let MLflow use its intelligent defaults
			wantServeArtifacts:       false,
			wantWorkers:              4,
			wantBackendSecretRef:     false,
			wantRegistrySecretRef:    false,
		},
		{
			name: "mlflow config with secret references",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURIFrom: &corev1.SecretKeySelector{
						LocalObjectReference: corev1.LocalObjectReference{Name: "db-creds"},
						Key:                  "backend-uri",
					},
					RegistryStoreURIFrom: &corev1.SecretKeySelector{
						LocalObjectReference: corev1.LocalObjectReference{Name: "db-creds"},
						Key:                  "registry-uri",
					},
				},
			},
			wantBackendStoreURI:      defaultBackendStoreURI, // Falls back to default when using secret ref
			wantRegistryStoreURI:     defaultBackendStoreURI, // Registry defaults to backend
			wantArtifactsDestination: defaultArtifactsDest,
			wantDefaultArtifactRoot:  "", // Empty - let MLflow use its intelligent defaults
			wantServeArtifacts:       false,
			wantWorkers:              1,
			wantBackendSecretRef:     true,
			wantRegistrySecretRef:    true,
		},
		{
			name: "secret reference takes precedence over direct value",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr("postgresql://ignored"),
					BackendStoreURIFrom: &corev1.SecretKeySelector{
						LocalObjectReference: corev1.LocalObjectReference{Name: "db-creds"},
						Key:                  "backend-uri",
					},
				},
			},
			wantBackendStoreURI:      defaultBackendStoreURI, // Direct value ignored when secret ref present
			wantRegistryStoreURI:     defaultBackendStoreURI, // Registry defaults to backend
			wantArtifactsDestination: defaultArtifactsDest,
			wantDefaultArtifactRoot:  "", // Empty - let MLflow use its intelligent defaults
			wantServeArtifacts:       false,
			wantWorkers:              1,
			wantBackendSecretRef:     true,
			wantRegistrySecretRef:    true, // Should inherit backend secret ref
		},
		{
			name: "mlflow config with custom defaultArtifactRoot",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					ArtifactsDestination: ptr("s3://bucket/artifacts"),
					DefaultArtifactRoot:  ptr("s3://bucket/custom-root"),
				},
			},
			wantBackendStoreURI:      defaultBackendStoreURI,
			wantRegistryStoreURI:     defaultBackendStoreURI, // Registry defaults to backend
			wantArtifactsDestination: "s3://bucket/artifacts",
			wantDefaultArtifactRoot:  "s3://bucket/custom-root", // Custom value overrides default
			wantServeArtifacts:       false,                     // Default is now false
			wantWorkers:              1,
			wantBackendSecretRef:     false,
			wantRegistrySecretRef:    false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := gomega.NewWithT(t)

			values, err := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace", RenderOptions{})
			g.Expect(err).NotTo(HaveOccurred())

			mlflowConfig, ok := values["mlflow"].(map[string]interface{})
			if !ok {
				t.Fatal("mlflow not found in values or wrong type")
			}

			if got := mlflowConfig["backendStoreUri"].(string); got != tt.wantBackendStoreURI {
				t.Errorf("mlflow.backendStoreUri = %v, want %v", got, tt.wantBackendStoreURI)
			}

			if got := mlflowConfig["registryStoreUri"].(string); got != tt.wantRegistryStoreURI {
				t.Errorf("mlflow.registryStoreUri = %v, want %v", got, tt.wantRegistryStoreURI)
			}

			if got := mlflowConfig["artifactsDestination"].(string); got != tt.wantArtifactsDestination {
				t.Errorf("mlflow.artifactsDestination = %v, want %v", got, tt.wantArtifactsDestination)
			}

			if got := mlflowConfig["defaultArtifactRoot"].(string); got != tt.wantDefaultArtifactRoot {
				t.Errorf("mlflow.defaultArtifactRoot = %v, want %v", got, tt.wantDefaultArtifactRoot)
			}

			if got := mlflowConfig["serveArtifacts"].(bool); got != tt.wantServeArtifacts {
				t.Errorf("mlflow.serveArtifacts = %v, want %v", got, tt.wantServeArtifacts)
			}

			if got := mlflowConfig["workers"].(int32); got != tt.wantWorkers {
				t.Errorf("mlflow.workers = %v, want %v", got, tt.wantWorkers)
			}

			// Check secret references
			_, hasBackendSecretRef := mlflowConfig["backendStoreUriFrom"]
			if hasBackendSecretRef != tt.wantBackendSecretRef {
				t.Errorf("mlflow.backendStoreUriFrom exists = %v, want %v", hasBackendSecretRef, tt.wantBackendSecretRef)
			}

			_, hasRegistrySecretRef := mlflowConfig["registryStoreUriFrom"]
			if hasRegistrySecretRef != tt.wantRegistrySecretRef {
				t.Errorf("mlflow.registryStoreUriFrom exists = %v, want %v", hasRegistrySecretRef, tt.wantRegistrySecretRef)
			}

			// Validate secret ref structure if present
			if tt.wantBackendSecretRef {
				secretRef, ok := mlflowConfig["backendStoreUriFrom"].(map[string]interface{})
				if !ok {
					t.Error("backendStoreUriFrom is not a map")
				} else {
					secretKeyRef := secretRef["secretKeyRef"].(map[string]interface{})
					if secretKeyRef["name"] != "db-creds" {
						t.Errorf("backendStoreUriFrom secret name = %v, want db-creds", secretKeyRef["name"])
					}
					if secretKeyRef["key"] != "backend-uri" {
						t.Errorf("backendStoreUriFrom secret key = %v, want backend-uri", secretKeyRef["key"])
					}
				}
			}
		})
	}
}

func TestMlflowToHelmValues_StaticPrefix(t *testing.T) {
	g := gomega.NewWithT(t)

	renderer := &HelmRenderer{}

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "test"},
		Spec:       mlflowv1.MLflowSpec{},
	}

	values, err := renderer.mlflowToHelmValues(mlflow, "test-namespace", RenderOptions{})
	g.Expect(err).NotTo(HaveOccurred())

	mlflowConfig, ok := values["mlflow"].(map[string]interface{})
	if !ok {
		t.Fatal("mlflow config not found in values or wrong type")
	}

	// Verify staticPrefix is set to the constant value
	staticPrefix, ok := mlflowConfig["staticPrefix"].(string)
	if !ok {
		t.Fatal("staticPrefix not found in mlflow config or wrong type")
	}

	if staticPrefix != StaticPrefix {
		t.Errorf("staticPrefix = %v, want %v", staticPrefix, StaticPrefix)
	}

	if staticPrefix != "/mlflow" {
		t.Errorf("staticPrefix = %v, want /mlflow", staticPrefix)
	}
}

func TestMlflowToHelmValues_Env(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name        string
		mlflow      *mlflowv1.MLflow
		wantMinEnvs int // Minimum number of env vars expected
		wantEnvName string
		wantEnvVal  string
	}{
		{
			name: "no custom env vars",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			wantMinEnvs: 0, // No env vars when none are specified
		},
		{
			name: "with custom env vars",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					Env: []corev1.EnvVar{
						{Name: "CUSTOM_VAR", Value: "custom-value"},
						{Name: "AWS_REGION", Value: "us-east-1"},
					},
				},
			},
			wantMinEnvs: 2, // 2 custom env vars
			wantEnvName: "CUSTOM_VAR",
			wantEnvVal:  "custom-value",
		},
		{
			name: "with env from secret",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					Env: []corev1.EnvVar{
						{
							Name: "DB_PASSWORD",
							ValueFrom: &corev1.EnvVarSource{
								SecretKeyRef: &corev1.SecretKeySelector{
									LocalObjectReference: corev1.LocalObjectReference{
										Name: "db-secret",
									},
									Key: "password",
								},
							},
						},
					},
				},
			},
			wantMinEnvs: 1, // 1 custom env var
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := gomega.NewWithT(t)

			values, err := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace", RenderOptions{})
			g.Expect(err).NotTo(HaveOccurred())

			env, ok := values["env"].([]any)
			if !ok {
				t.Fatal("env not found in values or wrong type")
			}

			if len(env) < tt.wantMinEnvs {
				t.Errorf("env length = %v, want at least %v", len(env), tt.wantMinEnvs)
			}

			// Check for specific custom env if provided
			if tt.wantEnvName != "" {
				found := false
				for _, e := range env {
					envMap := e.(map[string]any)
					if envMap["name"] == tt.wantEnvName {
						found = true
						if envMap["value"] != tt.wantEnvVal {
							t.Errorf("env[%s] = %v, want %v", tt.wantEnvName, envMap["value"], tt.wantEnvVal)
						}
						break
					}
				}
				if !found {
					t.Errorf("custom env var %s not found", tt.wantEnvName)
				}
			}
		})
	}
}

func TestMlflowToHelmValues_EnvFrom(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name             string
		mlflow           *mlflowv1.MLflow
		wantEnvFromCount int
	}{
		{
			name: "no envFrom",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			wantEnvFromCount: 0,
		},
		{
			name: "with secret and configmap envFrom",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					EnvFrom: []corev1.EnvFromSource{
						{
							SecretRef: &corev1.SecretEnvSource{
								LocalObjectReference: corev1.LocalObjectReference{
									Name: "aws-credentials",
								},
							},
						},
						{
							ConfigMapRef: &corev1.ConfigMapEnvSource{
								LocalObjectReference: corev1.LocalObjectReference{
									Name: "app-config",
								},
							},
						},
					},
				},
			},
			wantEnvFromCount: 2,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := gomega.NewWithT(t)

			values, err := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace", RenderOptions{})
			g.Expect(err).NotTo(HaveOccurred())

			if tt.wantEnvFromCount == 0 {
				if _, exists := values["envFrom"]; exists {
					t.Error("envFrom should not exist when no envFrom is configured")
				}
				return
			}

			envFrom, ok := values["envFrom"].([]any)
			if !ok {
				t.Fatal("envFrom not found in values or wrong type")
			}

			if len(envFrom) != tt.wantEnvFromCount {
				t.Errorf("envFrom length = %v, want %v", len(envFrom), tt.wantEnvFromCount)
			}
		})
	}
}

func TestMlflowToHelmValues_Resources(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name               string
		mlflow             *mlflowv1.MLflow
		wantResourcesSet   bool
		wantRequestsCPU    string
		wantRequestsMemory string
		wantLimitsCPU      string
		wantLimitsMemory   string
	}{
		{
			name: "resources not configured - should not set in values (helm chart defaults apply)",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			wantResourcesSet: false,
		},
		{
			name: "resources with custom values",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					Resources: &corev1.ResourceRequirements{
						Requests: corev1.ResourceList{
							corev1.ResourceCPU:    resource.MustParse("500m"),
							corev1.ResourceMemory: resource.MustParse("1Gi"),
						},
						Limits: corev1.ResourceList{
							corev1.ResourceCPU:    resource.MustParse("2"),
							corev1.ResourceMemory: resource.MustParse("4Gi"),
						},
					},
				},
			},
			wantResourcesSet:   true,
			wantRequestsCPU:    "500m",
			wantRequestsMemory: "1Gi",
			wantLimitsCPU:      "2",
			wantLimitsMemory:   "4Gi",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := gomega.NewWithT(t)

			values, err := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace", RenderOptions{})
			g.Expect(err).NotTo(HaveOccurred())

			resources, ok := values["resources"].(map[string]interface{})
			if !tt.wantResourcesSet {
				if ok {
					t.Error("resources should not be set when not configured in CR spec")
				}
				return
			}

			if !ok {
				t.Fatal("resources not found in values or wrong type")
			}

			requests := resources["requests"].(map[string]interface{})
			if got := requests["cpu"].(string); got != tt.wantRequestsCPU {
				t.Errorf("resources.requests.cpu = %v, want %v", got, tt.wantRequestsCPU)
			}
			if got := requests["memory"].(string); got != tt.wantRequestsMemory {
				t.Errorf("resources.requests.memory = %v, want %v", got, tt.wantRequestsMemory)
			}

			limits := resources["limits"].(map[string]interface{})
			if got := limits["cpu"].(string); got != tt.wantLimitsCPU {
				t.Errorf("resources.limits.cpu = %v, want %v", got, tt.wantLimitsCPU)
			}
			if got := limits["memory"].(string); got != tt.wantLimitsMemory {
				t.Errorf("resources.limits.memory = %v, want %v", got, tt.wantLimitsMemory)
			}
		})
	}
}

func TestMlflowToHelmValues_Replicas(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name         string
		mlflow       *mlflowv1.MLflow
		wantReplicas int32
	}{
		{
			name: "replicas not configured - should default to 1",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			wantReplicas: 1,
		},
		{
			name: "replicas set to 3",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					Replicas: ptr(int32(3)),
				},
			},
			wantReplicas: 3,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := gomega.NewWithT(t)

			values, err := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace", RenderOptions{})
			g.Expect(err).NotTo(HaveOccurred())

			if got := values["replicaCount"].(int32); got != tt.wantReplicas {
				t.Errorf("replicaCount = %v, want %v", got, tt.wantReplicas)
			}
		})
	}
}

func TestMlflowToHelmValues_Namespace(t *testing.T) {
	g := gomega.NewWithT(t)
	renderer := &HelmRenderer{}

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "test"},
		Spec:       mlflowv1.MLflowSpec{},
	}

	testNamespace := "custom-namespace"
	values, err := renderer.mlflowToHelmValues(mlflow, testNamespace, RenderOptions{})
	g.Expect(err).NotTo(HaveOccurred())

	if got := values["namespace"].(string); got != testNamespace {
		t.Errorf("namespace = %v, want %v", got, testNamespace)
	}
}

func TestMlflowToHelmValues_ResourceSuffix(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name               string
		crName             string
		wantResourceSuffix string
	}{
		{
			name:               "singleton mlflow CR should have empty suffix",
			crName:             "mlflow",
			wantResourceSuffix: "",
		},
		{
			name:               "custom CR name should have suffix",
			crName:             "dev",
			wantResourceSuffix: "-dev",
		},
		{
			name:               "another custom CR name",
			crName:             "production",
			wantResourceSuffix: "-production",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := gomega.NewWithT(t)
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: tt.crName},
				Spec:       mlflowv1.MLflowSpec{},
			}

			values, err := renderer.mlflowToHelmValues(mlflow, "test-namespace", RenderOptions{})
			g.Expect(err).NotTo(HaveOccurred())

			if got := values["resourceSuffix"].(string); got != tt.wantResourceSuffix {
				t.Errorf("resourceSuffix = %v, want %v", got, tt.wantResourceSuffix)
			}
		})
	}
}

// TestRenderChart_EnvVars tests that env vars with both value and valueFrom are rendered correctly
func TestRenderChart_EnvVars(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "test-mlflow"},
		Spec: mlflowv1.MLflowSpec{
			Env: []corev1.EnvVar{
				{
					Name:  "SIMPLE_VAR",
					Value: "simple-value",
				},
				{
					Name: "SECRET_VAR",
					ValueFrom: &corev1.EnvVarSource{
						SecretKeyRef: &corev1.SecretKeySelector{
							LocalObjectReference: corev1.LocalObjectReference{Name: "my-secret"},
							Key:                  "password",
						},
					},
				},
				{
					Name: "CONFIGMAP_VAR",
					ValueFrom: &corev1.EnvVarSource{
						ConfigMapKeyRef: &corev1.ConfigMapKeySelector{
							LocalObjectReference: corev1.LocalObjectReference{Name: "my-config"},
							Key:                  "config-key",
						},
					},
				},
			},
		},
	}

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{})
	if err != nil {
		t.Fatalf("RenderChart() error = %v", err)
	}

	// Find the Deployment
	var deployment *unstructured.Unstructured
	for _, obj := range objs {
		if obj.GetKind() == deploymentKind {
			deployment = obj
			break
		}
	}
	if deployment == nil {
		t.Fatal("Deployment not found in rendered objects")
	}

	// Get the env vars from the MLflow container
	containers, found, err := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "containers")
	if err != nil || !found || len(containers) == 0 {
		t.Fatalf("Failed to get containers from deployment: found=%v, err=%v", found, err)
	}

	var mlflowContainer map[string]interface{}
	for _, c := range containers {
		container := c.(map[string]interface{})
		if container["name"] == "mlflow" {
			mlflowContainer = container
			break
		}
	}
	if mlflowContainer == nil {
		t.Fatal("MLflow container not found")
	}

	env, found, err := unstructured.NestedSlice(mlflowContainer, "env")
	if err != nil || !found {
		t.Fatalf("Failed to get env from container: found=%v, err=%v", found, err)
	}

	// Check for SIMPLE_VAR with value
	foundSimpleVar := false
	for _, e := range env {
		envVar := e.(map[string]interface{})
		if envVar["name"] == "SIMPLE_VAR" {
			foundSimpleVar = true
			if envVar["value"] != "simple-value" {
				t.Errorf("SIMPLE_VAR value = %v, want 'simple-value'", envVar["value"])
			}
			if _, hasValueFrom := envVar["valueFrom"]; hasValueFrom {
				t.Error("SIMPLE_VAR should not have valueFrom")
			}
		}
	}
	if !foundSimpleVar {
		t.Error("SIMPLE_VAR not found in env")
	}

	// Check for SECRET_VAR with valueFrom
	foundSecretVar := false
	for _, e := range env {
		envVar := e.(map[string]interface{})
		if envVar["name"] == "SECRET_VAR" {
			foundSecretVar = true
			if _, hasValue := envVar["value"]; hasValue {
				t.Error("SECRET_VAR should not have value field")
			}
			valueFrom, ok := envVar["valueFrom"].(map[string]interface{})
			if !ok {
				t.Fatal("SECRET_VAR valueFrom not found or wrong type")
			}
			secretKeyRef, ok := valueFrom["secretKeyRef"].(map[string]interface{})
			if !ok {
				t.Fatal("SECRET_VAR secretKeyRef not found or wrong type")
			}
			if secretKeyRef["name"] != "my-secret" {
				t.Errorf("SECRET_VAR secret name = %v, want 'my-secret'", secretKeyRef["name"])
			}
			if secretKeyRef["key"] != "password" {
				t.Errorf("SECRET_VAR secret key = %v, want 'password'", secretKeyRef["key"])
			}
		}
	}
	if !foundSecretVar {
		t.Error("SECRET_VAR not found in env")
	}

	// Check for CONFIGMAP_VAR with valueFrom
	foundConfigMapVar := false
	for _, e := range env {
		envVar := e.(map[string]interface{})
		if envVar["name"] == "CONFIGMAP_VAR" {
			foundConfigMapVar = true
			valueFrom, ok := envVar["valueFrom"].(map[string]interface{})
			if !ok {
				t.Fatal("CONFIGMAP_VAR valueFrom not found or wrong type")
			}
			configMapKeyRef, ok := valueFrom["configMapKeyRef"].(map[string]interface{})
			if !ok {
				t.Fatal("CONFIGMAP_VAR configMapKeyRef not found or wrong type")
			}
			if configMapKeyRef["name"] != "my-config" {
				t.Errorf("CONFIGMAP_VAR configmap name = %v, want 'my-config'", configMapKeyRef["name"])
			}
			if configMapKeyRef["key"] != "config-key" {
				t.Errorf("CONFIGMAP_VAR configmap key = %v, want 'config-key'", configMapKeyRef["key"])
			}
		}
	}
	if !foundConfigMapVar {
		t.Error("CONFIGMAP_VAR not found in env")
	}
}

// TestRenderChart tests the full helm chart rendering including YAML parsing
func TestRenderChart(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")

	tests := []struct {
		name         string
		mlflow       *mlflowv1.MLflow
		namespace    string
		wantErr      bool
		validateObjs func(t *testing.T, objs []*unstructured.Unstructured)
	}{
		{
			name: "basic rendering should succeed",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Name: "test-mlflow",
				},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      ptr("sqlite:////mlflow/mlflow.db"),
					RegistryStoreURI:     ptr("sqlite:////mlflow/mlflow.db"),
					ArtifactsDestination: ptr("file:///mlflow/artifacts"),
				},
			},
			namespace: "test-ns",
			wantErr:   false,
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				if len(objs) == 0 {
					t.Fatal("expected rendered objects, got none")
				}

				// Should have Deployment
				foundDeployment := false
				for _, obj := range objs {
					if obj.GetKind() == deploymentKind {
						foundDeployment = true
					}
				}
				if !foundDeployment {
					t.Error("Deployment not found in rendered objects")
				}
			},
		},
		{
			name: "deployment should include static prefix in health probes",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Name: "test-mlflow",
				},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      ptr("sqlite:////mlflow/mlflow.db"),
					RegistryStoreURI:     ptr("sqlite:////mlflow/mlflow.db"),
					ArtifactsDestination: ptr("file:///mlflow/artifacts"),
				},
			},
			namespace: "test-ns",
			wantErr:   false,
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				for _, obj := range objs {
					if obj.GetKind() != deploymentKind {
						continue
					}

					containers, found, err := unstructured.NestedSlice(obj.Object, "spec", "template", "spec", "containers")
					if err != nil || !found || len(containers) == 0 {
						t.Fatalf("Failed to get containers from deployment: found=%v, err=%v", found, err)
					}

					container := containers[0].(map[string]interface{})
					expectedPath := StaticPrefix + "/health"

					livenessPath, found, err := unstructured.NestedString(container, "livenessProbe", "httpGet", "path")
					if err != nil || !found {
						t.Fatalf("Failed to get livenessProbe path: found=%v, err=%v", found, err)
					}
					if livenessPath != expectedPath {
						t.Errorf("livenessProbe path = %s, want %s", livenessPath, expectedPath)
					}

					readinessPath, found, err := unstructured.NestedString(container, "readinessProbe", "httpGet", "path")
					if err != nil || !found {
						t.Fatalf("Failed to get readinessProbe path: found=%v, err=%v", found, err)
					}
					if readinessPath != expectedPath {
						t.Errorf("readinessProbe path = %s, want %s", readinessPath, expectedPath)
					}
				}
			},
		},
		{
			name: "deployment should have allowed hosts configured",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Name: "test-mlflow",
				},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      ptr("sqlite:////mlflow/mlflow.db"),
					RegistryStoreURI:     ptr("sqlite:////mlflow/mlflow.db"),
					ArtifactsDestination: ptr("file:///mlflow/artifacts"),
				},
			},
			namespace: "test-ns",
			wantErr:   false,
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				for _, obj := range objs {
					if obj.GetKind() == deploymentKind {
						// Check allowed hosts are in args
						containers, found, err := unstructured.NestedSlice(obj.Object, "spec", "template", "spec", "containers")
						if err != nil || !found || len(containers) == 0 {
							t.Fatalf("Failed to get containers from deployment: found=%v, err=%v", found, err)
						}

						container := containers[0].(map[string]interface{})
						args, found, err := unstructured.NestedStringSlice(container, "args")
						if err != nil || !found {
							t.Fatalf("Failed to get args from container: found=%v, err=%v", found, err)
						}

						// Check for --allowed-hosts arg
						hasAllowedHosts := false
						for i, arg := range args {
							if arg == "--allowed-hosts" {
								hasAllowedHosts = true
								// Next arg should be the comma-separated list
								if i+1 < len(args) {
									hosts := args[i+1]
									if hosts == "" {
										t.Error("--allowed-hosts flag present but hosts list is empty")
									}
									t.Logf("Allowed hosts: %s", hosts)
								}
								break
							}
						}
						if !hasAllowedHosts {
							t.Error("--allowed-hosts not found in deployment args")
						}

						staticPrefixArg := "--static-prefix=" + StaticPrefix
						hasStaticPrefixArg := false
						for _, arg := range args {
							if arg == staticPrefixArg {
								hasStaticPrefixArg = true
								break
							}
						}
						if !hasStaticPrefixArg {
							t.Errorf("%s not found in deployment args", staticPrefixArg)
						}
					}
				}
			},
		},
		{
			name: "RBAC resources should use static ClusterRole name and resourceSuffix for ClusterRoleBinding",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Name: "my-instance",
				},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      ptr("sqlite:////mlflow/mlflow.db"),
					RegistryStoreURI:     ptr("sqlite:////mlflow/mlflow.db"),
					ArtifactsDestination: ptr("file:///mlflow/artifacts"),
				},
			},
			namespace: "test-ns",
			wantErr:   false,
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				expectedSuffix := "-my-instance"
				expectedBindingName := "mlflow" + expectedSuffix
				// ClusterRole is static (shared across all instances)
				expectedClusterRoleName := "mlflow"

				foundClusterRole := false
				foundClusterRoleBinding := false

				for _, obj := range objs {
					switch obj.GetKind() {
					case "ClusterRole":
						foundClusterRole = true
						if obj.GetName() != expectedClusterRoleName {
							t.Errorf("ClusterRole name = %s, want %s (should be static, shared across all MLflow instances)", obj.GetName(), expectedClusterRoleName)
						}
					case "ClusterRoleBinding":
						foundClusterRoleBinding = true
						if obj.GetName() != expectedBindingName {
							t.Errorf("ClusterRoleBinding name = %s, want %s (should include resourceSuffix)", obj.GetName(), expectedBindingName)
						}
						// Verify it references the static ClusterRole
						roleRef, found, err := unstructured.NestedString(obj.Object, "roleRef", "name")
						if err != nil || !found {
							t.Fatalf("Failed to get roleRef.name from ClusterRoleBinding: found=%v, err=%v", found, err)
						}
						if roleRef != expectedClusterRoleName {
							t.Errorf("ClusterRoleBinding roleRef.name = %s, want %s", roleRef, expectedClusterRoleName)
						}
					}
				}

				if !foundClusterRole {
					t.Error("ClusterRole not found in rendered objects")
				}
				if !foundClusterRoleBinding {
					t.Error("ClusterRoleBinding not found in rendered objects")
				}
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			objs, err := renderer.RenderChart(tt.mlflow, tt.namespace, RenderOptions{})
			if (err != nil) != tt.wantErr {
				t.Fatalf("RenderChart() error = %v, wantErr %v", err, tt.wantErr)
			}
			if !tt.wantErr && tt.validateObjs != nil {
				tt.validateObjs(t, objs)
			}
		})
	}
}

func TestMlflowToHelmValues_CABundle(t *testing.T) {
	renderer := &HelmRenderer{}

	// Test: no CA bundles configured
	values, err := renderer.mlflowToHelmValues(&mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec:       mlflowv1.MLflowSpec{},
	}, "test-ns", RenderOptions{PlatformTrustedCABundleExists: false})
	if err != nil {
		t.Fatalf("mlflowToHelmValues() error = %v", err)
	}

	// caBundle should have empty configMaps when no CA bundles are configured
	caBundle := values["caBundle"].(map[string]interface{})
	configMaps := caBundle["configMaps"].([]map[string]interface{})
	if len(configMaps) != 0 {
		t.Errorf("caBundle.configMaps should be empty, got %d", len(configMaps))
	}

	// filePaths should always include the system CA path
	filePaths := caBundle["filePaths"].([]string)
	if len(filePaths) != 1 || filePaths[0] != systemCAPath {
		t.Errorf("caBundle.filePaths should be [%s], got %v", systemCAPath, filePaths)
	}

	// Test: user-provided CA bundle only
	values, err = renderer.mlflowToHelmValues(&mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec: mlflowv1.MLflowSpec{
			CABundleConfigMap: &mlflowv1.CABundleConfigMapSpec{Name: "my-ca"},
		},
	}, "test-ns", RenderOptions{PlatformTrustedCABundleExists: false})
	if err != nil {
		t.Fatalf("mlflowToHelmValues() error = %v", err)
	}

	caBundle = values["caBundle"].(map[string]interface{})
	configMaps = caBundle["configMaps"].([]map[string]interface{})
	if len(configMaps) != 1 {
		t.Fatalf("caBundle.configMaps should have 1 entry, got %d", len(configMaps))
	}
	if configMaps[0]["name"].(string) != "my-ca" {
		t.Errorf("configMaps[0].name = %v, want my-ca", configMaps[0]["name"])
	}
	if configMaps[0]["mountPath"].(string) != caCustomMount {
		t.Errorf("configMaps[0].mountPath = %v, want %v", configMaps[0]["mountPath"], caCustomMount)
	}

	// Test: ODH CA bundle only (no user-provided)
	values, err = renderer.mlflowToHelmValues(&mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec:       mlflowv1.MLflowSpec{},
	}, "test-ns", RenderOptions{PlatformTrustedCABundleExists: true})
	if err != nil {
		t.Fatalf("mlflowToHelmValues() error = %v", err)
	}

	caBundle = values["caBundle"].(map[string]interface{})
	configMaps = caBundle["configMaps"].([]map[string]interface{})
	if len(configMaps) != 1 {
		t.Fatalf("caBundle.configMaps should have 1 entry, got %d", len(configMaps))
	}
	if configMaps[0]["name"].(string) != PlatformTrustedCABundleConfigMapName {
		t.Errorf("configMaps[0].name = %v, want %v", configMaps[0]["name"], PlatformTrustedCABundleConfigMapName)
	}
	if configMaps[0]["mountPath"].(string) != caPlatformMount {
		t.Errorf("configMaps[0].mountPath = %v, want %v", configMaps[0]["mountPath"], caPlatformMount)
	}

	// Test: both CA bundles enabled - combined bundle has both ConfigMaps
	values, err = renderer.mlflowToHelmValues(&mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec: mlflowv1.MLflowSpec{
			CABundleConfigMap: &mlflowv1.CABundleConfigMapSpec{Name: "my-ca"},
		},
	}, "test-ns", RenderOptions{PlatformTrustedCABundleExists: true})
	if err != nil {
		t.Fatalf("mlflowToHelmValues() error = %v", err)
	}

	caBundle = values["caBundle"].(map[string]interface{})
	configMaps = caBundle["configMaps"].([]map[string]interface{})
	if len(configMaps) != 2 {
		t.Fatalf("caBundle.configMaps should have 2 entries, got %d", len(configMaps))
	}
	// Platform CA should be first
	if configMaps[0]["name"].(string) != PlatformTrustedCABundleConfigMapName {
		t.Errorf("configMaps[0].name = %v, want %v", configMaps[0]["name"], PlatformTrustedCABundleConfigMapName)
	}
	// Custom CA should be second
	if configMaps[1]["name"].(string) != "my-ca" {
		t.Errorf("configMaps[1].name = %v, want my-ca", configMaps[1]["name"])
	}
}

func TestRenderChart_CABundle(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")

	// Test with both CA bundles enabled - the most comprehensive case
	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec: mlflowv1.MLflowSpec{
			CABundleConfigMap: &mlflowv1.CABundleConfigMapSpec{Name: "my-ca"},
		},
	}

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{PlatformTrustedCABundleExists: true})
	if err != nil {
		t.Fatalf("RenderChart() error = %v", err)
	}

	// Find the Deployment
	var deployment *unstructured.Unstructured
	for _, obj := range objs {
		if obj.GetKind() == deploymentKind {
			deployment = obj
			break
		}
	}
	if deployment == nil {
		t.Fatal("Deployment not found")
	}

	// Check init container exists for combining CA bundles
	initContainers, _, _ := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "initContainers")
	if len(initContainers) == 0 {
		t.Fatal("init containers not found - should have combine-ca-bundles init container")
	}
	initContainer := initContainers[0].(map[string]interface{})
	if initContainer["name"].(string) != "combine-ca-bundles" {
		t.Errorf("init container name = %v, want combine-ca-bundles", initContainer["name"])
	}

	// Check all CA bundle-related env vars exist
	containers, _, _ := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "containers")
	container := containers[0].(map[string]interface{})
	envVars, _, _ := unstructured.NestedSlice(container, "env")

	// These are all the env vars that should be set when CA bundles are enabled
	requiredEnvVars := []string{
		"SSL_CERT_FILE",      // Python ssl module, OpenSSL, httpx
		"REQUESTS_CA_BUNDLE", // requests library
		"CURL_CA_BUNDLE",     // pycurl fallback
		"AWS_CA_BUNDLE",      // boto3/botocore for S3
		"PGSSLROOTCERT",      // psycopg2 for PostgreSQL
	}

	foundEnvVars := make(map[string]string)
	for _, env := range envVars {
		envMap := env.(map[string]interface{})
		name := envMap["name"].(string)
		if value, ok := envMap["value"].(string); ok {
			foundEnvVars[name] = value
		}
	}

	for _, required := range requiredEnvVars {
		if _, found := foundEnvVars[required]; !found {
			t.Errorf("required env var %s not found", required)
		}
	}

	// Verify file-based env vars point to combined CA bundle (includes system + ODH + user CAs)
	expectedFilePath := caCombinedBundle
	fileBasedEnvVars := []string{"SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "AWS_CA_BUNDLE", "PGSSLROOTCERT"}
	for _, envName := range fileBasedEnvVars {
		if foundEnvVars[envName] != expectedFilePath {
			t.Errorf("%s = %v, want %v", envName, foundEnvVars[envName], expectedFilePath)
		}
	}

	// Verify PGSSLMODE is set to verify-full for security
	if foundEnvVars["PGSSLMODE"] != "verify-full" {
		t.Errorf("PGSSLMODE = %v, want verify-full", foundEnvVars["PGSSLMODE"])
	}

	// Check combined-ca-bundle volume mount exists on main container
	volumeMounts, _, _ := unstructured.NestedSlice(container, "volumeMounts")
	foundCombined := false
	for _, vm := range volumeMounts {
		name := vm.(map[string]interface{})["name"].(string)
		if name == caCombinedVolume {
			foundCombined = true
		}
	}
	if !foundCombined {
		t.Errorf("%s volume mount not found on main container", caCombinedVolume)
	}

	// Check that init container has all required volume mounts for combining bundles
	// With the new structure, volume names are ca-bundle-0 (platform) and ca-bundle-1 (custom)
	initVolumeMounts, _, _ := unstructured.NestedSlice(initContainer, "volumeMounts")
	foundInitCombined := false
	caVolumeCount := 0
	for _, vm := range initVolumeMounts {
		name := vm.(map[string]interface{})["name"].(string)
		if name == caCombinedVolume {
			foundInitCombined = true
		}
		if len(name) > 10 && name[:10] == "ca-bundle-" {
			caVolumeCount++
		}
	}
	if !foundInitCombined {
		t.Errorf("init container: %s volume mount not found", caCombinedVolume)
	}
	if caVolumeCount != 2 {
		t.Errorf("init container: expected 2 ca-bundle-* volume mounts, got %d", caVolumeCount)
	}

	// Check volumes exist including combined-ca-bundle emptyDir
	volumes, _, _ := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "volumes")
	foundCombinedVolume := false
	for _, vol := range volumes {
		volMap := vol.(map[string]interface{})
		name := volMap["name"].(string)
		if name == caCombinedVolume {
			foundCombinedVolume = true
			// Should be an emptyDir
			if _, ok := volMap["emptyDir"]; !ok {
				t.Errorf("%s volume should be an emptyDir", caCombinedVolume)
			}
		}
		// Check CA ConfigMap volumes have optional: true
		if len(name) > 10 && name[:10] == "ca-bundle-" {
			configMap, _, _ := unstructured.NestedMap(volMap, "configMap")
			if optional, ok := configMap["optional"].(bool); !ok || !optional {
				t.Errorf("volume %s should have optional: true", name)
			}
		}
	}
	if !foundCombinedVolume {
		t.Errorf("%s volume not found", caCombinedVolume)
	}
}

func TestRenderChart_CABundle_ODHOnly(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")

	// Test with only ODH CA bundle (no user-provided)
	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec:       mlflowv1.MLflowSpec{},
	}

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{PlatformTrustedCABundleExists: true})
	if err != nil {
		t.Fatalf("RenderChart() error = %v", err)
	}

	// Find the Deployment
	var deployment *unstructured.Unstructured
	for _, obj := range objs {
		if obj.GetKind() == deploymentKind {
			deployment = obj
			break
		}
	}
	if deployment == nil {
		t.Fatal("Deployment not found")
	}

	// Check init container exists
	initContainers, _, _ := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "initContainers")
	if len(initContainers) == 0 {
		t.Fatal("init containers not found")
	}

	containers, _, _ := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "containers")
	container := containers[0].(map[string]interface{})
	envVars, _, _ := unstructured.NestedSlice(container, "env")

	foundEnvVars := make(map[string]string)
	for _, env := range envVars {
		envMap := env.(map[string]interface{})
		name := envMap["name"].(string)
		if value, ok := envMap["value"].(string); ok {
			foundEnvVars[name] = value
		}
	}

	// Verify file-based env vars point to combined CA bundle
	expectedFilePath := caCombinedBundle
	fileBasedEnvVars := []string{"SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "AWS_CA_BUNDLE", "PGSSLROOTCERT"}
	for _, envName := range fileBasedEnvVars {
		if foundEnvVars[envName] != expectedFilePath {
			t.Errorf("%s = %v, want %v", envName, foundEnvVars[envName], expectedFilePath)
		}
	}
}

func TestRenderChart_NoCABundle(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")

	// Test with no CA bundles configured
	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec:       mlflowv1.MLflowSpec{},
	}

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{PlatformTrustedCABundleExists: false})
	if err != nil {
		t.Fatalf("RenderChart() error = %v", err)
	}

	// Find the Deployment
	var deployment *unstructured.Unstructured
	for _, obj := range objs {
		if obj.GetKind() == deploymentKind {
			deployment = obj
			break
		}
	}
	if deployment == nil {
		t.Fatal("Deployment not found")
	}

	// Check no init containers exist when CA bundles are not configured
	initContainers, _, _ := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "initContainers")
	if len(initContainers) > 0 {
		t.Error("init containers should not exist when no CA bundles are configured")
	}

	// Check no combined-ca-bundle volume exists
	volumes, _, _ := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "volumes")
	for _, vol := range volumes {
		volMap := vol.(map[string]interface{})
		if volMap["name"].(string) == caCombinedVolume {
			t.Errorf("%s volume should not exist when no CA bundles are configured", caCombinedVolume)
		}
	}

	// Check CA bundle env vars are not set
	containers, _, _ := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "containers")
	container := containers[0].(map[string]interface{})
	envVars, _, _ := unstructured.NestedSlice(container, "env")

	caBundleEnvVars := []string{"SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "AWS_CA_BUNDLE", "PGSSLROOTCERT"}
	for _, env := range envVars {
		envMap := env.(map[string]interface{})
		name := envMap["name"].(string)
		for _, caVar := range caBundleEnvVars {
			if name == caVar {
				t.Errorf("env var %s should not be set when no CA bundles are configured", name)
			}
		}
	}
}

func TestMlflowToHelmValues_Metrics(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name           string
		mlflow         *mlflowv1.MLflow
		namespace      string
		wantEnabled    bool
		wantServerName string
	}{
		{
			name: "metrics always enabled with CA-based tlsConfig",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			namespace:      "test-namespace",
			wantEnabled:    true,
			wantServerName: "mlflow.test-namespace.svc",
		},
		{
			name: "metrics with custom CR name includes suffix in serverName",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "custom-mlflow"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			namespace:      "opendatahub",
			wantEnabled:    true,
			wantServerName: "mlflow-custom-mlflow.opendatahub.svc",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := gomega.NewWithT(t)

			values, err := renderer.mlflowToHelmValues(tt.mlflow, tt.namespace, RenderOptions{})
			g.Expect(err).NotTo(HaveOccurred())

			metrics, ok := values["metrics"].(map[string]interface{})
			g.Expect(ok).To(BeTrue(), "metrics should be present in values")

			enabled, ok := metrics["enabled"].(bool)
			g.Expect(ok).To(BeTrue(), "metrics.enabled should be present")
			g.Expect(enabled).To(Equal(tt.wantEnabled))

			// tlsConfig should always contain CA-based verification
			tlsConfig, hasTLSConfig := metrics["tlsConfig"].(map[string]interface{})
			g.Expect(hasTLSConfig).To(BeTrue(), "metrics.tlsConfig should always be present")

			// Verify CA config
			ca, ok := tlsConfig["ca"].(map[string]interface{})
			g.Expect(ok).To(BeTrue(), "tlsConfig.ca should be present")

			configMap, ok := ca["configMap"].(map[string]interface{})
			g.Expect(ok).To(BeTrue(), "tlsConfig.ca.configMap should be present")
			g.Expect(configMap["name"]).To(Equal("openshift-service-ca.crt"))
			g.Expect(configMap["key"]).To(Equal("service-ca.crt"))

			// Verify serverName
			serverName, ok := tlsConfig["serverName"].(string)
			g.Expect(ok).To(BeTrue(), "tlsConfig.serverName should be present")
			g.Expect(serverName).To(Equal(tt.wantServerName))
		})
	}
}

func TestRenderChart_ServiceMonitorWithTLSConfig(t *testing.T) {
	g := gomega.NewWithT(t)
	renderer := NewHelmRenderer("../../charts/mlflow")

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "test-mlflow"},
		Spec:       mlflowv1.MLflowSpec{},
	}

	// Render chart - CA-based tlsConfig is always set
	objs, err := renderer.RenderChart(mlflow, "opendatahub", RenderOptions{})
	g.Expect(err).NotTo(gomega.HaveOccurred())

	// Find the ServiceMonitor
	var serviceMonitor *unstructured.Unstructured
	for _, obj := range objs {
		if obj.GetKind() == "ServiceMonitor" {
			serviceMonitor = obj
			break
		}
	}
	g.Expect(serviceMonitor).NotTo(gomega.BeNil(), "ServiceMonitor should be rendered when metrics.enabled=true")

	// Verify ServiceMonitor metadata
	g.Expect(serviceMonitor.GetName()).To(gomega.Equal("mlflow-metrics-monitor-test-mlflow"))
	g.Expect(serviceMonitor.GetNamespace()).To(gomega.Equal("opendatahub"))

	// Verify labels include app.kubernetes.io/name
	labels := serviceMonitor.GetLabels()
	g.Expect(labels["app"]).To(gomega.Equal("mlflow"))
	g.Expect(labels["app.kubernetes.io/name"]).To(gomega.Equal("mlflow-test-mlflow"))

	// Verify endpoints configuration
	endpoints, found, err := unstructured.NestedSlice(serviceMonitor.Object, "spec", "endpoints")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue())
	g.Expect(endpoints).To(gomega.HaveLen(1))

	endpoint := endpoints[0].(map[string]interface{})
	g.Expect(endpoint["path"]).To(gomega.Equal("/metrics"))
	g.Expect(endpoint["port"]).To(gomega.Equal("https"))
	g.Expect(endpoint["scheme"]).To(gomega.Equal("https"))

	// Verify TLS config with CA bundle reference
	tlsConfig, ok := endpoint["tlsConfig"].(map[string]interface{})
	g.Expect(ok).To(gomega.BeTrue(), "tlsConfig should be present")

	ca, ok := tlsConfig["ca"].(map[string]interface{})
	g.Expect(ok).To(gomega.BeTrue(), "tlsConfig.ca should be present")

	configMap, ok := ca["configMap"].(map[string]interface{})
	g.Expect(ok).To(gomega.BeTrue(), "tlsConfig.ca.configMap should be present")
	g.Expect(configMap["name"]).To(gomega.Equal("openshift-service-ca.crt"))
	g.Expect(configMap["key"]).To(gomega.Equal("service-ca.crt"))

	// Verify serverName for certificate validation
	serverName, ok := tlsConfig["serverName"].(string)
	g.Expect(ok).To(gomega.BeTrue(), "tlsConfig.serverName should be present")
	g.Expect(serverName).To(gomega.Equal("mlflow-test-mlflow.opendatahub.svc"))

	// Verify selector matches Service labels
	matchLabels, found, err := unstructured.NestedStringMap(serviceMonitor.Object, "spec", "selector", "matchLabels")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue())
	g.Expect(matchLabels["app"]).To(gomega.Equal("mlflow"))
	g.Expect(matchLabels["app.kubernetes.io/name"]).To(gomega.Equal("mlflow-test-mlflow"))
}

func TestRenderChart_ServiceMonitorDefaultCR(t *testing.T) {
	g := gomega.NewWithT(t)
	renderer := NewHelmRenderer("../../charts/mlflow")

	// Default CR name "mlflow" should produce no suffix in service name
	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec:       mlflowv1.MLflowSpec{},
	}

	objs, err := renderer.RenderChart(mlflow, "default", RenderOptions{})
	g.Expect(err).NotTo(gomega.HaveOccurred())

	// Find the ServiceMonitor
	var serviceMonitor *unstructured.Unstructured
	for _, obj := range objs {
		if obj.GetKind() == "ServiceMonitor" {
			serviceMonitor = obj
			break
		}
	}
	g.Expect(serviceMonitor).NotTo(gomega.BeNil(), "ServiceMonitor should be rendered when metrics.enabled=true")

	// Verify endpoints configuration
	endpoints, found, err := unstructured.NestedSlice(serviceMonitor.Object, "spec", "endpoints")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue())
	g.Expect(endpoints).To(gomega.HaveLen(1))

	endpoint := endpoints[0].(map[string]interface{})

	// Verify TLS config always has CA-based verification
	tlsConfig, ok := endpoint["tlsConfig"].(map[string]interface{})
	g.Expect(ok).To(gomega.BeTrue(), "tlsConfig should be present")

	ca, ok := tlsConfig["ca"].(map[string]interface{})
	g.Expect(ok).To(gomega.BeTrue(), "tlsConfig.ca should be present")

	configMap, ok := ca["configMap"].(map[string]interface{})
	g.Expect(ok).To(gomega.BeTrue(), "tlsConfig.ca.configMap should be present")
	g.Expect(configMap["name"]).To(gomega.Equal("openshift-service-ca.crt"))
	g.Expect(configMap["key"]).To(gomega.Equal("service-ca.crt"))

	// Verify serverName uses the default service name (no suffix for "mlflow" CR name)
	serverName, ok := tlsConfig["serverName"].(string)
	g.Expect(ok).To(gomega.BeTrue(), "tlsConfig.serverName should be present")
	g.Expect(serverName).To(gomega.Equal("mlflow.default.svc"))
}

// Helper function to create pointers
func ptr[T any](v T) *T {
	return &v
}

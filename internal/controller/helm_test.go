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

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

const (
	deploymentKind = "Deployment"
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
			values := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace")

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
			values := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace")

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
			wantDefaultArtifactRoot:  defaultArtifactsDest, // Defaults to artifactsDestination
			wantServeArtifacts:       false,                // Default is now false
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
			wantDefaultArtifactRoot:  "s3://bucket/artifacts", // Defaults to artifactsDestination
			wantServeArtifacts:       false,                   // Default is now false
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
			wantDefaultArtifactRoot:  "s3://bucket/artifacts",
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
			wantDefaultArtifactRoot:  defaultArtifactsDest, // Defaults to artifactsDestination
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
			wantDefaultArtifactRoot:  defaultArtifactsDest, // Defaults to artifactsDestination
			wantServeArtifacts:       false,                // Default is now false
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
			wantDefaultArtifactRoot:  defaultArtifactsDest, // Defaults to artifactsDestination
			wantServeArtifacts:       false,                // Default is now false
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
			values := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace")

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
	renderer := &HelmRenderer{}

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "test"},
		Spec:       mlflowv1.MLflowSpec{},
	}

	values := renderer.mlflowToHelmValues(mlflow, "test-namespace")

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
			values := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace")

			env, ok := values["env"].([]map[string]interface{})
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
					if e["name"] == tt.wantEnvName {
						found = true
						if e["value"] != tt.wantEnvVal {
							t.Errorf("env[%s] = %v, want %v", tt.wantEnvName, e["value"], tt.wantEnvVal)
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
			values := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace")

			if tt.wantEnvFromCount == 0 {
				if _, exists := values["envFrom"]; exists {
					t.Error("envFrom should not exist when no envFrom is configured")
				}
				return
			}

			envFrom, ok := values["envFrom"].([]map[string]interface{})
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
			values := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace")

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
			values := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace")

			if got := values["replicaCount"].(int32); got != tt.wantReplicas {
				t.Errorf("replicaCount = %v, want %v", got, tt.wantReplicas)
			}
		})
	}
}

func TestMlflowToHelmValues_Namespace(t *testing.T) {
	renderer := &HelmRenderer{}

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "test"},
		Spec:       mlflowv1.MLflowSpec{},
	}

	testNamespace := "custom-namespace"
	values := renderer.mlflowToHelmValues(mlflow, testNamespace)

	if got := values["namespace"].(string); got != testNamespace {
		t.Errorf("namespace = %v, want %v", got, testNamespace)
	}
}

func TestConvertResources(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name      string
		resources *corev1.ResourceRequirements
		wantKeys  []string
	}{
		{
			name: "resources with requests and limits",
			resources: &corev1.ResourceRequirements{
				Requests: corev1.ResourceList{
					corev1.ResourceCPU:    resource.MustParse("100m"),
					corev1.ResourceMemory: resource.MustParse("256Mi"),
				},
				Limits: corev1.ResourceList{
					corev1.ResourceCPU:    resource.MustParse("500m"),
					corev1.ResourceMemory: resource.MustParse("1Gi"),
				},
			},
			wantKeys: []string{"requests", "limits"},
		},
		{
			name: "resources with only requests",
			resources: &corev1.ResourceRequirements{
				Requests: corev1.ResourceList{
					corev1.ResourceCPU: resource.MustParse("100m"),
				},
			},
			wantKeys: []string{"requests"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := renderer.convertResources(tt.resources)

			for _, key := range tt.wantKeys {
				if _, exists := result[key]; !exists {
					t.Errorf("expected key %s not found in result", key)
				}
			}
		})
	}
}

func TestConvertEnvVarSource(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name   string
		source *corev1.EnvVarSource
		want   string // Expected key in result
	}{
		{
			name: "secretKeyRef",
			source: &corev1.EnvVarSource{
				SecretKeyRef: &corev1.SecretKeySelector{
					LocalObjectReference: corev1.LocalObjectReference{Name: "my-secret"},
					Key:                  "password",
				},
			},
			want: "secretKeyRef",
		},
		{
			name: "configMapKeyRef",
			source: &corev1.EnvVarSource{
				ConfigMapKeyRef: &corev1.ConfigMapKeySelector{
					LocalObjectReference: corev1.LocalObjectReference{Name: "my-config"},
					Key:                  "config-key",
				},
			},
			want: "configMapKeyRef",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := renderer.convertEnvVarSource(tt.source)

			if _, exists := result[tt.want]; !exists {
				t.Errorf("expected key %s not found in result", tt.want)
			}
		})
	}
}

// TestRenderChart_EnvVars tests that env vars with both value and valueFrom are rendered correctly
//
//nolint:gocyclo // Test function with comprehensive test cases
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

	objs, err := renderer.RenderChart(mlflow, "test-ns")
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
					}
				}
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			objs, err := renderer.RenderChart(tt.mlflow, tt.namespace)
			if (err != nil) != tt.wantErr {
				t.Fatalf("RenderChart() error = %v, wantErr %v", err, tt.wantErr)
			}
			if !tt.wantErr && tt.validateObjs != nil {
				tt.validateObjs(t, objs)
			}
		})
	}
}

func TestMlflowToHelmValues_KubeRbacProxyImage(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name           string
		mlflow         *mlflowv1.MLflow
		wantEnabled    bool
		wantName       string
		wantPullPolicy string // empty string means pullPolicy should not be set
		wantSecretName string
	}{
		{
			name: "kube-rbac-proxy with default config",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			wantEnabled:    false,
			wantPullPolicy: "", // pullPolicy should not be set when not explicitly provided
			wantSecretName: "mlflow-tls",
		},
		{
			name: "kube-rbac-proxy enabled with custom image",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					KubeRbacProxy: &mlflowv1.KubeRbacProxyConfig{
						Enabled: ptr(true),
						Image: &mlflowv1.ImageConfig{
							Image:           ptr("custom/proxy:v1.0.0"),
							ImagePullPolicy: ptr(corev1.PullAlways),
						},
					},
				},
			},
			wantEnabled:    true,
			wantName:       "custom/proxy:v1.0.0",
			wantPullPolicy: "Always",
			wantSecretName: "mlflow-tls",
		},
		{
			name: "kube-rbac-proxy with custom TLS config",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					KubeRbacProxy: &mlflowv1.KubeRbacProxyConfig{
						Enabled: ptr(true),
						TLS: &mlflowv1.TLSConfig{
							SecretName: ptr("custom-tls"),
						},
					},
				},
			},
			wantEnabled:    true,
			wantPullPolicy: "", // pullPolicy should not be set when not explicitly provided
			wantSecretName: "custom-tls",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			values := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace")

			kubeRbacProxy, ok := values["kubeRbacProxy"].(map[string]interface{})
			if !ok {
				t.Fatal("kubeRbacProxy not found in values or wrong type")
			}

			if got := kubeRbacProxy["enabled"].(bool); got != tt.wantEnabled {
				t.Errorf("kubeRbacProxy.enabled = %v, want %v", got, tt.wantEnabled)
			}

			image, ok := kubeRbacProxy["image"].(map[string]interface{})
			if !ok {
				t.Fatal("kubeRbacProxy.image not found in values or wrong type")
			}

			if tt.wantName != "" {
				if got := image["name"].(string); got != tt.wantName {
					t.Errorf("kubeRbacProxy.image.name = %v, want %v", got, tt.wantName)
				}
			}

			if tt.wantPullPolicy != "" {
				if got, ok := image["imagePullPolicy"].(string); !ok || got != tt.wantPullPolicy {
					t.Errorf("kubeRbacProxy.image.imagePullPolicy = %v, want %v", got, tt.wantPullPolicy)
				}
			} else {
				if _, exists := image["imagePullPolicy"]; exists {
					t.Errorf("kubeRbacProxy.image.imagePullPolicy should not be set but found: %v", image["imagePullPolicy"])
				}
			}

			tls, ok := kubeRbacProxy["tls"].(map[string]interface{})
			if !ok {
				t.Fatal("kubeRbacProxy.tls not found in values or wrong type")
			}

			if got := tls["secretName"].(string); got != tt.wantSecretName {
				t.Errorf("kubeRbacProxy.tls.secretName = %v, want %v", got, tt.wantSecretName)
			}
		})
	}
}

// Helper function to create pointers
func ptr[T any](v T) *T {
	return &v
}

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
					Storage: &mlflowv1.StorageConfig{},
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
					Storage: &mlflowv1.StorageConfig{
						Size:             ptr(resource.MustParse("20Gi")),
						StorageClassName: ptr("fast-ssd"),
						AccessMode:       ptr(corev1.ReadWriteMany),
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

func TestMlflowToHelmValues_OpenShift(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name                   string
		mlflow                 *mlflowv1.MLflow
		wantServingCertEnabled bool
		wantSecretName         string
	}{
		{
			name: "openshift not configured - serving cert disabled by default",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			wantServingCertEnabled: false,
			wantSecretName:         "mlflow-tls",
		},
		{
			name: "openshift serving cert enabled",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					OpenShift: &mlflowv1.OpenShiftConfig{
						ServingCert: &mlflowv1.ServingCertConfig{
							Enabled: ptr(true),
						},
					},
				},
			},
			wantServingCertEnabled: true,
			wantSecretName:         "mlflow-tls",
		},
		{
			name: "openshift serving cert with custom secret name",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					OpenShift: &mlflowv1.OpenShiftConfig{
						ServingCert: &mlflowv1.ServingCertConfig{
							Enabled:    ptr(true),
							SecretName: ptr("custom-tls"),
						},
					},
				},
			},
			wantServingCertEnabled: true,
			wantSecretName:         "custom-tls",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			values := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace")

			openShift, ok := values["openShift"].(map[string]interface{})
			if !ok {
				t.Fatal("openShift not found in values or wrong type")
			}

			servingCert, ok := openShift["servingCert"].(map[string]interface{})
			if !ok {
				t.Fatal("openShift.servingCert not found or wrong type")
			}

			if got := servingCert["enabled"].(bool); got != tt.wantServingCertEnabled {
				t.Errorf("openShift.servingCert.enabled = %v, want %v", got, tt.wantServingCertEnabled)
			}

			if got := servingCert["secretName"].(string); got != tt.wantSecretName {
				t.Errorf("openShift.servingCert.secretName = %v, want %v", got, tt.wantSecretName)
			}
		})
	}
}

func TestMlflowToHelmValues_Image(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name           string
		mlflow         *mlflowv1.MLflow
		wantRepository string
		wantTag        string
		wantPullPolicy string
	}{
		{
			name: "image not configured - should use config defaults",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			// Will use values from config package
			wantPullPolicy: "Always",
		},
		{
			name: "image with custom values",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					Image: &mlflowv1.ImageConfig{
						Image:      ptr("custom/mlflow:v2.0.0"),
						PullPolicy: ptr(corev1.PullIfNotPresent),
					},
				},
			},
			wantRepository: "custom/mlflow",
			wantTag:        "v2.0.0",
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

			if tt.wantRepository != "" {
				if got := image["repository"].(string); got != tt.wantRepository {
					t.Errorf("image.repository = %v, want %v", got, tt.wantRepository)
				}
			}

			if tt.wantTag != "" {
				if got := image["tag"].(string); got != tt.wantTag {
					t.Errorf("image.tag = %v, want %v", got, tt.wantTag)
				}
			}

			if got := image["pullPolicy"].(string); got != tt.wantPullPolicy {
				t.Errorf("image.pullPolicy = %v, want %v", got, tt.wantPullPolicy)
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
		wantServeArtifacts       bool
		wantWorkers              int32
	}{
		{
			name: "mlflow config not set - should use defaults",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			wantBackendStoreURI:      defaultBackendStoreURI,
			wantRegistryStoreURI:     defaultRegistryStoreURI,
			wantArtifactsDestination: defaultArtifactsDest,
			wantServeArtifacts:       true,
			wantWorkers:              1,
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
			wantServeArtifacts:       true,
			wantWorkers:              1,
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
			wantRegistryStoreURI:     defaultRegistryStoreURI,
			wantArtifactsDestination: defaultArtifactsDest,
			wantServeArtifacts:       false,
			wantWorkers:              4,
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

			if got := mlflowConfig["serveArtifacts"].(bool); got != tt.wantServeArtifacts {
				t.Errorf("mlflow.serveArtifacts = %v, want %v", got, tt.wantServeArtifacts)
			}

			if got := mlflowConfig["workers"].(int32); got != tt.wantWorkers {
				t.Errorf("mlflow.workers = %v, want %v", got, tt.wantWorkers)
			}
		})
	}
}

func TestMlflowToHelmValues_Env(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name        string
		mlflow      *mlflowv1.MLflow
		wantMinEnvs int // Default envs (HOME)
		wantEnvName string
		wantEnvVal  string
	}{
		{
			name: "no custom env vars",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			wantMinEnvs: 1, // HOME only
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
			wantMinEnvs: 3, // 1 default + 2 custom
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
			wantMinEnvs: 2, // 1 default + 1 custom
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
		wantRequestsCPU    string
		wantRequestsMemory string
		wantLimitsCPU      string
		wantLimitsMemory   string
	}{
		{
			name: "resources not configured - should use defaults",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			wantRequestsCPU:    "250m",
			wantRequestsMemory: "512Mi",
			wantLimitsCPU:      "1",
			wantLimitsMemory:   "1Gi",
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
					if obj.GetKind() == "Deployment" {
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
					if obj.GetKind() == "Deployment" {
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
		name               string
		mlflow             *mlflowv1.MLflow
		wantEnabled        bool
		wantRepository     string
		wantTag            string
		wantPullPolicy     string
		wantSecretName     string
		wantUpstreamCAFile string
	}{
		{
			name: "kube-rbac-proxy with default config",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			wantEnabled:        false,
			wantPullPolicy:     "IfNotPresent",
			wantSecretName:     "mlflow-tls",
			wantUpstreamCAFile: "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt",
		},
		{
			name: "kube-rbac-proxy enabled with custom image",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					KubeRbacProxy: &mlflowv1.KubeRbacProxyConfig{
						Enabled: ptr(true),
						Image: &mlflowv1.ImageConfig{
							Image:      ptr("custom/proxy:v1.0.0"),
							PullPolicy: ptr(corev1.PullAlways),
						},
					},
				},
			},
			wantEnabled:        true,
			wantRepository:     "custom/proxy",
			wantTag:            "v1.0.0",
			wantPullPolicy:     "Always",
			wantSecretName:     "mlflow-tls",
			wantUpstreamCAFile: "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt",
		},
		{
			name: "kube-rbac-proxy with custom TLS config",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					KubeRbacProxy: &mlflowv1.KubeRbacProxyConfig{
						Enabled: ptr(true),
						TLS: &mlflowv1.TLSConfig{
							SecretName:     ptr("custom-tls"),
							UpstreamCAFile: ptr("/custom/ca.crt"),
						},
					},
				},
			},
			wantEnabled:        true,
			wantPullPolicy:     "IfNotPresent",
			wantSecretName:     "custom-tls",
			wantUpstreamCAFile: "/custom/ca.crt",
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

			if tt.wantRepository != "" {
				if got := image["repository"].(string); got != tt.wantRepository {
					t.Errorf("kubeRbacProxy.image.repository = %v, want %v", got, tt.wantRepository)
				}
			}

			if tt.wantTag != "" {
				if got := image["tag"].(string); got != tt.wantTag {
					t.Errorf("kubeRbacProxy.image.tag = %v, want %v", got, tt.wantTag)
				}
			}

			if got := image["pullPolicy"].(string); got != tt.wantPullPolicy {
				t.Errorf("kubeRbacProxy.image.pullPolicy = %v, want %v", got, tt.wantPullPolicy)
			}

			tls, ok := kubeRbacProxy["tls"].(map[string]interface{})
			if !ok {
				t.Fatal("kubeRbacProxy.tls not found in values or wrong type")
			}

			if got := tls["secretName"].(string); got != tt.wantSecretName {
				t.Errorf("kubeRbacProxy.tls.secretName = %v, want %v", got, tt.wantSecretName)
			}

			if got := tls["upstreamCAFile"].(string); got != tt.wantUpstreamCAFile {
				t.Errorf("kubeRbacProxy.tls.upstreamCAFile = %v, want %v", got, tt.wantUpstreamCAFile)
			}
		})
	}
}

func TestSplitImage(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name           string
		image          string
		wantRepository string
		wantTag        string
	}{
		{
			name:           "simple image with tag",
			image:          "nginx:1.19",
			wantRepository: "nginx",
			wantTag:        "1.19",
		},
		{
			name:           "image without tag defaults to latest",
			image:          "nginx",
			wantRepository: "nginx",
			wantTag:        "latest",
		},
		{
			name:           "image with registry and tag",
			image:          "quay.io/opendatahub/mlflow:latest",
			wantRepository: "quay.io/opendatahub/mlflow",
			wantTag:        "latest",
		},
		{
			name:           "image with port number in registry",
			image:          "registry.example.com:5000/myimage:v1.0",
			wantRepository: "registry.example.com:5000/myimage",
			wantTag:        "v1.0",
		},
		{
			name:           "digest-based reference with sha256",
			image:          "quay.io/opendatahub/mlflow@sha256:1234567890abcdef",
			wantRepository: "quay.io/opendatahub/mlflow",
			wantTag:        "sha256:1234567890abcdef",
		},
		{
			name:           "simple image with digest",
			image:          "nginx@sha256:abcdef123456",
			wantRepository: "nginx",
			wantTag:        "sha256:abcdef123456",
		},
		{
			name:           "registry with port and digest",
			image:          "registry.example.com:5000/myimage@sha256:fedcba654321",
			wantRepository: "registry.example.com:5000/myimage",
			wantTag:        "sha256:fedcba654321",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			repo, tag := renderer.splitImage(tt.image)

			if repo != tt.wantRepository {
				t.Errorf("splitImage(%q) repository = %v, want %v", tt.image, repo, tt.wantRepository)
			}
			if tag != tt.wantTag {
				t.Errorf("splitImage(%q) tag = %v, want %v", tt.image, tag, tt.wantTag)
			}
		})
	}
}

// Helper function to create pointers
func ptr[T any](v T) *T {
	return &v
}

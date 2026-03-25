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

	gomega "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

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
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
				},
			},
			wantResourcesSet: false,
		},
		{
			name: "resources with custom values",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
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
			g.Expect(err).NotTo(gomega.HaveOccurred())

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
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
				},
			},
			wantReplicas: 1,
		},
		{
			name: "replicas set to 3",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					Replicas:        ptr(int32(3)),
				},
			},
			wantReplicas: 3,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := gomega.NewWithT(t)

			values, err := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace", RenderOptions{})
			g.Expect(err).NotTo(gomega.HaveOccurred())

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
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr(testBackendStoreURI),
		},
	}

	testNamespace := "custom-namespace"
	values, err := renderer.mlflowToHelmValues(mlflow, testNamespace, RenderOptions{})
	g.Expect(err).NotTo(gomega.HaveOccurred())

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
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
				},
			}

			values, err := renderer.mlflowToHelmValues(mlflow, "test-namespace", RenderOptions{})
			g.Expect(err).NotTo(gomega.HaveOccurred())

			if got := values["resourceSuffix"].(string); got != tt.wantResourceSuffix {
				t.Errorf("resourceSuffix = %v, want %v", got, tt.wantResourceSuffix)
			}
		})
	}
}

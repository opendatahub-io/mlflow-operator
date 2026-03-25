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
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
				},
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
					BackendStoreURI: ptr(testBackendStoreURI),
					Storage:         &corev1.PersistentVolumeClaimSpec{},
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
					BackendStoreURI: ptr(testBackendStoreURI),
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
			g.Expect(err).NotTo(gomega.HaveOccurred())

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

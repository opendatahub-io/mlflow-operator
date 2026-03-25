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
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

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
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
				},
			},
			// pullPolicy should not be set when not explicitly provided
			wantPullPolicy: "",
		},
		{
			name: "image with custom values",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
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
			g.Expect(err).NotTo(gomega.HaveOccurred())

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

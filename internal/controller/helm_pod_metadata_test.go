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
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

func TestMlflowToHelmValues_PodAnnotations(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name            string
		podAnnotations  map[string]string
		wantExists      bool
		wantAnnotations map[string]string
	}{
		{
			name:       "no annotations - key should not exist",
			wantExists: false,
		},
		{
			name:            "single annotation",
			podAnnotations:  map[string]string{"prometheus.io/scrape": "true"},
			wantExists:      true,
			wantAnnotations: map[string]string{"prometheus.io/scrape": "true"},
		},
		{
			name: "multiple annotations",
			podAnnotations: map[string]string{
				"prometheus.io/scrape":    "true",
				"prometheus.io/port":      "8443",
				"sidecar.istio.io/inject": "false",
			},
			wantExists: true,
			wantAnnotations: map[string]string{
				"prometheus.io/scrape":    "true",
				"prometheus.io/port":      "8443",
				"sidecar.istio.io/inject": "false",
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := gomega.NewWithT(t)

			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					PodAnnotations:  tt.podAnnotations,
				},
			}

			values, err := renderer.mlflowToHelmValues(mlflow, "test-namespace", RenderOptions{})
			g.Expect(err).NotTo(gomega.HaveOccurred())

			podAnnotations, exists := values["podAnnotations"]
			if !tt.wantExists {
				if exists {
					t.Error("podAnnotations should not exist when no annotations are configured")
				}
				return
			}

			if !exists {
				t.Fatal("podAnnotations not found in values")
			}

			annotationsMap, ok := podAnnotations.(map[string]interface{})
			if !ok {
				t.Fatal("podAnnotations is not a map[string]interface{}")
			}

			if len(annotationsMap) != len(tt.wantAnnotations) {
				t.Errorf("podAnnotations length = %d, want %d", len(annotationsMap), len(tt.wantAnnotations))
			}

			for k, wantV := range tt.wantAnnotations {
				gotV, ok := annotationsMap[k]
				if !ok {
					t.Errorf("podAnnotations missing key %q", k)
					continue
				}
				if gotV != wantV {
					t.Errorf("podAnnotations[%q] = %v, want %v", k, gotV, wantV)
				}
			}
		})
	}
}

func TestRenderChart_PodAnnotations(t *testing.T) {
	g := gomega.NewWithT(t)
	renderer := NewHelmRenderer("../../charts/mlflow")

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr(testBackendStoreURI),
			PodAnnotations: map[string]string{
				"prometheus.io/scrape": "true",
				"prometheus.io/port":   "8443",
			},
		},
	}

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{})
	g.Expect(err).NotTo(gomega.HaveOccurred())

	deployment := findObject(objs, deploymentKind, "mlflow")
	g.Expect(deployment).NotTo(gomega.BeNil(), "Deployment should be rendered")

	annotations, found, err := unstructured.NestedStringMap(deployment.Object, "spec", "template", "metadata", "annotations")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue(), "Pod annotations should be rendered")
	g.Expect(annotations).To(gomega.HaveKeyWithValue("prometheus.io/scrape", "true"))
	g.Expect(annotations).To(gomega.HaveKeyWithValue("prometheus.io/port", "8443"))
}

func TestMlflowToHelmValues_PodLabels(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name       string
		podLabels  map[string]string
		wantExists bool
		wantLabels map[string]string
	}{
		{
			name:       "no labels - key should not exist",
			wantExists: false,
		},
		{
			name:       "single label",
			podLabels:  map[string]string{"team": "ml-platform"},
			wantExists: true,
			wantLabels: map[string]string{"team": "ml-platform"},
		},
		{
			name: "multiple labels",
			podLabels: map[string]string{
				"team":        "ml-platform",
				"environment": "production",
				"cost-center": "ai-ops",
			},
			wantExists: true,
			wantLabels: map[string]string{
				"team":        "ml-platform",
				"environment": "production",
				"cost-center": "ai-ops",
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := gomega.NewWithT(t)

			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					PodLabels:       tt.podLabels,
				},
			}

			values, err := renderer.mlflowToHelmValues(mlflow, "test-namespace", RenderOptions{})
			g.Expect(err).NotTo(gomega.HaveOccurred())

			podLabels, exists := values["podLabels"]
			if !tt.wantExists {
				if exists {
					t.Error("podLabels should not exist when no labels are configured")
				}
				return
			}

			if !exists {
				t.Fatal("podLabels not found in values")
			}

			labelsMap, ok := podLabels.(map[string]interface{})
			if !ok {
				t.Fatal("podLabels is not a map[string]interface{}")
			}

			if len(labelsMap) != len(tt.wantLabels) {
				t.Errorf("podLabels length = %d, want %d", len(labelsMap), len(tt.wantLabels))
			}

			for k, wantV := range tt.wantLabels {
				gotV, ok := labelsMap[k]
				if !ok {
					t.Errorf("podLabels missing key %q", k)
					continue
				}
				if gotV != wantV {
					t.Errorf("podLabels[%q] = %v, want %v", k, gotV, wantV)
				}
			}
		})
	}
}

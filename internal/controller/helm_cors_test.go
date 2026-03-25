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
	"strings"
	"testing"

	gomega "github.com/onsi/gomega"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	"github.com/opendatahub-io/mlflow-operator/internal/config"
)

func TestBuildCORSAllowedOrigins(t *testing.T) {
	tests := []struct {
		name           string
		mlflow         *mlflowv1.MLflow
		namespace      string
		mlflowURL      string
		wantContains   []string
		wantNotContain []string
	}{
		{
			name: "default CR name produces correct service origins",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			namespace: "opendatahub",
			mlflowURL: "https://gateway.example.com",
			wantContains: []string{
				"https://mlflow:8443",
				"https://mlflow.opendatahub.svc:8443",
				"https://mlflow.opendatahub.svc.cluster.local:8443",
				"https://gateway.example.com",
				"localhost:*",
				"127.0.0.1:*",
			},
		},
		{
			name: "custom CR name produces suffixed service origins",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "dev"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			namespace: "test-ns",
			mlflowURL: "https://gateway.example.com",
			wantContains: []string{
				"https://mlflow-dev:8443",
				"https://mlflow-dev.test-ns.svc:8443",
				"https://mlflow-dev.test-ns.svc.cluster.local:8443",
			},
			wantNotContain: []string{
				"https://mlflow:8443",
			},
		},
		{
			name: "gateway URL with port preserves port in origin",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			namespace: "opendatahub",
			mlflowURL: "https://gateway.example.com:9443/mlflow",
			wantContains: []string{
				"https://gateway.example.com:9443",
			},
			wantNotContain: []string{
				"https://gateway.example.com:9443/mlflow",
			},
		},
		{
			name: "empty gateway URL omits gateway origin",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			namespace: "opendatahub",
			mlflowURL: "",
			wantContains: []string{
				"https://mlflow:8443",
				"localhost:*",
			},
			wantNotContain: []string{
				"example.com",
			},
		},
		{
			name: "extraAllowedOrigins are appended",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					ExtraAllowedOrigins: []string{
						"https://my-app.example.com",
						"https://jupyter.example.com:8888",
					},
				},
			},
			namespace: "opendatahub",
			mlflowURL: "https://gateway.example.com",
			wantContains: []string{
				"https://mlflow:8443",
				"https://gateway.example.com",
				"https://my-app.example.com",
				"https://jupyter.example.com:8888",
			},
		},
		{
			name: "empty and comma-containing extraAllowedOrigins are skipped",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					ExtraAllowedOrigins: []string{
						"",
						"  ",
						"https://a.com,https://b.com",
						"https://valid.example.com",
					},
				},
			},
			namespace: "opendatahub",
			mlflowURL: "https://gateway.example.com",
			wantContains: []string{
				"https://valid.example.com",
			},
			wantNotContain: []string{
				"https://a.com",
				"https://b.com",
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := gomega.NewWithT(t)

			cfg := &config.OperatorConfig{
				MLflowURL: tt.mlflowURL,
			}

			result := buildCORSAllowedOrigins(tt.mlflow, tt.namespace, cfg)
			origins := strings.Split(result, ",")
			originSet := make(map[string]struct{}, len(origins))
			for _, o := range origins {
				originSet[o] = struct{}{}
			}

			for _, want := range tt.wantContains {
				_, ok := originSet[want]
				g.Expect(ok).To(gomega.BeTrue(), "missing origin %q in %q", want, result)
			}
			for _, notWant := range tt.wantNotContain {
				_, ok := originSet[notWant]
				g.Expect(ok).To(gomega.BeFalse(), "unexpected origin %q in %q", notWant, result)
			}
		})
	}
}

func TestMlflowToHelmValues_CORSAllowedOrigins(t *testing.T) {
	g := gomega.NewWithT(t)
	renderer := &HelmRenderer{}

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr(testBackendStoreURI),
		},
	}

	values, err := renderer.mlflowToHelmValues(mlflow, "test-namespace", RenderOptions{})
	g.Expect(err).NotTo(gomega.HaveOccurred())

	mlflowConfig, ok := values["mlflow"].(map[string]interface{})
	g.Expect(ok).To(gomega.BeTrue(), "mlflow config should be a map")

	corsOrigins, ok := mlflowConfig["corsAllowedOrigins"].(string)
	g.Expect(ok).To(gomega.BeTrue(), "corsAllowedOrigins should be a string")
	g.Expect(corsOrigins).NotTo(gomega.BeEmpty())
	g.Expect(corsOrigins).To(gomega.ContainSubstring("https://mlflow:8443"))
	g.Expect(corsOrigins).To(gomega.ContainSubstring("https://mlflow.test-namespace.svc:8443"))
	g.Expect(corsOrigins).To(gomega.ContainSubstring("https://mlflow.test-namespace.svc.cluster.local:8443"))
	g.Expect(corsOrigins).To(gomega.ContainSubstring("localhost:*"))
	g.Expect(corsOrigins).To(gomega.ContainSubstring("127.0.0.1:*"))
}

func TestRenderChart_CORSEnvVar(t *testing.T) {
	g := gomega.NewWithT(t)
	renderer := NewHelmRenderer("../../charts/mlflow")

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr(testBackendStoreURI),
		},
	}

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{})
	g.Expect(err).NotTo(gomega.HaveOccurred())

	var deployment *unstructured.Unstructured
	for _, obj := range objs {
		if obj.GetKind() == deploymentKind {
			deployment = obj
			break
		}
	}
	g.Expect(deployment).NotTo(gomega.BeNil())

	containers, found, err := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "containers")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue())

	var mlflowContainer map[string]interface{}
	for _, c := range containers {
		cm := c.(map[string]interface{})
		if cm["name"] == "mlflow" {
			mlflowContainer = cm
			break
		}
	}
	g.Expect(mlflowContainer).NotTo(gomega.BeNil(), "mlflow container not found")
	envList := mlflowContainer["env"].([]interface{})

	var corsEnvValue string
	for _, e := range envList {
		envMap := e.(map[string]interface{})
		if envMap["name"] == "MLFLOW_SERVER_CORS_ALLOWED_ORIGINS" {
			corsEnvValue = envMap["value"].(string)
			break
		}
	}

	g.Expect(corsEnvValue).NotTo(gomega.BeEmpty(), "MLFLOW_SERVER_CORS_ALLOWED_ORIGINS env var should be set")
	g.Expect(corsEnvValue).To(gomega.ContainSubstring("https://mlflow:8443"))
	g.Expect(corsEnvValue).To(gomega.ContainSubstring("https://mlflow.test-ns.svc:8443"))
	g.Expect(corsEnvValue).To(gomega.ContainSubstring("localhost:*"))
}

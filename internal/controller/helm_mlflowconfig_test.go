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
			name: "mlflow config with explicit backend store",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
				},
			},
			wantBackendStoreURI:      testBackendStoreURI,
			wantRegistryStoreURI:     testBackendStoreURI, // Registry defaults to backend
			wantArtifactsDestination: defaultArtifactsDest,
			wantDefaultArtifactRoot:  "", // Empty - let MLflow use its intelligent defaults
			wantServeArtifacts:       false,
			wantWorkers:              1,
			wantBackendSecretRef:     false,
			wantRegistrySecretRef:    false,
		},
		{
			name: "legacy CR without backend store fields uses implicit sqlite fallback",
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
					BackendStoreURI: ptr(testBackendStoreURI),
					ServeArtifacts:  ptr(false),
					Workers:         ptr(int32(4)),
				},
			},
			wantBackendStoreURI:      testBackendStoreURI,
			wantRegistryStoreURI:     testBackendStoreURI, // Registry defaults to backend
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
			wantBackendStoreURI:      "",
			wantRegistryStoreURI:     "",
			wantArtifactsDestination: defaultArtifactsDest,
			wantDefaultArtifactRoot:  "", // Empty - let MLflow use its intelligent defaults
			wantServeArtifacts:       false,
			wantWorkers:              1,
			wantBackendSecretRef:     true,
			wantRegistrySecretRef:    true,
		},
		{
			name: "defense-in-depth: secret reference wins when both are set",
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
			wantBackendStoreURI:      "",
			wantRegistryStoreURI:     "",
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
					BackendStoreURI:      ptr(testBackendStoreURI),
					ArtifactsDestination: ptr("s3://bucket/artifacts"),
					DefaultArtifactRoot:  ptr("s3://bucket/custom-root"),
				},
			},
			wantBackendStoreURI:      testBackendStoreURI,
			wantRegistryStoreURI:     testBackendStoreURI, // Registry defaults to backend
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
			g.Expect(err).NotTo(gomega.HaveOccurred())

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

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
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

func TestInjectMigrationInitContainer_Basic(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec:       mlflowv1.MLflowSpec{},
	}

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{})
	if err != nil {
		t.Fatalf("RenderChart() error = %v", err)
	}

	// Inject the migration init container
	if err := injectMigrationInitContainer(objs); err != nil {
		t.Fatalf("injectMigrationInitContainer() error = %v", err)
	}

	deployment := findDeployment(t, objs)

	initContainers, _, _ := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "initContainers")
	if len(initContainers) != 1 {
		t.Fatalf("expected 1 init container, got %d", len(initContainers))
	}

	initContainer := initContainers[0].(map[string]interface{})

	// Verify name
	if initContainer["name"].(string) != "db-migration" {
		t.Errorf("init container name = %v, want db-migration", initContainer["name"])
	}

	// Verify command
	command, _ := initContainer["command"].([]interface{})
	if len(command) != 1 || command[0].(string) != "mlflow" {
		t.Errorf("init container command = %v, want [mlflow]", command)
	}

	// Verify args
	args, _ := initContainer["args"].([]interface{})
	if len(args) != 2 || args[0].(string) != "db" || args[1].(string) != "fix-migration-gap" {
		t.Errorf("init container args = %v, want [db fix-migration-gap]", args)
	}

	// Verify it has MLFLOW_BACKEND_STORE_URI env var
	envVars, _, _ := unstructured.NestedSlice(initContainer, "env")
	foundBackendURI := false
	for _, env := range envVars {
		envMap := env.(map[string]interface{})
		if envMap["name"].(string) == "MLFLOW_BACKEND_STORE_URI" {
			foundBackendURI = true
		}
	}
	if !foundBackendURI {
		t.Error("MLFLOW_BACKEND_STORE_URI env var not found in init container")
	}

	// Verify it uses the same image as the main container
	containers, _, _ := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "containers")
	mainContainer := containers[0].(map[string]interface{})
	if initContainer["image"] != mainContainer["image"] {
		t.Errorf("init container image = %v, want %v", initContainer["image"], mainContainer["image"])
	}

	// Verify security context is copied
	if _, found, _ := unstructured.NestedMap(initContainer, "securityContext"); !found {
		t.Error("security context not found on init container")
	}

	// Verify resources are set
	if _, found, _ := unstructured.NestedMap(initContainer, "resources"); !found {
		t.Error("resources not found on init container")
	}
}

func TestInjectMigrationInitContainer_WithCABundle(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec: mlflowv1.MLflowSpec{
			CABundleConfigMap: &mlflowv1.CABundleConfigMapSpec{
				Name: "custom-ca",
			},
		},
	}

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{PlatformTrustedCABundleExists: true})
	if err != nil {
		t.Fatalf("RenderChart() error = %v", err)
	}

	if err := injectMigrationInitContainer(objs); err != nil {
		t.Fatalf("injectMigrationInitContainer() error = %v", err)
	}

	deployment := findDeployment(t, objs)

	initContainers, _, _ := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "initContainers")

	// Should have 2 init containers: combine-ca-bundles + db-migration
	if len(initContainers) != 2 {
		t.Fatalf("expected 2 init containers, got %d", len(initContainers))
	}

	// First should be combine-ca-bundles (from chart)
	first := initContainers[0].(map[string]interface{})
	if first["name"].(string) != "combine-ca-bundles" {
		t.Errorf("first init container name = %v, want combine-ca-bundles", first["name"])
	}

	// Second should be db-migration (injected)
	second := initContainers[1].(map[string]interface{})
	if second["name"].(string) != "db-migration" {
		t.Errorf("second init container name = %v, want db-migration", second["name"])
	}

	// Verify db-migration has CA bundle env vars
	envVars, _, _ := unstructured.NestedSlice(second, "env")
	foundEnvVars := make(map[string]bool)
	for _, env := range envVars {
		envMap := env.(map[string]interface{})
		foundEnvVars[envMap["name"].(string)] = true
	}

	for _, required := range []string{"MLFLOW_BACKEND_STORE_URI", "SSL_CERT_FILE", "PGSSLROOTCERT", "PGSSLMODE"} {
		if !foundEnvVars[required] {
			t.Errorf("expected env var %s not found in db-migration init container", required)
		}
	}

	// Verify db-migration has combined-ca-bundle volume mount
	volumeMounts, _, _ := unstructured.NestedSlice(second, "volumeMounts")
	foundCAMount := false
	for _, vm := range volumeMounts {
		vmMap := vm.(map[string]interface{})
		if vmMap["name"].(string) == "combined-ca-bundle" {
			foundCAMount = true
		}
	}
	if !foundCAMount {
		t.Error("combined-ca-bundle volume mount not found on db-migration init container")
	}
}

func TestInjectMigrationInitContainer_WithSecretRef(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")

	optional := false
	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURIFrom: &corev1.SecretKeySelector{
				LocalObjectReference: corev1.LocalObjectReference{
					Name: "db-credentials",
				},
				Key:      "uri",
				Optional: &optional,
			},
		},
	}

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{})
	if err != nil {
		t.Fatalf("RenderChart() error = %v", err)
	}

	if err := injectMigrationInitContainer(objs); err != nil {
		t.Fatalf("injectMigrationInitContainer() error = %v", err)
	}

	deployment := findDeployment(t, objs)

	initContainers, _, _ := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "initContainers")
	if len(initContainers) != 1 {
		t.Fatalf("expected 1 init container, got %d", len(initContainers))
	}

	initContainer := initContainers[0].(map[string]interface{})

	// Verify MLFLOW_BACKEND_STORE_URI uses valueFrom (secret reference)
	envVars, _, _ := unstructured.NestedSlice(initContainer, "env")
	for _, env := range envVars {
		envMap := env.(map[string]interface{})
		if envMap["name"].(string) == "MLFLOW_BACKEND_STORE_URI" {
			if _, hasValueFrom := envMap["valueFrom"]; !hasValueFrom {
				t.Error("MLFLOW_BACKEND_STORE_URI should use valueFrom (secret ref), not a direct value")
			}
			return
		}
	}
	t.Error("MLFLOW_BACKEND_STORE_URI env var not found")
}

// findDeployment finds the Deployment object in the rendered objects.
func findDeployment(t *testing.T, objs []*unstructured.Unstructured) *unstructured.Unstructured {
	t.Helper()
	for _, obj := range objs {
		if obj.GetKind() == deploymentKind {
			return obj
		}
	}
	t.Fatal("Deployment not found in rendered objects")
	return nil
}

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

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

func TestMlflowToHelmValues_CABundle(t *testing.T) {
	renderer := &HelmRenderer{}

	// Test: no CA bundles configured
	values, err := renderer.mlflowToHelmValues(&mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr(testBackendStoreURI),
		},
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
			BackendStoreURI:   ptr(testBackendStoreURI),
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
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr(testBackendStoreURI),
		},
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
			BackendStoreURI:   ptr(testBackendStoreURI),
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
			BackendStoreURI:   ptr(testBackendStoreURI),
			CABundleConfigMap: &mlflowv1.CABundleConfigMapSpec{Name: "my-ca"},
		},
	}

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{PlatformTrustedCABundleExists: true})
	if err != nil {
		t.Fatalf("RenderChart() error = %v", err)
	}

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
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr(testBackendStoreURI),
		},
	}

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{PlatformTrustedCABundleExists: true})
	if err != nil {
		t.Fatalf("RenderChart() error = %v", err)
	}

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
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr(testBackendStoreURI),
		},
	}

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{PlatformTrustedCABundleExists: false})
	if err != nil {
		t.Fatalf("RenderChart() error = %v", err)
	}

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

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
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

func TestMlflowToHelmValues_StaticPrefix(t *testing.T) {
	g := gomega.NewWithT(t)

	renderer := &HelmRenderer{}

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "test"},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr(testBackendStoreURI),
		},
	}

	values, err := renderer.mlflowToHelmValues(mlflow, "test-namespace", RenderOptions{})
	g.Expect(err).NotTo(gomega.HaveOccurred())

	mlflowConfig, ok := values["mlflow"].(map[string]interface{})
	if !ok {
		t.Fatal("mlflow config not found in values or wrong type")
	}

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
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
				},
			},
			wantMinEnvs: 0, // No env vars when none are specified
		},
		{
			name: "with custom env vars",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
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
					BackendStoreURI: ptr(testBackendStoreURI),
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
			g := gomega.NewWithT(t)

			values, err := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace", RenderOptions{})
			g.Expect(err).NotTo(gomega.HaveOccurred())

			env, ok := values["env"].([]any)
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
					envMap := e.(map[string]any)
					if envMap["name"] == tt.wantEnvName {
						found = true
						if envMap["value"] != tt.wantEnvVal {
							t.Errorf("env[%s] = %v, want %v", tt.wantEnvName, envMap["value"], tt.wantEnvVal)
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

func TestMlflowToHelmValues_OpenShiftInjectsUvicornSSLCiphersEnv(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name         string
		mlflow       *mlflowv1.MLflow
		wantMinCount int
		wantValue    string
	}{
		{
			name: "injects env when absent",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec:       mlflowv1.MLflowSpec{},
			},
			wantMinCount: 1,
			wantValue:    uvicornSystemCiphers,
		},
		{
			name: "preserves user supplied value without duplicates",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					Env: []corev1.EnvVar{
						{Name: "CUSTOM_VAR", Value: "custom-value"},
						{Name: uvicornSSLCiphersEnv, Value: "DEFAULT"},
					},
				},
			},
			wantMinCount: 2,
			wantValue:    "DEFAULT",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := gomega.NewWithT(t)

			values, err := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace", RenderOptions{IsOpenShift: true})
			g.Expect(err).NotTo(gomega.HaveOccurred())

			env, ok := values["env"].([]any)
			if !ok {
				t.Fatal("env not found in values or wrong type")
			}

			if len(env) < tt.wantMinCount {
				t.Fatalf("env length = %v, want at least %v", len(env), tt.wantMinCount)
			}

			foundCount := 0
			for _, e := range env {
				envMap := e.(map[string]any)
				if envMap["name"] == uvicornSSLCiphersEnv {
					foundCount++
					if envMap["value"] != tt.wantValue {
						t.Errorf("%s = %v, want %v", uvicornSSLCiphersEnv, envMap["value"], tt.wantValue)
					}
				}
			}

			if foundCount != 1 {
				t.Errorf("found %d %s env vars, want 1", foundCount, uvicornSSLCiphersEnv)
			}
		})
	}
}

func TestMlflowToHelmValues_NonOpenShiftDoesNotInjectUvicornSSLCiphersEnv(t *testing.T) {
	renderer := &HelmRenderer{}
	g := gomega.NewWithT(t)

	values, err := renderer.mlflowToHelmValues(&mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "test"},
		Spec: mlflowv1.MLflowSpec{
			Env: []corev1.EnvVar{
				{Name: "CUSTOM_VAR", Value: "custom-value"},
			},
		},
	}, "test-namespace", RenderOptions{IsOpenShift: false})
	g.Expect(err).NotTo(gomega.HaveOccurred())

	env, ok := values["env"].([]any)
	if !ok {
		t.Fatal("env not found in values or wrong type")
	}

	foundUvicornSSLCiphers := false
	foundCustomVar := false
	for _, e := range env {
		envMap := e.(map[string]any)
		switch envMap["name"] {
		case uvicornSSLCiphersEnv:
			foundUvicornSSLCiphers = true
		case "CUSTOM_VAR":
			foundCustomVar = true
		}
	}

	if foundUvicornSSLCiphers {
		t.Fatalf("did not expect %s to be injected on non-OpenShift renders", uvicornSSLCiphersEnv)
	}
	if !foundCustomVar {
		t.Fatal("expected CUSTOM_VAR to be preserved in env")
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
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
				},
			},
			wantEnvFromCount: 0,
		},
		{
			name: "with secret and configmap envFrom",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
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
			g := gomega.NewWithT(t)

			values, err := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace", RenderOptions{})
			g.Expect(err).NotTo(gomega.HaveOccurred())

			if tt.wantEnvFromCount == 0 {
				if _, exists := values["envFrom"]; exists {
					t.Error("envFrom should not exist when no envFrom is configured")
				}
				return
			}

			envFrom, ok := values["envFrom"].([]any)
			if !ok {
				t.Fatal("envFrom not found in values or wrong type")
			}

			if len(envFrom) != tt.wantEnvFromCount {
				t.Errorf("envFrom length = %v, want %v", len(envFrom), tt.wantEnvFromCount)
			}
		})
	}
}

// TestRenderChart_EnvVars tests that env vars with both value and valueFrom are rendered correctly.
func TestRenderChart_EnvVars(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "test-mlflow"},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr(testBackendStoreURI),
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

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{})
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
		t.Fatal("Deployment not found in rendered objects")
	}

	containers, found, err := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "containers")
	if err != nil || !found || len(containers) == 0 {
		t.Fatalf("Failed to get containers from deployment: found=%v, err=%v", found, err)
	}

	var mlflowContainer map[string]interface{}
	for _, c := range containers {
		container := c.(map[string]interface{})
		if container["name"] == ResourceName {
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

func TestRenderChart_WorkspaceLabelSelectorEnvVar(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")

	labelSelector := &metav1.LabelSelector{
		MatchLabels: map[string]string{
			"mlflow-enabled": "true",
			"team":           "a",
		},
		MatchExpressions: []metav1.LabelSelectorRequirement{
			{
				Key:      "environment",
				Operator: metav1.LabelSelectorOpIn,
				Values:   []string{"prod", "staging"},
			},
		},
	}

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "test-mlflow"},
		Spec: mlflowv1.MLflowSpec{
			WorkspaceLabelSelector: labelSelector,
		},
	}

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{})
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
		t.Fatal("Deployment not found in rendered objects")
	}

	containers, found, err := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "containers")
	if err != nil || !found || len(containers) == 0 {
		t.Fatalf("Failed to get containers from deployment: found=%v, err=%v", found, err)
	}

	var mlflowContainer map[string]any
	for _, c := range containers {
		container := c.(map[string]any)
		if container["name"] == ResourceName {
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

	var got string
	for _, e := range env {
		envVar := e.(map[string]any)
		if envVar["name"] == "MLFLOW_K8S_WORKSPACE_LABEL_SELECTOR" {
			got, _ = envVar["value"].(string)
			break
		}
	}

	wantSubstrings := []string{
		"environment in (prod,staging)",
		"mlflow-enabled=true",
		"team=a",
	}
	for _, want := range wantSubstrings {
		if !strings.Contains(got, want) {
			t.Fatalf("MLFLOW_K8S_WORKSPACE_LABEL_SELECTOR=%q missing expected substring %q", got, want)
		}
	}
}

func TestRenderChart_WorkspaceLabelSelectorNilOmitsEnvVar(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "test-mlflow"},
		Spec:       mlflowv1.MLflowSpec{},
	}

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{})
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
		t.Fatal("Deployment not found in rendered objects")
	}

	containers, found, err := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "containers")
	if err != nil || !found || len(containers) == 0 {
		t.Fatalf("Failed to get containers: found=%v, err=%v", found, err)
	}

	var mlflowContainer map[string]any
	for _, c := range containers {
		container := c.(map[string]any)
		if container["name"] == ResourceName {
			mlflowContainer = container
			break
		}
	}
	if mlflowContainer == nil {
		t.Fatal("MLflow container not found")
	}

	env, found, err := unstructured.NestedSlice(mlflowContainer, "env")
	if err != nil || !found {
		t.Fatalf("Failed to get env: found=%v, err=%v", found, err)
	}

	for _, e := range env {
		envVar := e.(map[string]any)
		if envVar["name"] == "MLFLOW_K8S_WORKSPACE_LABEL_SELECTOR" {
			t.Fatal("MLFLOW_K8S_WORKSPACE_LABEL_SELECTOR should not be present when WorkspaceLabelSelector is nil")
		}
	}
}

func TestRenderChart_WorkspaceLabelSelectorEmptyOmitsEnvVar(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "test-mlflow"},
		Spec: mlflowv1.MLflowSpec{
			WorkspaceLabelSelector: &metav1.LabelSelector{},
		},
	}

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{})
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
		t.Fatal("Deployment not found in rendered objects")
	}

	containers, found, err := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "containers")
	if err != nil || !found || len(containers) == 0 {
		t.Fatalf("Failed to get containers: found=%v, err=%v", found, err)
	}

	var mlflowContainer map[string]any
	for _, c := range containers {
		container := c.(map[string]any)
		if container["name"] == ResourceName {
			mlflowContainer = container
			break
		}
	}
	if mlflowContainer == nil {
		t.Fatal("MLflow container not found")
	}

	env, found, err := unstructured.NestedSlice(mlflowContainer, "env")
	if err != nil || !found {
		t.Fatalf("Failed to get env: found=%v, err=%v", found, err)
	}

	for _, e := range env {
		envVar := e.(map[string]any)
		if envVar["name"] == "MLFLOW_K8S_WORKSPACE_LABEL_SELECTOR" {
			t.Fatal("MLFLOW_K8S_WORKSPACE_LABEL_SELECTOR should not be present when WorkspaceLabelSelector is empty")
		}
	}
}

func TestRenderChart_WorkspaceLabelSelectorInvalidOperatorReturnsError(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "test-mlflow"},
		Spec: mlflowv1.MLflowSpec{
			WorkspaceLabelSelector: &metav1.LabelSelector{
				MatchExpressions: []metav1.LabelSelectorRequirement{
					{
						Key:      "env",
						Operator: metav1.LabelSelectorOperator("InvalidOp"),
						Values:   []string{"prod"},
					},
				},
			},
		},
	}

	_, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{})
	if err == nil {
		t.Fatal("expected RenderChart to return an error for invalid label selector operator")
	}
	if !strings.Contains(err.Error(), "workspaceLabelSelector") {
		t.Fatalf("error should mention workspaceLabelSelector, got: %v", err)
	}
}

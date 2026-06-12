/*
Copyright 2026.

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
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

func TestMlflowToHelmValues_TraceArchival(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name        string
		mlflow      *mlflowv1.MLflow
		wantEnabled bool
		wantValues  map[string]interface{}
	}{
		{
			name: "trace archival not configured - should be disabled",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
				},
			},
			wantEnabled: false,
		},
		{
			name: "trace archival enabled with schedule and defaults",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					TraceArchival: &mlflowv1.TraceArchivalSpec{
						Enabled:  true,
						Schedule: ptr("*/5 * * * *"),
					},
				},
			},
			wantEnabled: true,
			wantValues: map[string]interface{}{
				"schedule": "*/5 * * * *",
			},
		},
		{
			name: "trace archival enabled with all fields",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					TraceArchival: &mlflowv1.TraceArchivalSpec{
						Enabled:          true,
						Schedule:         ptr("0 */6 * * *"),
						Location:         ptr("s3://mlflow-trace-archive"),
						Retention:        ptr("14d"),
						MaxTracesPerPass: ptr(int32(500)),
						LongRetentionAllowlist: []string{
							"123",
							"456",
						},
					},
				},
			},
			wantEnabled: true,
			wantValues: map[string]interface{}{
				"schedule":  "0 */6 * * *",
				"location":  "s3://mlflow-trace-archive",
				"retention": "14d",
			},
		},
		{
			name: "trace archival explicitly disabled",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					TraceArchival: &mlflowv1.TraceArchivalSpec{
						Enabled:  false,
						Schedule: ptr("*/5 * * * *"),
					},
				},
			},
			wantEnabled: false,
		},
		{
			name: "trace archival with custom resources",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					TraceArchival: &mlflowv1.TraceArchivalSpec{
						Enabled:  true,
						Schedule: ptr("*/10 * * * *"),
						Resources: &corev1.ResourceRequirements{
							Requests: corev1.ResourceList{
								corev1.ResourceCPU:    resource.MustParse("200m"),
								corev1.ResourceMemory: resource.MustParse("512Mi"),
							},
						},
					},
				},
			},
			wantEnabled: true,
			wantValues: map[string]interface{}{
				"schedule": "*/10 * * * *",
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := gomega.NewWithT(t)

			values, err := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace", RenderOptions{}, nil)
			g.Expect(err).NotTo(gomega.HaveOccurred())

			ta, ok := values["traceArchival"].(map[string]interface{})
			if !ok {
				t.Fatal("traceArchival not found in values or wrong type")
			}

			if got := ta["enabled"].(bool); got != tt.wantEnabled {
				t.Errorf("traceArchival.enabled = %v, want %v", got, tt.wantEnabled)
			}

			if tt.wantValues != nil {
				for key, want := range tt.wantValues {
					if got, exists := ta[key]; !exists {
						t.Errorf("traceArchival.%s not found", key)
					} else if got != want {
						t.Errorf("traceArchival.%s = %v (%T), want %v (%T)", key, got, got, want, want)
					}
				}
			}

			if tt.wantEnabled {
				spec := tt.mlflow.Spec.TraceArchival
				if spec != nil && len(spec.LongRetentionAllowlist) > 0 {
					allowlist, exists := ta["longRetentionAllowlist"]
					if !exists {
						t.Error("traceArchival.longRetentionAllowlist not found")
					} else {
						list := allowlist.([]interface{})
						if len(list) != len(spec.LongRetentionAllowlist) {
							t.Errorf("longRetentionAllowlist length = %d, want %d",
								len(list), len(spec.LongRetentionAllowlist))
						}
					}
				}
				if spec != nil && spec.Resources != nil {
					if _, exists := ta["resources"]; !exists {
						t.Error("traceArchival.resources not found when resources are specified")
					}
				}
			}
		})
	}
}

func TestRenderChart_TraceArchival(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")

	tests := []struct {
		name         string
		mlflow       *mlflowv1.MLflow
		namespace    string
		wantErr      bool
		validateObjs func(t *testing.T, objs []*unstructured.Unstructured)
	}{
		{
			name: "archival disabled - no CronJob, ConfigMap, or RBAC rendered",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
				},
			},
			namespace: "test-ns",
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				if findObject(objs, "CronJob", "mlflow-trace-archival") != nil {
					t.Error("trace archival CronJob should not be rendered when archival is disabled")
				}
				if findObject(objs, "ConfigMap", "mlflow-trace-archival-config") != nil {
					t.Error("trace archival ConfigMap should not be rendered when archival is disabled")
				}
				if findObject(objs, "ServiceAccount", TraceArchivalServiceAccountName) != nil {
					t.Error("trace archival ServiceAccount should not be rendered when archival is disabled")
				}

				dep := findObject(objs, deploymentKind, "mlflow")
				if dep == nil {
					t.Fatal("Deployment not found")
				}
				assertJobExecutionEnv(t, dep, "false")
			},
		},
		{
			name: "archival enabled - CronJob and ConfigMap rendered",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					TraceArchival: &mlflowv1.TraceArchivalSpec{
						Enabled:   true,
						Schedule:  ptr("*/5 * * * *"),
						Location:  ptr("s3://trace-archive"),
						Retention: ptr("30d"),
					},
				},
			},
			namespace: "test-ns",
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				cronJob := findObject(objs, "CronJob", "mlflow-trace-archival")
				if cronJob == nil {
					t.Fatal("trace archival CronJob not found")
				}

				schedule, found, err := unstructured.NestedString(cronJob.Object, "spec", "schedule")
				if err != nil || !found {
					t.Fatalf("Failed to get CronJob schedule: found=%v, err=%v", found, err)
				}
				if schedule != "*/5 * * * *" {
					t.Errorf("CronJob schedule = %s, want */5 * * * *", schedule)
				}

				policy, found, err := unstructured.NestedString(cronJob.Object, "spec", "concurrencyPolicy")
				if err != nil || !found {
					t.Fatalf("Failed to get concurrencyPolicy: found=%v, err=%v", found, err)
				}
				if policy != "Forbid" {
					t.Errorf("concurrencyPolicy = %s, want Forbid", policy)
				}

				cm := findObject(objs, "ConfigMap", "mlflow-trace-archival-config")
				if cm == nil {
					t.Fatal("trace archival ConfigMap not found")
				}

				data, found, err := unstructured.NestedMap(cm.Object, "data")
				if err != nil || !found {
					t.Fatalf("Failed to get ConfigMap data: found=%v, err=%v", found, err)
				}
				yamlContent, ok := data["trace-archival.yaml"].(string)
				if !ok || yamlContent == "" {
					t.Fatal("trace-archival.yaml not found in ConfigMap data")
				}
			},
		},
		{
			name: "archival enabled - Deployment keeps JOB_EXECUTION=false and mounts config",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					TraceArchival: &mlflowv1.TraceArchivalSpec{
						Enabled:  true,
						Schedule: ptr("*/5 * * * *"),
					},
				},
			},
			namespace: "test-ns",
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				dep := findObject(objs, deploymentKind, "mlflow")
				if dep == nil {
					t.Fatal("Deployment not found")
				}
				assertJobExecutionEnv(t, dep, "false")
				assertArchivalConfigEnv(t, dep)
				assertArchivalVolumeMount(t, dep)
			},
		},
		{
			name: "archival enabled multi-replica - no primary Deployment, single Deployment unchanged",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					Replicas:        ptr(int32(3)),
					TraceArchival: &mlflowv1.TraceArchivalSpec{
						Enabled:  true,
						Schedule: ptr("*/5 * * * *"),
					},
				},
			},
			namespace: "test-ns",
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				dep := findObject(objs, deploymentKind, "mlflow")
				if dep == nil {
					t.Fatal("Deployment not found")
				}

				replicas, found, err := unstructured.NestedInt64(dep.Object, "spec", "replicas")
				if err != nil || !found {
					t.Fatalf("Failed to get replicas: found=%v, err=%v", found, err)
				}
				if replicas != 3 {
					t.Errorf("Deployment replicas = %d, want 3 (no split)", replicas)
				}

				assertJobExecutionEnv(t, dep, "false")

				if findObject(objs, deploymentKind, "mlflow-primary") != nil {
					t.Error("primary Deployment should not exist with CronJob approach")
				}
			},
		},
		{
			name: "archival enabled - CronJob uses separate service account and RBAC",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					TraceArchival: &mlflowv1.TraceArchivalSpec{
						Enabled:  true,
						Schedule: ptr("*/5 * * * *"),
					},
				},
			},
			namespace: "test-ns",
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				cronJob := findObject(objs, "CronJob", "mlflow-trace-archival")
				if cronJob == nil {
					t.Fatal("CronJob not found")
				}

				saName, found, err := unstructured.NestedString(
					cronJob.Object,
					"spec", "jobTemplate", "spec", "template", "spec", "serviceAccountName",
				)
				if err != nil || !found {
					t.Fatalf("Failed to get serviceAccountName: found=%v, err=%v", found, err)
				}
				if saName != TraceArchivalServiceAccountName {
					t.Errorf("CronJob serviceAccountName = %s, want %s", saName, TraceArchivalServiceAccountName)
				}

				taSA := findObject(objs, "ServiceAccount", TraceArchivalServiceAccountName)
				if taSA == nil {
					t.Fatal("trace archival ServiceAccount not found in rendered objects")
				}

				serverCRB := findObject(objs, "ClusterRoleBinding", "mlflow")
				if serverCRB == nil {
					t.Fatal("ClusterRoleBinding 'mlflow' not found")
				}
				subjects, found, err := unstructured.NestedSlice(serverCRB.Object, "subjects")
				if err != nil || !found {
					t.Fatalf("Failed to get ClusterRoleBinding subjects: found=%v, err=%v", found, err)
				}
				hasArchivalSA := false
				for _, s := range subjects {
					subject := s.(map[string]interface{})
					if subject["kind"] == "ServiceAccount" && subject["name"] == TraceArchivalServiceAccountName {
						hasArchivalSA = true
					}
				}
				if !hasArchivalSA {
					t.Error("trace archival ServiceAccount not found in ClusterRoleBinding subjects")
				}

				np := findObject(objs, "NetworkPolicy", "mlflow")
				if np == nil {
					t.Fatal("NetworkPolicy 'mlflow' not found")
				}
				selectorLabels, _, _ := unstructured.NestedStringMap(np.Object, "spec", "podSelector", "matchLabels")
				if selectorLabels["component"] != "mlflow" {
					t.Errorf("NetworkPolicy podSelector should use component=mlflow, got %v", selectorLabels)
				}
			},
		},
		{
			name: "archival with resource suffix - CronJob name includes suffix",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "my-instance"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					TraceArchival: &mlflowv1.TraceArchivalSpec{
						Enabled:  true,
						Schedule: ptr("*/5 * * * *"),
					},
				},
			},
			namespace: "test-ns",
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				cronJob := findObject(objs, "CronJob", "mlflow-trace-archival-my-instance")
				if cronJob == nil {
					t.Fatal("CronJob with suffix not found in rendered objects")
				}
			},
		},
		{
			name: "archival with local storage - CronJob mounts PVC",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      ptr("sqlite:////mlflow/mlflow.db"),
					ArtifactsDestination: ptr("file:///mlflow/artifacts"),
					Storage:              &corev1.PersistentVolumeClaimSpec{},
					TraceArchival: &mlflowv1.TraceArchivalSpec{
						Enabled:  true,
						Schedule: ptr("*/5 * * * *"),
						Location: ptr("file:///mlflow/traces"),
					},
				},
			},
			namespace: "test-ns",
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				cronJob := findObject(objs, "CronJob", "mlflow-trace-archival")
				if cronJob == nil {
					t.Fatal("CronJob not found")
				}

				volumes, found, err := unstructured.NestedSlice(cronJob.Object,
					"spec", "jobTemplate", "spec", "template", "spec", "volumes")
				if err != nil || !found {
					t.Fatalf("Failed to get volumes: found=%v, err=%v", found, err)
				}

				hasStorageVolume := false
				for _, v := range volumes {
					vol := v.(map[string]interface{})
					if vol["name"] == "mlflow-storage" {
						hasStorageVolume = true
					}
				}
				if !hasStorageVolume {
					t.Error("mlflow-storage volume not found in CronJob")
				}

				containers, found, err := unstructured.NestedSlice(cronJob.Object,
					"spec", "jobTemplate", "spec", "template", "spec", "containers")
				if err != nil || !found || len(containers) == 0 {
					t.Fatalf("Failed to get containers: found=%v, err=%v", found, err)
				}

				container := containers[0].(map[string]interface{})
				mounts, found, err := unstructured.NestedSlice(container, "volumeMounts")
				if err != nil || !found {
					t.Fatalf("Failed to get volumeMounts: found=%v, err=%v", found, err)
				}

				hasStorageMount := false
				for _, m := range mounts {
					mount := m.(map[string]interface{})
					if mount["name"] == "mlflow-storage" && mount["mountPath"] == "/mlflow" {
						hasStorageMount = true
					}
				}
				if !hasStorageMount {
					t.Error("/mlflow volume mount not found in CronJob")
				}
			},
		},
		{
			name: "archival with file location but no storage - render fails",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					TraceArchival: &mlflowv1.TraceArchivalSpec{
						Enabled:  true,
						Schedule: ptr("*/5 * * * *"),
						Location: ptr("file:///mlflow/traces"),
					},
				},
			},
			namespace: "test-ns",
			wantErr:   true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			objs, err := renderer.RenderChart(tt.mlflow, tt.namespace, RenderOptions{}, nil)
			if (err != nil) != tt.wantErr {
				t.Fatalf("RenderChart() error = %v, wantErr %v", err, tt.wantErr)
			}
			if !tt.wantErr && tt.validateObjs != nil {
				tt.validateObjs(t, objs)
			}
		})
	}
}

func TestIsTraceArchivalEnabled(t *testing.T) {
	tests := []struct {
		name   string
		mlflow *mlflowv1.MLflow
		want   bool
	}{
		{
			name: "nil TraceArchival",
			mlflow: &mlflowv1.MLflow{
				Spec: mlflowv1.MLflowSpec{},
			},
			want: false,
		},
		{
			name: "nil Enabled",
			mlflow: &mlflowv1.MLflow{
				Spec: mlflowv1.MLflowSpec{
					TraceArchival: &mlflowv1.TraceArchivalSpec{Schedule: ptr("*/5 * * * *")},
				},
			},
			want: false,
		},
		{
			name: "explicitly disabled",
			mlflow: &mlflowv1.MLflow{
				Spec: mlflowv1.MLflowSpec{
					TraceArchival: &mlflowv1.TraceArchivalSpec{
						Enabled:  false,
						Schedule: ptr("*/5 * * * *"),
					},
				},
			},
			want: false,
		},
		{
			name: "enabled",
			mlflow: &mlflowv1.MLflow{
				Spec: mlflowv1.MLflowSpec{
					TraceArchival: &mlflowv1.TraceArchivalSpec{
						Enabled:  true,
						Schedule: ptr("*/5 * * * *"),
					},
				},
			},
			want: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := isTraceArchivalEnabled(tt.mlflow); got != tt.want {
				t.Errorf("isTraceArchivalEnabled() = %v, want %v", got, tt.want)
			}
		})
	}
}

func assertJobExecutionEnv(t *testing.T, dep *unstructured.Unstructured, want string) {
	t.Helper()
	containers, found, err := unstructured.NestedSlice(dep.Object,
		"spec", "template", "spec", "containers")
	if err != nil || !found || len(containers) == 0 {
		t.Fatalf("Failed to get containers: found=%v, err=%v", found, err)
	}

	container := containers[0].(map[string]interface{})
	envList, found, err := unstructured.NestedSlice(container, "env")
	if err != nil || !found {
		t.Fatalf("Failed to get env: found=%v, err=%v", found, err)
	}

	for _, e := range envList {
		envVar := e.(map[string]interface{})
		if envVar["name"] == "MLFLOW_SERVER_ENABLE_JOB_EXECUTION" {
			if envVar["value"] != want {
				t.Errorf("MLFLOW_SERVER_ENABLE_JOB_EXECUTION = %v, want %s", envVar["value"], want)
			}
			return
		}
	}
	t.Error("MLFLOW_SERVER_ENABLE_JOB_EXECUTION not found in env")
}

func assertArchivalConfigEnv(t *testing.T, dep *unstructured.Unstructured) {
	t.Helper()
	containers, found, err := unstructured.NestedSlice(dep.Object,
		"spec", "template", "spec", "containers")
	if err != nil || !found || len(containers) == 0 {
		t.Fatalf("Failed to get containers: found=%v, err=%v", found, err)
	}

	container := containers[0].(map[string]interface{})
	envList, found, err := unstructured.NestedSlice(container, "env")
	if err != nil || !found {
		t.Fatalf("Failed to get env: found=%v, err=%v", found, err)
	}

	for _, e := range envList {
		envVar := e.(map[string]interface{})
		if envVar["name"] == "MLFLOW_TRACE_ARCHIVAL_CONFIG" {
			if envVar["value"] != "/etc/mlflow/trace-archival.yaml" {
				t.Errorf("MLFLOW_TRACE_ARCHIVAL_CONFIG = %v, want /etc/mlflow/trace-archival.yaml",
					envVar["value"])
			}
			return
		}
	}
	t.Error("MLFLOW_TRACE_ARCHIVAL_CONFIG not found in env")
}

func assertArchivalVolumeMount(t *testing.T, dep *unstructured.Unstructured) {
	t.Helper()
	containers, found, err := unstructured.NestedSlice(dep.Object,
		"spec", "template", "spec", "containers")
	if err != nil || !found || len(containers) == 0 {
		t.Fatalf("Failed to get containers: found=%v, err=%v", found, err)
	}

	container := containers[0].(map[string]interface{})
	mounts, found, err := unstructured.NestedSlice(container, "volumeMounts")
	if err != nil || !found {
		t.Fatalf("Failed to get volumeMounts: found=%v, err=%v", found, err)
	}

	for _, m := range mounts {
		mount := m.(map[string]interface{})
		if mount["name"] == "trace-archival-config" && mount["mountPath"] == "/etc/mlflow" {
			return
		}
	}
	t.Error("trace-archival-config volume mount not found at /etc/mlflow")
}

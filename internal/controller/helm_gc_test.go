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

func TestMlflowToHelmValues_GarbageCollection(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name        string
		mlflow      *mlflowv1.MLflow
		wantEnabled bool
		wantValues  map[string]interface{}
	}{
		{
			name: "gc not configured - should be disabled",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
				},
			},
			wantEnabled: false,
		},
		{
			name: "gc configured with schedule only",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					GarbageCollection: &mlflowv1.GarbageCollectionSpec{
						Schedule: "0 2 * * 0",
					},
				},
			},
			wantEnabled: true,
			wantValues: map[string]interface{}{
				"schedule": "0 2 * * 0",
			},
		},
		{
			name: "gc configured with schedule and olderThan",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					GarbageCollection: &mlflowv1.GarbageCollectionSpec{
						Schedule:  "0 3 * * *",
						OlderThan: ptr("30d"),
					},
				},
			},
			wantEnabled: true,
			wantValues: map[string]interface{}{
				"schedule":  "0 3 * * *",
				"olderThan": "30d",
			},
		},
		{
			name: "gc configured with custom resources",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "test"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					GarbageCollection: &mlflowv1.GarbageCollectionSpec{
						Schedule: "0 2 * * 0",
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
				"schedule": "0 2 * * 0",
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := gomega.NewWithT(t)

			values, err := renderer.mlflowToHelmValues(tt.mlflow, "test-namespace", RenderOptions{})
			g.Expect(err).NotTo(gomega.HaveOccurred())

			gc, ok := values["garbageCollection"].(map[string]interface{})
			if !ok {
				t.Fatal("garbageCollection not found in values or wrong type")
			}

			if got := gc["enabled"].(bool); got != tt.wantEnabled {
				t.Errorf("garbageCollection.enabled = %v, want %v", got, tt.wantEnabled)
			}

			if tt.wantValues != nil {
				for key, want := range tt.wantValues {
					if got, exists := gc[key]; !exists {
						t.Errorf("garbageCollection.%s not found", key)
					} else if got != want {
						t.Errorf("garbageCollection.%s = %v, want %v", key, got, want)
					}
				}
			}

			if tt.wantEnabled && tt.mlflow.Spec.GarbageCollection.Resources != nil {
				if _, exists := gc["resources"]; !exists {
					t.Error("garbageCollection.resources not found when resources are specified")
				}
			}
		})
	}
}

func TestRenderChart_GarbageCollection(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")

	tests := []struct {
		name         string
		mlflow       *mlflowv1.MLflow
		namespace    string
		wantErr      bool
		validateObjs func(t *testing.T, objs []*unstructured.Unstructured)
	}{
		{
			name: "gc disabled - no CronJob rendered",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
				},
			},
			namespace: "test-ns",
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				cronJob := findObject(objs, "CronJob", "mlflow-gc")
				if cronJob != nil {
					t.Error("CronJob should not be rendered when gc is disabled")
				}
				if findObject(objs, "ServiceAccount", "mlflow-gc-sa") != nil {
					t.Error("GC ServiceAccount should not be rendered when gc is disabled")
				}
				if findObject(objs, "ClusterRole", "mlflow-gc") != nil {
					t.Error("GC ClusterRole should not be rendered when gc is disabled")
				}
				if findObject(objs, "ClusterRoleBinding", "mlflow-gc") != nil {
					t.Error("GC ClusterRoleBinding should not be rendered when gc is disabled")
				}
			},
		},
		{
			name: "gc enabled - CronJob rendered with correct name and schedule",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					GarbageCollection: &mlflowv1.GarbageCollectionSpec{
						Schedule: "0 2 * * 0",
					},
				},
			},
			namespace: "test-ns",
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				cronJob := findObject(objs, "CronJob", "mlflow-gc")
				if cronJob == nil {
					t.Fatal("CronJob not found in rendered objects")
				}

				schedule, found, err := unstructured.NestedString(cronJob.Object, "spec", "schedule")
				if err != nil || !found {
					t.Fatalf("Failed to get CronJob schedule: found=%v, err=%v", found, err)
				}
				if schedule != "0 2 * * 0" {
					t.Errorf("CronJob schedule = %s, want 0 2 * * 0", schedule)
				}

				policy, found, err := unstructured.NestedString(cronJob.Object, "spec", "concurrencyPolicy")
				if err != nil || !found {
					t.Fatalf("Failed to get CronJob concurrencyPolicy: found=%v, err=%v", found, err)
				}
				if policy != "Forbid" {
					t.Errorf("CronJob concurrencyPolicy = %s, want Forbid", policy)
				}
			},
		},
		{
			name: "gc enabled - CronJob uses separate service account and RBAC",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					GarbageCollection: &mlflowv1.GarbageCollectionSpec{
						Schedule: "0 2 * * 0",
					},
				},
			},
			namespace: "test-ns",
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				cronJob := findObject(objs, "CronJob", "mlflow-gc")
				if cronJob == nil {
					t.Fatal("CronJob not found in rendered objects")
				}

				serviceAccountName, found, err := unstructured.NestedString(
					cronJob.Object,
					"spec", "jobTemplate", "spec", "template", "spec", "serviceAccountName",
				)
				if err != nil || !found {
					t.Fatalf("Failed to get CronJob serviceAccountName: found=%v, err=%v", found, err)
				}
				if serviceAccountName != "mlflow-gc-sa" {
					t.Errorf("CronJob serviceAccountName = %s, want mlflow-gc-sa", serviceAccountName)
				}

				gcServiceAccount := findObject(objs, "ServiceAccount", "mlflow-gc-sa")
				if gcServiceAccount == nil {
					t.Fatal("GC ServiceAccount not found in rendered objects")
				}

				serverClusterRoleBinding := findObject(objs, "ClusterRoleBinding", "mlflow")
				if serverClusterRoleBinding == nil {
					t.Fatal("Server ClusterRoleBinding not found in rendered objects")
				}

				subjects, found, err := unstructured.NestedSlice(serverClusterRoleBinding.Object, "subjects")
				if err != nil || !found {
					t.Fatalf("Failed to get server ClusterRoleBinding subjects: found=%v, err=%v", found, err)
				}

				hasGCServiceAccountSubject := false
				for _, s := range subjects {
					subject := s.(map[string]interface{})
					if subject["kind"] == "ServiceAccount" && subject["name"] == "mlflow-gc-sa" {
						hasGCServiceAccountSubject = true
					}
				}
				if hasGCServiceAccountSubject {
					t.Error("GC ServiceAccount should not be in the server ClusterRoleBinding")
				}

				gcClusterRole := findObject(objs, "ClusterRole", "mlflow-gc")
				if gcClusterRole == nil {
					t.Fatal("GC ClusterRole not found in rendered objects")
				}

				rules, found, err := unstructured.NestedSlice(gcClusterRole.Object, "rules")
				if err != nil || !found {
					t.Fatalf("Failed to get GC ClusterRole rules: found=%v, err=%v", found, err)
				}

				foundExperimentRule := false
				for _, rule := range rules {
					ruleMap, ok := rule.(map[string]interface{})
					if !ok {
						continue
					}

					resources, _, _ := unstructured.NestedStringSlice(ruleMap, "resources")
					verbs, _, _ := unstructured.NestedStringSlice(ruleMap, "verbs")

					if len(resources) == 1 && resources[0] == "experiments" {
						foundExperimentRule = true

						expectedVerbs := map[string]bool{
							"get":    true,
							"list":   true,
							"update": true,
							"delete": true,
						}
						for _, verb := range verbs {
							delete(expectedVerbs, verb)
						}
						if len(expectedVerbs) != 0 {
							t.Errorf("GC ClusterRole rule missing verbs: %v", expectedVerbs)
						}
					}
				}
				if !foundExperimentRule {
					t.Error("GC ClusterRole experiments rule not found")
				}

				gcClusterRoleBinding := findObject(objs, "ClusterRoleBinding", "mlflow-gc")
				if gcClusterRoleBinding == nil {
					t.Fatal("GC ClusterRoleBinding not found in rendered objects")
				}

				roleRefName, found, err := unstructured.NestedString(gcClusterRoleBinding.Object, "roleRef", "name")
				if err != nil || !found {
					t.Fatalf("Failed to get GC ClusterRoleBinding roleRef.name: found=%v, err=%v", found, err)
				}
				if roleRefName != "mlflow-gc" {
					t.Errorf("GC ClusterRoleBinding roleRef.name = %s, want mlflow-gc", roleRefName)
				}

				gcSubjects, found, err := unstructured.NestedSlice(gcClusterRoleBinding.Object, "subjects")
				if err != nil || !found {
					t.Fatalf("Failed to get GC ClusterRoleBinding subjects: found=%v, err=%v", found, err)
				}

				hasGCSubject := false
				for _, s := range gcSubjects {
					subject := s.(map[string]interface{})
					if subject["kind"] == "ServiceAccount" && subject["name"] == "mlflow-gc-sa" {
						hasGCSubject = true
					}
				}
				if !hasGCSubject {
					t.Error("GC ServiceAccount subject not found in GC ClusterRoleBinding")
				}
			},
		},
		{
			name: "gc enabled with olderThan - args include --older-than flag",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					GarbageCollection: &mlflowv1.GarbageCollectionSpec{
						Schedule:  "0 3 * * *",
						OlderThan: ptr("30d"),
					},
				},
			},
			namespace: "test-ns",
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				cronJob := findObject(objs, "CronJob", "mlflow-gc")
				if cronJob == nil {
					t.Fatal("CronJob not found in rendered objects")
				}

				containers, found, err := unstructured.NestedSlice(cronJob.Object,
					"spec", "jobTemplate", "spec", "template", "spec", "containers")
				if err != nil || !found || len(containers) == 0 {
					t.Fatalf("Failed to get containers: found=%v, err=%v", found, err)
				}

				container := containers[0].(map[string]interface{})
				args, found, err := unstructured.NestedStringSlice(container, "args")
				if err != nil || !found {
					t.Fatalf("Failed to get args: found=%v, err=%v", found, err)
				}

				hasOlderThan := false
				hasAllWorkspaces := false
				for _, arg := range args {
					if arg == "--older-than=30d" {
						hasOlderThan = true
					}
					if arg == "--all-workspaces" {
						hasAllWorkspaces = true
					}
				}
				if !hasOlderThan {
					t.Error("--older-than=30d not found in CronJob args")
				}
				if !hasAllWorkspaces {
					t.Error("--all-workspaces not found in CronJob args")
				}
			},
		},
		{
			name: "gc with local storage - CronJob mounts PVC",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      ptr("sqlite:////mlflow/mlflow.db"),
					ArtifactsDestination: ptr("file:///mlflow/artifacts"),
					Storage:              &corev1.PersistentVolumeClaimSpec{},
					GarbageCollection: &mlflowv1.GarbageCollectionSpec{
						Schedule: "0 2 * * 0",
					},
				},
			},
			namespace: "test-ns",
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				cronJob := findObject(objs, "CronJob", "mlflow-gc")
				if cronJob == nil {
					t.Fatal("CronJob not found in rendered objects")
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
			name: "gc with resource suffix - CronJob name includes suffix",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "my-instance"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
					GarbageCollection: &mlflowv1.GarbageCollectionSpec{
						Schedule: "0 2 * * 0",
					},
				},
			},
			namespace: "test-ns",
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				cronJob := findObject(objs, "CronJob", "mlflow-gc-my-instance")
				if cronJob == nil {
					t.Fatal("CronJob with suffix not found in rendered objects")
				}
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			objs, err := renderer.RenderChart(tt.mlflow, tt.namespace, RenderOptions{})
			if (err != nil) != tt.wantErr {
				t.Fatalf("RenderChart() error = %v, wantErr %v", err, tt.wantErr)
			}
			if !tt.wantErr && tt.validateObjs != nil {
				tt.validateObjs(t, objs)
			}
		})
	}
}

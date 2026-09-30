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
	"context"
	"strings"
	"testing"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func TestEffectiveBackendAndRuntimeOverrides(t *testing.T) {
	ctx := context.Background()
	scheme := runtime.NewScheme()
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	secret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "backend", Namespace: "apps"}, Data: map[string][]byte{
		"uri":  []byte("postgresql://user:private@db-b/mlflow"),
		"flag": []byte("FALSE"),
	}}
	configMap := &corev1.ConfigMap{ObjectMeta: metav1.ObjectMeta{Name: "settings", Namespace: "apps"}, Data: map[string]string{
		"uri": "mysql+pymysql://db-c/mlflow", "flag": "1", "prefix": "postgresql://", backendStoreEnvName: "postgresql://env-from/mlflow",
	}}
	reader := fake.NewClientBuilder().WithScheme(scheme).WithObjects(secret, configMap).Build()
	reconciler := &MLflowReconciler{APIReader: reader}
	for _, tc := range []struct {
		name                 string
		backend              string
		env                  []corev1.EnvVar
		envFrom              []corev1.EnvFromSource
		wantURI              string
		wantSQL, wantRuntime bool
		wantError            bool
	}{
		{name: "SQL to SQL literal", backend: "postgresql://db-a/mlflow", env: []corev1.EnvVar{{Name: backendStoreEnvName, Value: "mysql://db-b/mlflow"}}, wantURI: "mysql://db-b/mlflow", wantSQL: true, wantRuntime: true},
		{name: "SQL to SQLite", backend: "postgresql://db-a/mlflow", env: []corev1.EnvVar{{Name: backendStoreEnvName, Value: "sqlite:////mlflow/mlflow.db"}}, wantURI: "sqlite:////mlflow/mlflow.db"},
		{name: "SQLite to SQL", backend: "sqlite:////mlflow/mlflow.db", env: []corev1.EnvVar{{Name: backendStoreEnvName, Value: "postgresql://db-b/mlflow"}}, wantURI: "postgresql://db-b/mlflow", wantSQL: true, wantRuntime: true},
		{name: "Secret backend and flag", backend: "postgresql://db-a/mlflow", env: []corev1.EnvVar{{Name: backendStoreEnvName, ValueFrom: &corev1.EnvVarSource{SecretKeyRef: &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "backend"}, Key: "uri"}}}, {Name: rollupsRuntimeEnvName, ValueFrom: &corev1.EnvVarSource{SecretKeyRef: &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "backend"}, Key: "flag"}}}}, wantURI: "postgresql://user:private@db-b/mlflow", wantSQL: true},
		{name: "ConfigMap backend and flag", backend: "postgresql://db-a/mlflow", env: []corev1.EnvVar{{Name: backendStoreEnvName, ValueFrom: &corev1.EnvVarSource{ConfigMapKeyRef: &corev1.ConfigMapKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "settings"}, Key: "uri"}}}, {Name: rollupsRuntimeEnvName, ValueFrom: &corev1.EnvVarSource{ConfigMapKeyRef: &corev1.ConfigMapKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "settings"}, Key: "flag"}}}}, wantURI: "mysql+pymysql://db-c/mlflow", wantSQL: true, wantRuntime: true},
		{name: "envFrom wins when optional explicit backend is absent", backend: "postgresql://db-a/mlflow", envFrom: []corev1.EnvFromSource{{ConfigMapRef: &corev1.ConfigMapEnvSource{LocalObjectReference: corev1.LocalObjectReference{Name: "settings"}}}}, env: []corev1.EnvVar{{Name: backendStoreEnvName, ValueFrom: &corev1.EnvVarSource{SecretKeyRef: &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "missing"}, Key: "uri", Optional: ptr(true)}}}}, wantURI: "postgresql://env-from/mlflow", wantSQL: true, wantRuntime: true},
		{name: "expansion uses preceding env", backend: "postgresql://db-a/mlflow", env: []corev1.EnvVar{{Name: "SCHEME", Value: "mysql"}, {Name: backendStoreEnvName, Value: "$(SCHEME)://db-b/mlflow"}}, wantURI: "mysql://db-b/mlflow", wantSQL: true, wantRuntime: true},
		{name: "invalid runtime flag", backend: "postgresql://db-a/mlflow", env: []corev1.EnvVar{{Name: rollupsRuntimeEnvName, Value: " true"}}, wantURI: "postgresql://db-a/mlflow", wantSQL: true, wantError: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			instance := &mlflowv1.MLflow{Spec: mlflowv1.MLflowSpec{BackendStoreURI: ptr(tc.backend), Env: tc.env, EnvFrom: tc.envFrom,
				Storage: &corev1.PersistentVolumeClaimSpec{AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce}},
			}}
			uri, err := reconciler.effectiveBackendURI(ctx, instance, "apps")
			if tc.wantError && tc.name != "invalid runtime flag" {
				if err == nil {
					t.Fatal("expected backend resolution error")
				}
				return
			}
			if err != nil || uri != tc.wantURI {
				t.Fatalf("backend = %q, %v; want %q", uri, err, tc.wantURI)
			}
			sql, err := reconciler.traceRollupsSQLBackend(ctx, instance, "apps")
			if err != nil || sql != tc.wantSQL {
				t.Fatalf("remote SQL = %v, %v; want %v", sql, err, tc.wantSQL)
			}
			runtimeEnabled, err := reconciler.effectiveRollupsRuntime(ctx, instance, "apps", sql)
			if tc.name == "invalid runtime flag" {
				if err == nil || strings.Contains(err.Error(), " true") {
					t.Fatalf("invalid flag leaked or passed: %v", err)
				}
				return
			}
			if err != nil || runtimeEnabled != tc.wantRuntime {
				t.Fatalf("runtime = %v, %v; want %v", runtimeEnabled, err, tc.wantRuntime)
			}
		})
	}
	if got := expandKubeEnv("$$(SCHEME)-$(SCHEME)", map[string]string{"SCHEME": "mysql"}); got != "$(SCHEME)-mysql" {
		t.Fatalf("escaped expansion = %q", got)
	}
}

func TestBackendOverrideRenderedOnceAcrossWorkloads(t *testing.T) {
	instance := &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: "mlflow"}, Spec: mlflowv1.MLflowSpec{
		BackendStoreURI:   ptr("postgresql://db-a/mlflow"),
		Env:               []corev1.EnvVar{{Name: "PREFIX", Value: "mysql://"}, {Name: backendStoreEnvName, Value: "$(PREFIX)db-b/mlflow"}},
		GarbageCollection: &mlflowv1.GarbageCollectionSpec{},
		TraceArchival:     &mlflowv1.TraceArchivalSpec{Enabled: true, Location: ptr("s3://bucket/archives")},
	}}
	objects, err := NewHelmRenderer("../../charts/mlflow").RenderChart(instance, "apps", RenderOptions{BackendUsesStorage: ptr(false)}, nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, workload := range []struct{ kind, name string }{
		{"Deployment", "mlflow"}, {"CronJob", "mlflow-gc"}, {"CronJob", "mlflow-trace-archival"}, {"CronJob", "mlflow-trace-rollups"},
	} {
		obj := findObject(objects, workload.kind, workload.name)
		if obj == nil {
			t.Fatalf("missing %s %s", workload.kind, workload.name)
		}
		env, err := traceRollupsWorkloadEnv(obj)
		if err != nil {
			t.Fatal(err)
		}
		count := 0
		for _, variable := range env {
			if variable.Name == backendStoreEnvName {
				count++
				if variable.Value != "$(PREFIX)db-b/mlflow" || variable.ValueFrom != nil {
					t.Fatalf("backend override changed: %+v", variable)
				}
			}
		}
		if count != 1 {
			t.Fatalf("%s has %d backend env entries", workload.name, count)
		}
		if workload.name == "mlflow-trace-archival" {
			found := false
			for _, variable := range env {
				if variable.Name == "_MLFLOW_SERVER_FILE_STORE" {
					found = variable.Value == "$(MLFLOW_BACKEND_STORE_URI)"
				}
			}
			if !found {
				t.Fatal("archival does not follow effective backend")
			}
		}
		if workload.name == "mlflow-gc" {
			job := &batchv1.CronJob{}
			if err := runtime.DefaultUnstructuredConverter.FromUnstructured(obj.Object, job); err != nil {
				t.Fatal(err)
			}
			if !strings.Contains(strings.Join(job.Spec.JobTemplate.Spec.Template.Spec.Containers[0].Args, " "), "--backend-store-uri=$(MLFLOW_BACKEND_STORE_URI)") {
				t.Fatal("GC does not follow effective backend")
			}
		}
	}
}

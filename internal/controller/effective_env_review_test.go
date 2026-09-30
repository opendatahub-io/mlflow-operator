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
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func TestEffectiveEnvResolvesOnlyOrderedDependencies(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	cm := &corev1.ConfigMap{ObjectMeta: metav1.ObjectMeta{Name: "settings", Namespace: "apps"},
		Data:       map[string]string{"SCHEME": "postgresql", backendStoreEnvName: "mysql://fallback/db"},
		BinaryData: map[string][]byte{"BINARY_SCHEME": []byte("postgresql"), "uri": []byte("postgresql://binary/db")},
	}
	missingKeySecret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "missing-key", Namespace: "apps"}, Data: map[string][]byte{"other": []byte("present")}}
	emptyValueSecret := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Name: "empty-value", Namespace: "apps"},
		Data:       map[string][]byte{"uri": {}},
	}
	r := &MLflowReconciler{APIReader: fake.NewClientBuilder().WithScheme(scheme).WithObjects(cm, missingKeySecret, emptyValueSecret).Build()}
	ref := func(name string, optional bool) corev1.EnvVar {
		return corev1.EnvVar{Name: name, ValueFrom: &corev1.EnvVarSource{ConfigMapKeyRef: &corev1.ConfigMapKeySelector{
			LocalObjectReference: corev1.LocalObjectReference{Name: "settings"}, Key: "uri", Optional: ptr(optional),
		}}}
	}
	missing := corev1.EnvVar{Name: rollupsRuntimeEnvName, ValueFrom: &corev1.EnvVarSource{
		SecretKeyRef: &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "missing"}, Key: "flag"},
	}}
	for _, tc := range []struct {
		name        string
		env         []corev1.EnvVar
		backendFrom *corev1.SecretKeySelector
		want        string
		wantError   bool
	}{
		{name: "unrelated missing ref before literal", env: []corev1.EnvVar{missing, {Name: backendStoreEnvName, Value: "postgresql://literal/db"}}, want: "postgresql://literal/db"},
		{name: "unrelated missing ref after literal", env: []corev1.EnvVar{{Name: backendStoreEnvName, Value: "postgresql://literal/db"}, missing}, want: "postgresql://literal/db"},
		{name: "ordered nested expansion", env: []corev1.EnvVar{{Name: "URI", Value: "$(SCHEME)://ordered/db"}, {Name: backendStoreEnvName, Value: "$(URI)"}, {Name: "SCHEME", Value: "mysql"}}, want: "postgresql://ordered/db"},
		{name: "escaped dependency", env: []corev1.EnvVar{{Name: "UNRELATED", ValueFrom: missing.ValueFrom}, {Name: backendStoreEnvName, Value: "postgresql://db/$$(UNRELATED)"}}, want: "postgresql://db/$(UNRELATED)"},
		{name: "required binary key is absent", env: []corev1.EnvVar{ref(backendStoreEnvName, false)}, wantError: true},
		{name: "optional binary key uses envFrom", env: []corev1.EnvVar{ref(backendStoreEnvName, true)}, want: "mysql://fallback/db"},
		{name: "optional backend Secret fallback uses envFrom", backendFrom: &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "missing"}, Key: "uri", Optional: ptr(true)}, want: "mysql://fallback/db"},
		{name: "optional backend key fallback uses envFrom", backendFrom: &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "missing-key"}, Key: "uri", Optional: ptr(true)}, want: "mysql://fallback/db"},
		{name: "present empty optional backend value does not use envFrom", backendFrom: &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "empty-value"}, Key: "uri", Optional: ptr(true)}, wantError: true},
		{name: "binary expansion stays unresolved", env: []corev1.EnvVar{{Name: backendStoreEnvName, Value: "$(BINARY_SCHEME)://db"}}, want: "$(BINARY_SCHEME)://db"},
		{name: "required expansion dependency fails", env: []corev1.EnvVar{{Name: "SCHEME", ValueFrom: missing.ValueFrom}, {Name: backendStoreEnvName, Value: "$(SCHEME)://db"}}, wantError: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			instance := &mlflowv1.MLflow{Spec: mlflowv1.MLflowSpec{BackendStoreURIFrom: tc.backendFrom, Env: tc.env,
				EnvFrom: []corev1.EnvFromSource{{ConfigMapRef: &corev1.ConfigMapEnvSource{LocalObjectReference: corev1.LocalObjectReference{Name: "settings"}}}},
			}}
			got, err := r.effectiveBackendURI(context.Background(), instance, "apps")
			if (err != nil) != tc.wantError || (!tc.wantError && got != tc.want) {
				t.Fatalf("backend = %q, %v; want %q, error=%t", got, err, tc.want, tc.wantError)
			}
		})
	}
	instance := &mlflowv1.MLflow{Spec: mlflowv1.MLflowSpec{Env: []corev1.EnvVar{{Name: backendStoreEnvName, Value: "postgresql://literal/db"}},
		EnvFrom: []corev1.EnvFromSource{{SecretRef: &corev1.SecretEnvSource{LocalObjectReference: corev1.LocalObjectReference{Name: "missing"}}}},
	}}
	if _, err := r.effectiveBackendURI(context.Background(), instance, "apps"); err != nil {
		t.Fatalf("literal backend resolved unrelated envFrom: %v", err)
	}
}

func TestEffectiveBackendResolutionMatchesGeneratedWorkloadEnvironment(t *testing.T) {
	registryURI := "postgresql://registry.example.com/mlflow"
	instance := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: ResourceName},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI:  ptr("postgresql://backend.example.com/mlflow"),
			RegistryStoreURI: ptr(registryURI),
			Env:              []corev1.EnvVar{{Name: backendStoreEnvName, Value: "$(" + registryStoreEnvName + ")"}},
		},
	}

	reconciler := &MLflowReconciler{}
	resolvedURI, err := reconciler.effectiveBackendURI(context.Background(), instance, "apps")
	if err != nil {
		t.Fatal(err)
	}
	if resolvedURI != registryURI {
		t.Fatalf("resolved backend URI = %q, want %q", resolvedURI, registryURI)
	}

	objects, err := NewHelmRenderer("../../charts/mlflow").RenderChart(instance, "apps", RenderOptions{
		BackendUsesStorage:         ptr(false),
		TraceRollupsRuntimeEnabled: ptr(true),
	}, nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, workload := range []struct{ kind, name string }{
		{kind: "Deployment", name: ResourceName},
		{kind: "CronJob", name: "mlflow-trace-rollups"},
	} {
		obj := findObject(objects, workload.kind, workload.name)
		if obj == nil {
			t.Fatalf("missing %s %s", workload.kind, workload.name)
		}
		env, err := traceRollupsWorkloadEnv(obj)
		if err != nil {
			t.Fatal(err)
		}
		renderedURI, found := expandedLiteralEnv(env, backendStoreEnvName)
		if !found || renderedURI != resolvedURI {
			t.Errorf("%s %s backend URI = %q, found=%t; resolver returned %q", workload.kind, workload.name, renderedURI, found, resolvedURI)
		}
	}
}

func expandedLiteralEnv(env []corev1.EnvVar, name string) (string, bool) {
	previous := make(map[string]string, len(env))
	for _, variable := range env {
		if variable.ValueFrom != nil {
			continue
		}
		previous[variable.Name] = expandKubeEnv(variable.Value, previous)
	}
	value, found := previous[name]
	return value, found
}

func TestDuplicateManagedEnvRejectedByResolverAndRenderer(t *testing.T) {
	for _, name := range []string{backendStoreEnvName, rollupsRuntimeEnvName} {
		for _, last := range []corev1.EnvVar{{Name: name, Value: "$(SAVED_URI)"}, {Name: name, ValueFrom: &corev1.EnvVarSource{
			SecretKeyRef: &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "missing"}, Key: "uri", Optional: ptr(true)},
		}}} {
			instance := &mlflowv1.MLflow{Spec: mlflowv1.MLflowSpec{BackendStoreURI: ptr("postgresql://db/default"), Env: []corev1.EnvVar{
				{Name: name, Value: "postgresql://first/db"}, {Name: "SAVED_URI", Value: "$(" + name + ")"}, last,
			}}}
			if _, err := (&MLflowReconciler{}).effectiveBackendURI(context.Background(), instance, "apps"); err == nil || !strings.Contains(err.Error(), "at most one") {
				t.Fatalf("duplicate %s accepted by resolver: %v", name, err)
			}
			if _, err := NewHelmRenderer("../../charts/mlflow").RenderChart(instance, "apps", RenderOptions{}, nil); err == nil || !strings.Contains(err.Error(), "at most one") {
				t.Fatalf("duplicate %s accepted by renderer: %v", name, err)
			}
		}
	}
}

func TestEffectiveSQLiteStorageValidation(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	uri := "sqlite:////mlflow/mlflow.db"
	secret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "backend", Namespace: "apps"}, Data: map[string][]byte{"uri": []byte(uri)}}
	r := &MLflowReconciler{APIReader: fake.NewClientBuilder().WithScheme(scheme).WithObjects(secret).Build()}
	for _, override := range []corev1.EnvVar{{Name: backendStoreEnvName, Value: uri}, {Name: backendStoreEnvName, ValueFrom: &corev1.EnvVarSource{
		SecretKeyRef: &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "backend"}, Key: "uri"},
	}}} {
		instance := &mlflowv1.MLflow{Spec: mlflowv1.MLflowSpec{BackendStoreURI: ptr("postgresql://unused/db"), Env: []corev1.EnvVar{override}}}
		if _, err := r.traceRollupsSQLBackend(context.Background(), instance, "apps"); err == nil || !strings.Contains(err.Error(), "spec.storage") {
			t.Fatalf("SQLite without storage passed: %v", err)
		}
		instance.Spec.Storage = &corev1.PersistentVolumeClaimSpec{AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce}}
		if sql, err := r.traceRollupsSQLBackend(context.Background(), instance, "apps"); err != nil || sql {
			t.Fatalf("SQLite with storage = %v, %v", sql, err)
		}
		instance.Spec.Replicas = ptr(int32(2))
		if _, err := r.traceRollupsSQLBackend(context.Background(), instance, "apps"); err == nil {
			t.Fatal("multiple SQLite replicas accepted ReadWriteOnce")
		}
		instance.Spec.Replicas = nil
		instance.Spec.TraceArchival = &mlflowv1.TraceArchivalSpec{Enabled: true}
		if _, err := r.traceRollupsSQLBackend(context.Background(), instance, "apps"); err == nil {
			t.Fatal("SQLite archival accepted ReadWriteOnce")
		}
		instance.Spec.Storage.AccessModes = []corev1.PersistentVolumeAccessMode{corev1.ReadWriteMany}
		if _, err := r.traceRollupsSQLBackend(context.Background(), instance, "apps"); err != nil {
			t.Fatal(err)
		}
	}
}

func TestSQLiteSkipsReferencedRollupRuntimeDuringRendering(t *testing.T) {
	for _, opts := range []RenderOptions{{}, {BackendUsesStorage: ptr(true), TraceRollupsDisabled: true}} {
		instance := &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: "mlflow"}, Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr("sqlite:////mlflow/mlflow.db"),
			Env: []corev1.EnvVar{{Name: rollupsRuntimeEnvName, ValueFrom: &corev1.EnvVarSource{
				ConfigMapKeyRef: &corev1.ConfigMapKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "runtime"}, Key: "flag"},
			}}},
		}}
		objects, err := NewHelmRenderer("../../charts/mlflow").RenderChart(instance, "apps", opts, nil)
		if err != nil {
			t.Fatal(err)
		}
		if findObject(objects, "CronJob", "mlflow-trace-rollups") != nil {
			t.Fatal("SQLite rendered a rollup CronJob")
		}
	}
}

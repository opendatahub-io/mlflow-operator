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
	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func TestTraceRollupsRendering(t *testing.T) {
	for _, tc := range []struct {
		name, uri string
		spec      *mlflowv1.TraceRollupsSpec
		opts      RenderOptions
		want      bool
		wantFlag  bool
	}{
		{name: "postgres defaults", uri: "postgresql://host/db", want: true, wantFlag: true},
		{name: "mysql driver", uri: "mysql+pymysql://host/db", want: true, wantFlag: true},
		{name: "sqlite", uri: "sqlite:////mlflow/mlflow.db"},
		{name: "sqlite driver", uri: "sqlite+pysqlite:////mlflow/mlflow.db", spec: &mlflowv1.TraceRollupsSpec{Enabled: ptr(true)}},
		{name: "omitted backend"},
		{name: "disabled", uri: "postgresql://host/db", spec: &mlflowv1.TraceRollupsSpec{Enabled: ptr(false)}, wantFlag: true},
		{name: "disabled invalid schedule", uri: "postgresql://host/db", spec: &mlflowv1.TraceRollupsSpec{Enabled: ptr(false), Schedule: ptr("@daily"), TimeZone: ptr("Mars/Crater")}, wantFlag: true},
		{name: "empty block", uri: "postgresql://host/db", spec: &mlflowv1.TraceRollupsSpec{}, want: true, wantFlag: true},
		{name: "custom schedule", uri: "postgresql://host/db", spec: &mlflowv1.TraceRollupsSpec{Schedule: ptr("30 1 * * *"), TimeZone: ptr("America/New_York")}, want: true, wantFlag: true},
		{name: "resolved secret sqlite", uri: "postgresql://host/db", opts: RenderOptions{TraceRollupsDisabled: true}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			instance := &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: "scale"}, Spec: mlflowv1.MLflowSpec{TraceRollups: tc.spec}}
			if tc.uri != "" {
				instance.Spec.BackendStoreURI = ptr(tc.uri)
			}
			renderer := NewHelmRenderer("../../charts/mlflow")
			objs, err := renderer.RenderChart(instance, "apps", tc.opts, nil)
			if err != nil {
				t.Fatal(err)
			}
			obj := findObject(objs, "CronJob", "mlflow-trace-rollups-scale")
			if (obj != nil) != tc.want {
				t.Fatalf("CronJob present = %v, want %v", obj != nil, tc.want)
			}
			deployment := &appsv1.Deployment{}
			if err := runtime.DefaultUnstructuredConverter.FromUnstructured(findObject(objs, "Deployment", "mlflow-scale").Object, deployment); err != nil {
				t.Fatal(err)
			}
			flagCount := 0
			for _, env := range deployment.Spec.Template.Spec.Containers[0].Env {
				if env.Name == "MLFLOW_SQL_TRACE_ROLLUPS_ENABLED" {
					flagCount++
					if env.Value != "true" {
						t.Fatalf("unexpected server flag: %+v", env)
					}
				}
			}
			if (flagCount == 1) != tc.wantFlag || flagCount > 1 {
				t.Fatalf("server flag count = %d, want enabled = %v", flagCount, tc.wantFlag)
			}
			if !tc.want {
				return
			}
			job := &batchv1.CronJob{}
			if err := runtime.DefaultUnstructuredConverter.FromUnstructured(obj.Object, job); err != nil {
				t.Fatal(err)
			}
			schedule, zone := "0 2 * * *", "Etc/UTC"
			if tc.spec != nil && tc.spec.Schedule != nil {
				if tc.spec.TimeZone == nil {
					t.Fatal("table row sets Schedule without TimeZone")
				}
				schedule = *tc.spec.Schedule
				zone = *tc.spec.TimeZone
			}
			if job.Spec.Schedule != schedule || job.Spec.TimeZone == nil || *job.Spec.TimeZone != zone || job.Spec.ConcurrencyPolicy != batchv1.ForbidConcurrent {
				t.Fatalf("unexpected CronJob schedule: %+v", job.Spec)
			}
			pod := job.Spec.JobTemplate.Spec.Template.Spec
			if pod.AutomountServiceAccountToken == nil || *pod.AutomountServiceAccountToken {
				t.Fatal("rollup job must not mount a token")
			}
			for _, vol := range pod.Volumes {
				if vol.PersistentVolumeClaim != nil {
					t.Fatal("rollup job must not mount PVC")
				}
			}
			command := strings.Join(pod.Containers[0].Command, " ")
			if !strings.Contains(command, "run_sql_trace_rollup_scheduler") || !strings.Contains(command, "failed partitions") {
				t.Fatal("standalone script or failure propagation missing")
			}
		})
	}
}

func TestTraceRollupsSecretAndResources(t *testing.T) {
	claim := corev1.PodResourceClaim{
		Name:                      "rollup-gpu",
		ResourceClaimTemplateName: ptr("rollup-gpu-template"),
	}
	instance := &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: "mlflow"}, Spec: mlflowv1.MLflowSpec{
		BackendStoreURIFrom:       &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "db"}, Key: "uri"},
		Env:                       []corev1.EnvVar{{Name: "MLFLOW_BACKEND_STORE_URI", Value: "postgresql://wrong/db"}},
		EnvFrom:                   []corev1.EnvFromSource{{SecretRef: &corev1.SecretEnvSource{LocalObjectReference: corev1.LocalObjectReference{Name: "env"}}}},
		ServiceAccountAnnotations: map[string]string{"identity": "rollups"},
		TraceRollups: &mlflowv1.TraceRollupsSpec{
			Resources: &corev1.ResourceRequirements{
				Requests: corev1.ResourceList{corev1.ResourceCPU: resource.MustParse("250m")},
				Claims:   []corev1.ResourceClaim{{Name: "rollup-gpu", Request: "gpu"}},
			},
			ResourceClaims: []corev1.PodResourceClaim{claim},
		},
	}}
	objs, err := NewHelmRenderer("../../charts/mlflow").RenderChart(instance, "apps", RenderOptions{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	cj := &batchv1.CronJob{}
	if err := runtime.DefaultUnstructuredConverter.FromUnstructured(findObject(objs, "CronJob", "mlflow-trace-rollups").Object, cj); err != nil {
		t.Fatal(err)
	}
	c := cj.Spec.JobTemplate.Spec.Template.Spec.Containers[0]
	if len(cj.Spec.JobTemplate.Spec.Template.Spec.ResourceClaims) != 1 || cj.Spec.JobTemplate.Spec.Template.Spec.ResourceClaims[0].Name != claim.Name {
		t.Fatalf("Pod resource claims = %+v, want claim %q", cj.Spec.JobTemplate.Spec.Template.Spec.ResourceClaims, claim.Name)
	}
	backendCount, rollupCount := 0, 0
	for _, v := range c.Env {
		if v.Name == "MLFLOW_BACKEND_STORE_URI" {
			backendCount++
			if v.ValueFrom == nil || v.ValueFrom.SecretKeyRef.Name != "db" {
				t.Fatal("job must use primary Secret")
			}
		}
		if v.Name == "MLFLOW_SQL_TRACE_ROLLUPS_ENABLED" {
			rollupCount++
			if v.Value != "true" {
				t.Fatal("rollup job must enable its direct entrypoint")
			}
		}
	}
	if backendCount != 1 || rollupCount != 1 || len(c.EnvFrom) != 1 || c.Resources.Requests.Cpu().String() != "250m" || len(c.Resources.Claims) != 1 || c.Resources.Claims[0].Name != claim.Name {
		t.Fatal("Secret/env/resources mapping failed")
	}
	sa := findObject(objs, "ServiceAccount", "mlflow-sa")
	if sa.GetAnnotations()["identity"] != "rollups" {
		t.Fatal("workload identity annotation lost")
	}
}

func TestTraceRollupsSecretResolution(t *testing.T) {
	scheme := runtime.NewScheme()
	_ = corev1.AddToScheme(scheme)
	for _, uri := range []string{"postgresql://host/db", "mysql+pymysql://host/db", "sqlite:////mlflow/mlflow.db", ""} {
		secret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "db", Namespace: "apps"}, Data: map[string][]byte{"uri": []byte(uri)}}
		reader := fake.NewClientBuilder().WithScheme(scheme).WithObjects(secret).Build()
		instance := &mlflowv1.MLflow{Spec: mlflowv1.MLflowSpec{BackendStoreURIFrom: &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "db"}, Key: "uri"}}}
		got, err := (&MLflowReconciler{APIReader: reader}).traceRollupsSQLBackend(context.Background(), instance, "apps")
		if uri == "" {
			if err == nil {
				t.Fatal("empty Secret value must fail")
			}
			continue
		}
		if err != nil || got != isRemoteSQLMetadataStoreURI(uri) {
			t.Fatalf("resolution = %v, %v", got, err)
		}
	}
	selector := &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "db"}, Key: "uri"}
	for _, tc := range []struct {
		name   string
		secret *corev1.Secret
	}{
		{name: "missing secret"},
		{name: "missing key", secret: &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "db", Namespace: "apps"}, Data: map[string][]byte{"other": []byte("postgresql://host/db")}}},
	} {
		builder := fake.NewClientBuilder().WithScheme(scheme)
		if tc.secret != nil {
			builder = builder.WithObjects(tc.secret)
		}
		instance := &mlflowv1.MLflow{Spec: mlflowv1.MLflowSpec{BackendStoreURIFrom: selector}}
		if _, err := (&MLflowReconciler{APIReader: builder.Build()}).traceRollupsSQLBackend(context.Background(), instance, "apps"); err == nil {
			t.Fatalf("%s: expected resolution error", tc.name)
		}
	}
	// Explicitly disabled scheduling must skip Secret resolution entirely.
	disabled := &mlflowv1.MLflow{Spec: mlflowv1.MLflowSpec{
		BackendStoreURIFrom: selector,
		TraceRollups:        &mlflowv1.TraceRollupsSpec{Enabled: ptr(false)},
	}}
	got, err := (&MLflowReconciler{APIReader: fake.NewClientBuilder().WithScheme(scheme).Build()}).traceRollupsSQLBackend(context.Background(), disabled, "apps")
	if err != nil || !got {
		t.Fatalf("disabled resolution = %v, %v", got, err)
	}
}

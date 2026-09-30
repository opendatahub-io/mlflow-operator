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

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func TestTraceRollupsRendering(t *testing.T) {
	for _, tc := range []struct {
		name, uri string
		spec      *mlflowv1.TraceRollupsSpec
		opts      RenderOptions
		want      bool
	}{
		{name: "postgres defaults", uri: "postgresql://host/db", want: true},
		{name: "mysql driver", uri: "mysql+pymysql://host/db", want: true},
		{name: "sqlite", uri: "sqlite:////mlflow/mlflow.db"},
		{name: "sqlite driver", uri: "sqlite+pysqlite:////mlflow/mlflow.db", spec: &mlflowv1.TraceRollupsSpec{Enabled: ptr(true)}},
		{name: "omitted backend"},
		{name: "disabled", uri: "postgresql://host/db", spec: &mlflowv1.TraceRollupsSpec{Enabled: ptr(false)}},
		{name: "empty block", uri: "postgresql://host/db", spec: &mlflowv1.TraceRollupsSpec{}, want: true},
		{name: "custom schedule", uri: "postgresql://host/db", spec: &mlflowv1.TraceRollupsSpec{Schedule: ptr("30 1 * * *"), TimeZone: ptr("America/New_York")}, want: true},
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
			if tc.want {
				scaled := findObject(scaledDownObjects(objs, "mlflow-scale"), "CronJob", "mlflow-trace-rollups-scale")
				suspended, _, err := unstructured.NestedBool(scaled.Object, "spec", "suspend")
				if err != nil || !suspended {
					t.Fatal("migration rendering must suspend rollups")
				}
				originallySuspended, _, _ := unstructured.NestedBool(obj.Object, "spec", "suspend")
				if originallySuspended {
					t.Fatal("normal rendering must resume rollups")
				}
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
			dep := findObject(objs, "Deployment", "mlflow-scale")
			deployment := &appsv1.Deployment{}
			if err := runtime.DefaultUnstructuredConverter.FromUnstructured(dep.Object, deployment); err != nil {
				t.Fatal(err)
			}
			env := deployment.Spec.Template.Spec.Containers[0].Env
			found := false
			for _, v := range env {
				if v.Name == "MLFLOW_SQL_TRACE_ROLLUPS_ENABLED" && v.Value == "true" {
					found = true
				}
			}
			if !found {
				t.Fatal("SQL server rollup read/write feature was not enabled")
			}
		})
	}
}

func TestTraceRollupsSecretAndOverrides(t *testing.T) {
	instance := &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: "mlflow"}, Spec: mlflowv1.MLflowSpec{
		BackendStoreURIFrom:       &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "db"}, Key: "uri"},
		Env:                       []corev1.EnvVar{{Name: "MLFLOW_SQL_TRACE_ROLLUPS_ENABLED", Value: "false"}, {Name: "MLFLOW_BACKEND_STORE_URI", Value: "postgresql://wrong/db"}},
		EnvFrom:                   []corev1.EnvFromSource{{SecretRef: &corev1.SecretEnvSource{LocalObjectReference: corev1.LocalObjectReference{Name: "env"}}}},
		ServiceAccountAnnotations: map[string]string{"identity": "rollups"},
		TraceRollups:              &mlflowv1.TraceRollupsSpec{Resources: &corev1.ResourceRequirements{Requests: corev1.ResourceList{corev1.ResourceCPU: resource.MustParse("250m")}}},
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
			if v.Value != "false" {
				t.Fatal("explicit rollup override lost")
			}
		}
	}
	if backendCount != 1 || rollupCount != 1 || len(c.EnvFrom) != 1 || c.Resources.Requests.Cpu().String() != "250m" {
		t.Fatal("Secret/env/resources mapping failed")
	}
	sa := findObject(objs, "ServiceAccount", "mlflow-sa")
	if sa.GetAnnotations()["identity"] != "rollups" {
		t.Fatal("workload identity annotation lost")
	}
}

func TestTraceRollupsInvalidSchedule(t *testing.T) {
	for _, spec := range []*mlflowv1.TraceRollupsSpec{
		{Schedule: ptr("@daily")}, {Schedule: ptr("0 25 * * *")}, {Schedule: ptr("TZ=UTC 0 2 * * *")},
		{TimeZone: ptr("Mars/Crater")}, {TimeZone: ptr("Local")},
	} {
		_, err := (&HelmRenderer{}).mlflowToHelmValues(&mlflowv1.MLflow{Spec: mlflowv1.MLflowSpec{BackendStoreURI: ptr("postgresql://host/db"), TraceRollups: spec}}, "apps", RenderOptions{}, nil)
		if err == nil {
			t.Fatalf("expected invalid schedule/timezone rejection: %+v", spec)
		}
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
}

func TestTraceRollupsMigrationAndCleanup(t *testing.T) {
	scheme := runtime.NewScheme()
	_ = batchv1.AddToScheme(scheme)
	_ = mlflowv1.AddToScheme(scheme)
	instance := &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: "mlflow", UID: types.UID("instance")}}
	cj := &batchv1.CronJob{ObjectMeta: metav1.ObjectMeta{Name: "mlflow-trace-rollups", Namespace: "apps", OwnerReferences: []metav1.OwnerReference{{APIVersion: mlflowv1.GroupVersion.String(), Kind: "MLflow", Name: instance.Name, UID: instance.UID, Controller: ptr(true)}}}}
	job := &batchv1.Job{ObjectMeta: metav1.ObjectMeta{Name: "rollup-manual", Namespace: "apps", Labels: map[string]string{traceRollupsInstanceLabel: "mlflow"}}}
	c := fake.NewClientBuilder().WithScheme(scheme).WithStatusSubresource(job).WithObjects(cj, job).Build()
	reconciler := &MLflowReconciler{Client: c, APIReader: c}
	ctx := context.Background()
	ready, err := reconciler.quiesceTraceRollups(ctx, instance, "apps")
	if err != nil || ready {
		t.Fatalf("first quiesce = %v, %v", ready, err)
	}
	if err := c.Get(ctx, types.NamespacedName{Name: cj.Name, Namespace: cj.Namespace}, cj); err != nil {
		t.Fatal(err)
	}
	if cj.Spec.Suspend == nil || !*cj.Spec.Suspend {
		t.Fatal("CronJob not suspended")
	}
	ready, err = reconciler.quiesceTraceRollups(ctx, instance, "apps")
	if err != nil || ready {
		t.Fatal("pending Job must block migration")
	}
	job.Status.Succeeded = 1
	job.Status.Conditions = []batchv1.JobCondition{{Type: batchv1.JobComplete, Status: corev1.ConditionTrue}}
	if err := c.Status().Update(ctx, job); err != nil {
		t.Fatal(err)
	}
	ready, err = reconciler.quiesceTraceRollups(ctx, instance, "apps")
	if err != nil || !ready {
		t.Fatalf("finished Job must allow migration: %v, %v", ready, err)
	}
	if err := reconciler.cleanupTraceRollups(ctx, instance, "apps"); err != nil {
		t.Fatal(err)
	}
	if err := c.Get(ctx, types.NamespacedName{Name: cj.Name, Namespace: cj.Namespace}, cj); err == nil {
		t.Fatal("disabled CronJob not deleted")
	}
}

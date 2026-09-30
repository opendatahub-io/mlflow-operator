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
	"strings"
	"testing"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
)

func TestTraceRollupsInvalidSchedule(t *testing.T) {
	for _, tc := range []struct {
		name, field string
		spec        mlflowv1.TraceRollupsSpec
	}{
		{name: "invalid hour", field: "schedule", spec: mlflowv1.TraceRollupsSpec{Schedule: ptr("0 25 * * *")}},
		{name: "descriptor", field: "schedule", spec: mlflowv1.TraceRollupsSpec{Schedule: ptr("@daily")}},
		{name: "seconds field", field: "schedule", spec: mlflowv1.TraceRollupsSpec{Schedule: ptr("0 0 2 * * *")}},
		{name: "zero step", field: "schedule", spec: mlflowv1.TraceRollupsSpec{Schedule: ptr("*/0 * * * *")}},
		{name: "embedded timezone", field: "schedule", spec: mlflowv1.TraceRollupsSpec{Schedule: ptr("TZ=UTC 0 2 * * *")}},
		{name: "tab timezone prefix", field: "schedule", spec: mlflowv1.TraceRollupsSpec{Schedule: ptr("TZ=UTC\t0\t2\t*\t*")}},
		{name: "empty schedule", field: "schedule", spec: mlflowv1.TraceRollupsSpec{Schedule: ptr("")}},
		{name: "unknown timezone", field: "timeZone", spec: mlflowv1.TraceRollupsSpec{TimeZone: ptr("Mars/Crater")}},
		{name: "local timezone", field: "timeZone", spec: mlflowv1.TraceRollupsSpec{TimeZone: ptr("Local")}},
		{name: "empty timezone", field: "timeZone", spec: mlflowv1.TraceRollupsSpec{TimeZone: ptr("")}},
		{name: "empty timezone component", field: "timeZone", spec: mlflowv1.TraceRollupsSpec{TimeZone: ptr("America//New_York")}},
		{name: "relative timezone component", field: "timeZone", spec: mlflowv1.TraceRollupsSpec{TimeZone: ptr("America/./New_York")}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			instance := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr("postgresql://host/db"),
					Migration:       &mlflowv1.MLflowMigrationConfig{Mode: mlflowv1.MLflowMigrateAlways},
					TraceRollups:    &tc.spec,
				},
			}
			objects, err := NewHelmRenderer("../../charts/mlflow").RenderChart(instance, "apps", RenderOptions{}, nil)
			if err == nil || !strings.Contains(err.Error(), "traceRollups."+tc.field) {
				t.Fatalf("expected %s validation error before migration, got %v", tc.field, err)
			}
			if len(objects) != 0 {
				t.Fatal("invalid timing must not return objects for migration or apply")
			}
		})
	}

	selector := &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "db"}, Key: "uri"}
	for _, tc := range []struct {
		name     string
		uri      *string
		secret   *corev1.SecretKeySelector
		disabled bool
		opts     RenderOptions
	}{
		{name: "inline SQL opt out", uri: ptr("postgresql://host/db"), disabled: true},
		{name: "Secret opt out", secret: selector, disabled: true},
		{name: "inline SQLite", uri: ptr("sqlite:////mlflow/mlflow.db")},
		{name: "inline SQLite driver", uri: ptr("sqlite+pysqlite:////mlflow/mlflow.db")},
		{name: "resolved Secret SQLite", secret: selector, opts: RenderOptions{TraceRollupsDisabled: true}},
		{name: "omitted backend"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			instance := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:     tc.uri,
					BackendStoreURIFrom: tc.secret,
					TraceRollups: &mlflowv1.TraceRollupsSpec{
						Enabled: ptr(!tc.disabled), Schedule: ptr("0 25 * * *"), TimeZone: ptr("Mars/Crater"),
					},
				},
			}
			objects, err := NewHelmRenderer("../../charts/mlflow").RenderChart(instance, "apps", tc.opts, nil)
			if err != nil {
				t.Fatalf("unused timing must not block rendering: %v", err)
			}
			if findObject(objects, "CronJob", "mlflow-trace-rollups") != nil {
				t.Fatal("excluded backend or opt-out must not schedule rollups")
			}
		})
	}
}

func TestTraceRollupsIndependentTimingOverrides(t *testing.T) {
	for _, tc := range []struct {
		name, schedule, zone string
		spec                 *mlflowv1.TraceRollupsSpec
	}{
		{name: "defaults", schedule: "0 2 * * *", zone: "Etc/UTC"},
		{name: "schedule only", schedule: "*/15 1-5 * JAN MON-FRI", zone: "Etc/UTC", spec: &mlflowv1.TraceRollupsSpec{Schedule: ptr("*/15 1-5 * JAN MON-FRI")}},
		{name: "timezone only", schedule: "0 2 * * *", zone: "America/New_York", spec: &mlflowv1.TraceRollupsSpec{TimeZone: ptr("America/New_York")}},
		{name: "legacy timezone name", schedule: "0 2 * * *", zone: "Etc/GMT+5", spec: &mlflowv1.TraceRollupsSpec{TimeZone: ptr("Etc/GMT+5")}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			instance := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec:       mlflowv1.MLflowSpec{BackendStoreURI: ptr("postgresql://host/db"), TraceRollups: tc.spec},
			}
			objects, err := NewHelmRenderer("../../charts/mlflow").RenderChart(instance, "apps", RenderOptions{}, nil)
			if err != nil {
				t.Fatal(err)
			}
			obj := findObject(objects, "CronJob", "mlflow-trace-rollups")
			if obj == nil {
				t.Fatal("SQL backend must schedule rollups")
			}
			job := &batchv1.CronJob{}
			if err := runtime.DefaultUnstructuredConverter.FromUnstructured(obj.Object, job); err != nil {
				t.Fatal(err)
			}
			if job.Spec.Schedule != tc.schedule || job.Spec.TimeZone == nil || *job.Spec.TimeZone != tc.zone {
				t.Fatalf("unexpected timing: schedule=%q timeZone=%v", job.Spec.Schedule, job.Spec.TimeZone)
			}
		})
	}
}

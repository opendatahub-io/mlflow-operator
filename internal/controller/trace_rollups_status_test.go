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
	"testing"
	"time"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	batchv1 "k8s.io/api/batch/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func TestTraceRollupsStatusFollowsOwnedCronJob(t *testing.T) {
	ctx := context.Background()
	scheme := runtime.NewScheme()
	if err := mlflowv1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	if err := batchv1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	instance := &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: "mlflow", UID: types.UID("mlflow-uid")}}
	cronJob := &batchv1.CronJob{ObjectMeta: metav1.ObjectMeta{
		Name: "mlflow-trace-rollups", Namespace: "apps", UID: types.UID("cron-uid"),
		OwnerReferences: []metav1.OwnerReference{{APIVersion: "mlflow.opendatahub.io/v1", Kind: "MLflow", Name: "mlflow", UID: instance.UID, Controller: ptr(true)}},
	}}
	cl := fake.NewClientBuilder().WithScheme(scheme).WithStatusSubresource(instance, cronJob).WithObjects(instance, cronJob).Build()
	r := &MLflowReconciler{Client: cl, APIReader: cl}
	refresh := func() *mlflowv1.TraceRollupsStatus {
		t.Helper()
		if err := r.refreshTraceRollupsStatus(ctx, instance, "apps"); err != nil {
			t.Fatal(err)
		}
		current := &mlflowv1.MLflow{}
		if err := cl.Get(ctx, client.ObjectKeyFromObject(instance), current); err != nil {
			t.Fatal(err)
		}
		return current.Status.TraceRollups
	}
	if got := refresh(); got != nil {
		t.Fatalf("empty CronJob yielded status: %+v", got)
	}
	when := metav1.NewTime(time.Date(2026, 10, 14, 2, 0, 0, 0, time.UTC))
	cronJob.Status.LastScheduleTime = &when
	cronJob.Status.LastSuccessfulTime = &when
	if err := cl.Status().Update(ctx, cronJob); err != nil {
		t.Fatal(err)
	}
	if got := refresh(); got == nil || got.LastScheduleTime == nil || !got.LastScheduleTime.Equal(&when) {
		t.Fatalf("schedule time missing: %+v", got)
	}
	beforeNoop := &mlflowv1.MLflow{}
	if err := cl.Get(ctx, client.ObjectKeyFromObject(instance), beforeNoop); err != nil {
		t.Fatal(err)
	}
	if got := refresh(); got == nil || got.LastScheduleTime == nil {
		t.Fatalf("no-op refresh cleared status: %+v", got)
	}
	afterNoop := &mlflowv1.MLflow{}
	if err := cl.Get(ctx, client.ObjectKeyFromObject(instance), afterNoop); err != nil {
		t.Fatal(err)
	}
	if afterNoop.ResourceVersion != beforeNoop.ResourceVersion {
		t.Fatal("no-op refresh wrote status")
	}
	cronJob.Spec.Suspend = ptr(true)
	if err := cl.Update(ctx, cronJob); err != nil {
		t.Fatal(err)
	}
	if got := refresh(); got == nil || got.LastSuccessfulTime == nil || !got.LastSuccessfulTime.Equal(&when) {
		t.Fatalf("suspension lost CronJob history: %+v", got)
	}
	if err := cl.Delete(ctx, cronJob); err != nil {
		t.Fatal(err)
	}
	if got := refresh(); got != nil {
		t.Fatalf("deleted CronJob retained status: %+v", got)
	}
	cronJob.UID = types.UID("replacement")
	cronJob.ResourceVersion = ""
	cronJob.Status = batchv1.CronJobStatus{}
	if err := cl.Create(ctx, cronJob); err != nil {
		t.Fatal(err)
	}
	if got := refresh(); got != nil {
		t.Fatalf("new CronJob inherited old timestamps: %+v", got)
	}
	cronJob.OwnerReferences = nil
	if err := cl.Update(ctx, cronJob); err != nil {
		t.Fatal(err)
	}
	if got := refresh(); got != nil {
		t.Fatalf("unowned CronJob yielded status: %+v", got)
	}
}

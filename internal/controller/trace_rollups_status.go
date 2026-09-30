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
	"fmt"
	"reflect"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	batchv1 "k8s.io/api/batch/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/util/retry"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

// refreshTraceRollupsStatus copies only the current CronJob's timestamps. A
// read failure leaves the previous report intact for the next reconcile.
func (r *MLflowReconciler) refreshTraceRollupsStatus(ctx context.Context, mlflow *mlflowv1.MLflow, namespace string) error {
	reader := r.APIReader
	if reader == nil {
		reader = r.Client
	}
	cronJob := &batchv1.CronJob{}
	key := types.NamespacedName{Namespace: namespace, Name: "mlflow-trace-rollups" + getResourceSuffix(mlflow.Name)}
	err := reader.Get(ctx, key, cronJob)
	if err != nil && !apierrors.IsNotFound(err) {
		return fmt.Errorf("read trace rollup CronJob status: %w", err)
	}
	var observed *mlflowv1.TraceRollupsStatus
	if err == nil && metav1.IsControlledBy(cronJob, mlflow) &&
		(cronJob.Status.LastScheduleTime != nil || cronJob.Status.LastSuccessfulTime != nil) {
		observed = &mlflowv1.TraceRollupsStatus{
			LastScheduleTime:   cronJob.Status.LastScheduleTime.DeepCopy(),
			LastSuccessfulTime: cronJob.Status.LastSuccessfulTime.DeepCopy(),
		}
	}
	if reflect.DeepEqual(mlflow.Status.TraceRollups, observed) {
		return nil
	}
	return retry.RetryOnConflict(retry.DefaultRetry, func() error {
		latest := &mlflowv1.MLflow{}
		if err := r.Get(ctx, client.ObjectKeyFromObject(mlflow), latest); err != nil {
			return err
		}
		if reflect.DeepEqual(latest.Status.TraceRollups, observed) {
			mlflow.Status.TraceRollups = observed
			mlflow.ResourceVersion = latest.ResourceVersion
			return nil
		}
		latest.Status.TraceRollups = observed
		if err := r.Status().Update(ctx, latest); err != nil {
			return err
		}
		mlflow.Status.TraceRollups = observed
		mlflow.ResourceVersion = latest.ResourceVersion
		return nil
	})
}

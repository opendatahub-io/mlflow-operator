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

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

const traceRollupsInstanceLabel = "mlflow.opendatahub.io/trace-rollups-instance"

// Suspend scheduling before migration and wait for existing passes, including
// manually-created Jobs from the labeled template, to terminate.
func (r *MLflowReconciler) quiesceTraceRollups(ctx context.Context, mlflow *mlflowv1.MLflow, namespace string) (bool, error) {
	reader := r.APIReader
	if reader == nil {
		reader = r.Client
	}
	cronJob := &batchv1.CronJob{}
	key := types.NamespacedName{Namespace: namespace, Name: "mlflow-trace-rollups" + getResourceSuffix(mlflow.Name)}
	err := reader.Get(ctx, key, cronJob)
	if err != nil && !errors.IsNotFound(err) {
		return false, err
	}
	ownedCronJob := err == nil && metav1.IsControlledBy(cronJob, mlflow)
	if ownedCronJob && (cronJob.Spec.Suspend == nil || !*cronJob.Spec.Suspend) {
		before := cronJob.DeepCopy()
		suspend := true
		cronJob.Spec.Suspend = &suspend
		if err := r.Patch(ctx, cronJob, client.MergeFromWithOptions(before, client.MergeFromWithOptimisticLock{})); err != nil {
			return false, err
		}
		// Re-list jobs on a later reconcile after requesting suspension. A CronJob
		// controller pass already in flight can still create a Job, so the rollup
		// and migration processes also share a database advisory lock.
		return false, nil
	}
	jobs := &batchv1.JobList{}
	if err := reader.List(ctx, jobs, client.InNamespace(namespace)); err != nil {
		return false, err
	}
	for i := range jobs.Items {
		job := &jobs.Items[i]
		labeled := job.Labels[traceRollupsInstanceLabel] == ResourceName+getResourceSuffix(mlflow.Name)
		// Older templates omitted the instance label. Match their scheduled Jobs
		// only through the UID of this instance's current owned CronJob.
		owned := ownedCronJob && metav1.IsControlledBy(job, cronJob)
		if (labeled || owned) && !isTraceRollupsJobFinished(job) {
			return false, nil
		}
	}
	return true, nil
}

func isTraceRollupsJobFinished(job *batchv1.Job) bool {
	// A succeeded counter can precede termination of the Job's remaining pods.
	for _, condition := range job.Status.Conditions {
		if condition.Status == corev1.ConditionTrue && (condition.Type == batchv1.JobComplete || condition.Type == batchv1.JobFailed) {
			return true
		}
	}
	return false
}

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

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

func isTraceRollupsEnabled(mlflow *mlflowv1.MLflow) bool {
	if traceRollupsExplicitlyDisabled(mlflow) {
		return false
	}
	if mlflow.Spec.BackendStoreURIFrom != nil {
		return true
	}
	return mlflow.Spec.BackendStoreURI != nil && isRemoteSQLMetadataStoreURI(*mlflow.Spec.BackendStoreURI)
}

func traceRollupsExplicitlyDisabled(mlflow *mlflowv1.MLflow) bool {
	spec := mlflow.Spec.TraceRollups
	return spec != nil && spec.Enabled != nil && !*spec.Enabled
}

// traceRollupsSQLBackend reports whether rendering should enable SQL rollup use.
// Scheduling opt-out skips Secret resolution so cleanup works without credentials,
// but keeps the server flag: existing aggregates require it on restart. The runtime
// handles SQLite safely when an opted-out Secret's backend cannot be classified.
func (r *MLflowReconciler) traceRollupsSQLBackend(ctx context.Context, mlflow *mlflowv1.MLflow, namespace string) (bool, error) {
	if mlflow.Spec.BackendStoreURIFrom == nil {
		return mlflow.Spec.BackendStoreURI != nil && isRemoteSQLMetadataStoreURI(*mlflow.Spec.BackendStoreURI), nil
	}
	if traceRollupsExplicitlyDisabled(mlflow) {
		return true, nil
	}
	reader := r.APIReader
	if reader == nil {
		reader = r.Client
	}
	secret := &corev1.Secret{}
	selector := mlflow.Spec.BackendStoreURIFrom
	if err := reader.Get(ctx, types.NamespacedName{Namespace: namespace, Name: selector.Name}, secret); err != nil {
		return false, fmt.Errorf("resolve trace rollup backend Secret %s/%s: %w", namespace, selector.Name, err)
	}
	uri, ok := secret.Data[selector.Key]
	if !ok || len(uri) == 0 {
		return false, fmt.Errorf("trace rollup backend key %q is missing or empty in Secret %s/%s", selector.Key, namespace, selector.Name)
	}
	return isRemoteSQLMetadataStoreURI(string(uri)), nil
}

func (r *MLflowReconciler) cleanupTraceRollups(ctx context.Context, mlflow *mlflowv1.MLflow, namespace string) error {
	cronJob := &batchv1.CronJob{}
	key := types.NamespacedName{Namespace: namespace, Name: "mlflow-trace-rollups" + getResourceSuffix(mlflow.Name)}
	if err := r.Get(ctx, key, cronJob); err != nil {
		if errors.IsNotFound(err) {
			return nil
		}
		return err
	}
	if !metav1.IsControlledBy(cronJob, mlflow) {
		return nil
	}
	return client.IgnoreNotFound(r.Delete(ctx, cronJob))
}

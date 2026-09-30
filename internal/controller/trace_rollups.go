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
	"strings"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	batchv1 "k8s.io/api/batch/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

func isTraceRollupsEnabled(mlflow *mlflowv1.MLflow) bool {
	if traceRollupsExplicitlyDisabled(mlflow) {
		return false
	}
	if explicitEnv(mlflow, backendStoreEnvName) {
		return true // The resolved backend is supplied by reconciliation.
	}
	if mlflow.Spec.BackendStoreURIFrom != nil {
		return true
	}
	return mlflow.Spec.BackendStoreURI != nil && isRemoteSQLMetadataStoreURI(*mlflow.Spec.BackendStoreURI)
}

func traceRollupsExplicitlyDisabled(mlflow *mlflowv1.MLflow) bool {
	spec := mlflow.Spec.TraceRollups
	return spec != nil && spec.SchedulingEnabled != nil && !*spec.SchedulingEnabled
}

// traceRollupsSQLBackend reports whether rendering should enable SQL rollup use.
// Scheduling opt-out skips Secret resolution so cleanup works without credentials,
// but keeps the server flag: existing aggregates require it on restart. The runtime
// handles SQLite safely when an opted-out Secret's backend cannot be classified.
func (r *MLflowReconciler) traceRollupsSQLBackend(ctx context.Context, mlflow *mlflowv1.MLflow, namespace string) (bool, error) {
	uri, err := r.effectiveBackendURI(ctx, mlflow, namespace)
	if err != nil {
		return false, err
	}
	if isRemoteSQLMetadataStoreURI(uri) {
		return true, nil
	}
	if strings.HasPrefix(uri, "sqlite://") || strings.HasPrefix(uri, "sqlite+") {
		if mlflow.Spec.Storage == nil {
			return false, fmt.Errorf("spec.storage must be configured for the effective SQLite backend")
		}
		rwx := len(mlflow.Spec.Storage.AccessModes) > 0 && mlflow.Spec.Storage.AccessModes[0] == "ReadWriteMany"
		if mlflow.Spec.Replicas != nil && *mlflow.Spec.Replicas > 1 && !rwx {
			return false, fmt.Errorf("multiple tracking replicas with an effective SQLite backend require ReadWriteMany storage as the first access mode")
		}
		if isTraceArchivalEnabled(mlflow) && !rwx {
			return false, fmt.Errorf("trace archival with an effective SQLite backend requires ReadWriteMany storage as the first access mode")
		}
		return false, nil
	}
	return false, fmt.Errorf("effective backend URI must use a supported SQL metadata store scheme")
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

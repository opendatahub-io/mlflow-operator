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

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/validation"
	"sigs.k8s.io/controller-runtime/pkg/client"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

const secretResourceVersionsAnnotation = "mlflow.opendatahub.io/secret-resource-versions"
const configMapResourceVersionsAnnotation = "mlflow.opendatahub.io/configmap-resource-versions"

func referencedDeploymentConfigMapNames(mlflow *mlflowv1.MLflow) map[string]bool {
	names := map[string]bool{}
	for _, env := range mlflow.Spec.Env {
		if env.ValueFrom != nil && env.ValueFrom.ConfigMapKeyRef != nil && env.ValueFrom.ConfigMapKeyRef.Name != "" {
			names[env.ValueFrom.ConfigMapKeyRef.Name] = true
		}
	}
	for _, from := range mlflow.Spec.EnvFrom {
		if from.ConfigMapRef != nil && from.ConfigMapRef.Name != "" {
			names[from.ConfigMapRef.Name] = true
		}
	}
	return names
}

func (r *MLflowReconciler) referencedConfigMapToMLflowRequests(ctx context.Context, obj client.Object) []reconcile.Request {
	if obj.GetNamespace() != r.Namespace {
		return nil
	}
	mlflows := &mlflowv1.MLflowList{}
	if err := r.List(ctx, mlflows); err != nil {
		logf.FromContext(ctx).Error(err, "Failed to list MLflow instances for referenced ConfigMap watch")
		return nil
	}
	requests := make([]reconcile.Request, 0)
	for _, mlflow := range mlflows.Items {
		if referencedDeploymentConfigMapNames(&mlflow)[obj.GetName()] {
			requests = append(requests, reconcile.Request{NamespacedName: types.NamespacedName{Name: mlflow.Name, Namespace: mlflow.Namespace}})
		}
	}
	return requests
}

func (r *MLflowReconciler) referencedConfigMapResourceVersions(ctx context.Context, mlflow *mlflowv1.MLflow, namespace string) (map[string]string, error) {
	versions := map[string]string{}
	for name := range referencedDeploymentConfigMapNames(mlflow) {
		if errs := validation.IsDNS1123Subdomain(name); len(errs) > 0 {
			return nil, fmt.Errorf("invalid referenced ConfigMap name %q: %s", name, strings.Join(errs, ", "))
		}
		cm := &corev1.ConfigMap{}
		err := r.Get(ctx, types.NamespacedName{Namespace: namespace, Name: name}, cm)
		if errors.IsNotFound(err) {
			continue
		}
		if err != nil {
			return nil, fmt.Errorf("get referenced ConfigMap %q: %w", name, err)
		}
		versions[name] = cm.ResourceVersion
	}
	return versions, nil
}

// referencedDeploymentSecretNames returns the local Secrets consumed by the MLflow server pod.
// Keep this in sync with the Deployment chart; CronJob-only references intentionally do not roll
// the server Deployment.
func referencedDeploymentSecretNames(mlflow *mlflowv1.MLflow) map[string]bool {
	names := map[string]bool{TLSSecretName: true}

	for _, selector := range []*corev1.SecretKeySelector{
		mlflow.Spec.BackendStoreURIFrom,
		mlflow.Spec.ReadReplicaBackendStoreURIFrom,
		mlflow.Spec.RegistryStoreURIFrom,
	} {
		if selector != nil && selector.Name != "" {
			names[selector.Name] = true
		}
	}
	for _, env := range mlflow.Spec.Env {
		if env.ValueFrom != nil && env.ValueFrom.SecretKeyRef != nil && env.ValueFrom.SecretKeyRef.Name != "" {
			names[env.ValueFrom.SecretKeyRef.Name] = true
		}
	}
	for _, envFrom := range mlflow.Spec.EnvFrom {
		if envFrom.SecretRef != nil && envFrom.SecretRef.Name != "" {
			names[envFrom.SecretRef.Name] = true
		}
	}
	return names
}

// secretToMLflowRequests filters namespace-wide Secret events through cached MLflow CRs.
func (r *MLflowReconciler) secretToMLflowRequests(ctx context.Context, obj client.Object) []reconcile.Request {
	if obj.GetNamespace() != r.Namespace {
		return nil
	}

	mlflows := &mlflowv1.MLflowList{}
	if err := r.List(ctx, mlflows); err != nil {
		logf.FromContext(ctx).Error(err, "Failed to list MLflow instances for Secret watch")
		return nil
	}

	requests := make([]reconcile.Request, 0)
	for _, mlflow := range mlflows.Items {
		if referencedDeploymentSecretNames(&mlflow)[obj.GetName()] {
			requests = append(requests, reconcile.Request{NamespacedName: types.NamespacedName{
				Name:      mlflow.Name,
				Namespace: mlflow.Namespace,
			}})
		}
	}
	return requests
}

// referencedSecretResourceVersions returns pod Secret resource versions for Helm rendering.
// NotFound is tolerated so optional or asynchronously-created Secrets can later trigger a rollout.
func (r *MLflowReconciler) referencedSecretResourceVersions(
	ctx context.Context,
	mlflow *mlflowv1.MLflow,
	namespace string,
) (map[string]string, error) {
	secretNames := referencedDeploymentSecretNames(mlflow)
	for name := range secretNames {
		if errs := validation.IsDNS1123Subdomain(name); len(errs) > 0 {
			return nil, fmt.Errorf("invalid referenced Secret name %q: %s", name, strings.Join(errs, ", "))
		}
	}

	versions := make(map[string]string)
	for name := range secretNames {
		secret := &corev1.Secret{}
		err := r.Get(ctx, types.NamespacedName{Namespace: namespace, Name: name}, secret)
		if errors.IsNotFound(err) {
			continue
		}
		if err != nil {
			return nil, fmt.Errorf("get referenced Secret %q: %w", name, err)
		}
		versions[name] = secret.ResourceVersion
	}

	return versions, nil
}

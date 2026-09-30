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
	"k8s.io/apimachinery/pkg/types"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

type metadataStoreReference struct {
	field    string
	uri      *string
	selector *corev1.SecretKeySelector
	required bool
}

func isRemoteSQLMetadataStoreURI(uri string) bool {
	return strings.HasPrefix(uri, "postgresql://") ||
		strings.HasPrefix(uri, "postgresql+") ||
		strings.HasPrefix(uri, "mysql://") ||
		strings.HasPrefix(uri, "mysql+")
}

func (r *MLflowReconciler) validateArtifactsServerMetadataStores(
	ctx context.Context,
	mlflow *mlflowv1.MLflow,
	namespace string,
) error {
	if err := validateManagedEnv(mlflow); err != nil {
		return err
	}
	if !isArtifactsServerEnabled(mlflow) {
		return nil
	}
	backendURI, err := r.effectiveBackendURI(ctx, mlflow, namespace)
	if err != nil {
		return fmt.Errorf("resolve effective backend metadata store: %w", err)
	}
	if !isRemoteSQLMetadataStoreURI(backendURI) {
		return fmt.Errorf("effective backend metadata store must be remote PostgreSQL or MySQL when artifactsServer is enabled")
	}

	references := []metadataStoreReference{
		{field: "registryStoreUri", uri: mlflow.Spec.RegistryStoreURI, selector: mlflow.Spec.RegistryStoreURIFrom},
		{field: "readReplicaBackendStoreUri", uri: mlflow.Spec.ReadReplicaBackendStoreURI, selector: mlflow.Spec.ReadReplicaBackendStoreURIFrom},
	}
	secrets := make(map[string]*corev1.Secret)

	for _, reference := range references {
		var uri string
		switch {
		case reference.selector != nil:
			if r.APIReader == nil {
				return fmt.Errorf("validate spec.%sFrom: APIReader is not configured", reference.field)
			}
			secret, ok := secrets[reference.selector.Name]
			if !ok {
				secret = &corev1.Secret{}
				key := types.NamespacedName{Name: reference.selector.Name, Namespace: namespace}
				if err := r.APIReader.Get(ctx, key, secret); err != nil {
					return fmt.Errorf("resolve spec.%sFrom from Secret %s/%s: %w", reference.field, namespace, reference.selector.Name, err)
				}
				secrets[reference.selector.Name] = secret
			}
			value, ok := secret.Data[reference.selector.Key]
			if !ok {
				return fmt.Errorf("resolve spec.%sFrom: key %q not found in Secret %s/%s", reference.field, reference.selector.Key, namespace, reference.selector.Name)
			}
			uri = string(value)
		case reference.uri != nil:
			uri = *reference.uri
		default:
			if reference.required {
				return fmt.Errorf("spec.%s or spec.%sFrom must be set when artifactsServer is enabled", reference.field, reference.field)
			}
			continue
		}

		if !isRemoteSQLMetadataStoreURI(uri) {
			return fmt.Errorf("spec.%s must resolve to a remote PostgreSQL or MySQL URI when artifactsServer is enabled", reference.field)
		}
	}

	return nil
}

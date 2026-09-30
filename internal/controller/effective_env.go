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
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

const backendStoreEnvName = "MLFLOW_BACKEND_STORE_URI"
const rollupsRuntimeEnvName = "MLFLOW_SQL_TRACE_ROLLUPS_ENABLED"
const registryStoreEnvName = "MLFLOW_REGISTRY_STORE_URI"

// kubelet expands $(NAME) against envFrom and earlier explicit env entries.
// $$ quotes the following dollar sign, including a subsequent $(NAME).
func expandKubeEnv(value string, previous map[string]string) string {
	return expandKubeEnvLookup(value, func(name string) (string, bool) {
		resolved, found := previous[name]
		return resolved, found
	})
}

func expandKubeEnvLookup(value string, lookup func(string) (string, bool)) string {
	var out strings.Builder
	for i := 0; i < len(value); {
		if value[i] != '$' || i+1 == len(value) {
			out.WriteByte(value[i])
			i++
			continue
		}
		if value[i+1] == '$' {
			out.WriteByte('$')
			i += 2
			continue
		}
		if value[i+1] == '(' {
			end := strings.IndexByte(value[i+2:], ')')
			if end >= 0 {
				end += i + 2
				if resolved, ok := lookup(value[i+2 : end]); ok {
					out.WriteString(resolved)
				} else {
					out.WriteString(value[i : end+1])
				}
				i = end + 1
				continue
			}
		}
		out.WriteByte('$')
		i++
	}
	return out.String()
}

func validateManagedEnv(mlflow *mlflowv1.MLflow) error {
	seen := map[string]bool{}
	for _, variable := range mlflow.Spec.Env {
		if variable.Name != backendStoreEnvName && variable.Name != rollupsRuntimeEnvName {
			continue
		}
		if seen[variable.Name] {
			return fmt.Errorf("spec.env must contain at most one %s entry", variable.Name)
		}
		seen[variable.Name] = true
	}
	return nil
}

func explicitEnv(mlflow *mlflowv1.MLflow, name string) bool {
	for _, env := range mlflow.Spec.Env {
		if env.Name == name {
			return true
		}
	}
	return false
}

// effectiveEnvResolver follows kubelet's envFrom-first, explicit-env-in-order
// behavior for the values that affect reconciliation. Pod-only field/resource
// references cannot be classified before a pod exists and fail closed.
type effectiveEnvResolver struct {
	reader    client.Reader
	namespace string
}

func (r *MLflowReconciler) envResolver(namespace string) effectiveEnvResolver {
	reader := r.APIReader
	if reader == nil {
		reader = r.Client
	}
	return effectiveEnvResolver{reader: reader, namespace: namespace}
}

func (e effectiveEnvResolver) secret(ctx context.Context, name string, optional bool) (*corev1.Secret, bool, error) {
	if e.reader == nil {
		return nil, false, fmt.Errorf("API reader is not configured for referenced Secret %s/%s", e.namespace, name)
	}
	secret := &corev1.Secret{}
	err := e.reader.Get(ctx, types.NamespacedName{Namespace: e.namespace, Name: name}, secret)
	if optional && apierrors.IsNotFound(err) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, fmt.Errorf("read referenced Secret %s/%s: %w", e.namespace, name, err)
	}
	return secret, true, nil
}

func (e effectiveEnvResolver) configMap(ctx context.Context, name string, optional bool) (*corev1.ConfigMap, bool, error) {
	if e.reader == nil {
		return nil, false, fmt.Errorf("API reader is not configured for referenced ConfigMap %s/%s", e.namespace, name)
	}
	configMap := &corev1.ConfigMap{}
	err := e.reader.Get(ctx, types.NamespacedName{Namespace: e.namespace, Name: name}, configMap)
	if optional && apierrors.IsNotFound(err) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, fmt.Errorf("read referenced ConfigMap %s/%s: %w", e.namespace, name, err)
	}
	return configMap, true, nil
}

func optional(flag *bool) bool { return flag != nil && *flag }

// generatedMetadataEnv returns the operator-generated metadata entries that
// precede spec.env in the tracking Deployment. User env values can refer to
// these entries, so reconciliation must resolve them in the same order.
func generatedMetadataEnv(mlflow *mlflowv1.MLflow) []corev1.EnvVar {
	backendOverridden := explicitEnv(mlflow, backendStoreEnvName)
	backendURI := ""
	if mlflow.Spec.BackendStoreURIFrom == nil {
		if mlflow.Spec.BackendStoreURI != nil {
			backendURI = *mlflow.Spec.BackendStoreURI
		} else {
			backendURI = defaultBackendStoreURI
		}
	}

	env := make([]corev1.EnvVar, 0, 3)
	if !backendOverridden {
		if mlflow.Spec.BackendStoreURIFrom != nil {
			env = append(env, corev1.EnvVar{Name: backendStoreEnvName, ValueFrom: &corev1.EnvVarSource{SecretKeyRef: mlflow.Spec.BackendStoreURIFrom}})
		} else {
			env = append(env, corev1.EnvVar{Name: backendStoreEnvName, Value: backendURI})
		}
	}

	if mlflow.Spec.ReadReplicaBackendStoreURIFrom != nil {
		env = append(env, corev1.EnvVar{Name: readReplicaBackendStoreURIEnvName, ValueFrom: &corev1.EnvVarSource{SecretKeyRef: mlflow.Spec.ReadReplicaBackendStoreURIFrom}})
	} else if mlflow.Spec.ReadReplicaBackendStoreURI != nil {
		env = append(env, corev1.EnvVar{Name: readReplicaBackendStoreURIEnvName, Value: *mlflow.Spec.ReadReplicaBackendStoreURI})
	}

	registryURI := backendURI
	if backendOverridden {
		registryURI = ""
	}
	registryURIFrom := (*corev1.SecretKeySelector)(nil)
	if mlflow.Spec.RegistryStoreURIFrom != nil {
		registryURIFrom = mlflow.Spec.RegistryStoreURIFrom
	} else if mlflow.Spec.RegistryStoreURI != nil {
		registryURI = *mlflow.Spec.RegistryStoreURI
	} else if mlflow.Spec.BackendStoreURIFrom != nil && !backendOverridden {
		registryURIFrom = mlflow.Spec.BackendStoreURIFrom
	}
	if registryURIFrom != nil {
		env = append(env, corev1.EnvVar{Name: registryStoreEnvName, ValueFrom: &corev1.EnvVarSource{SecretKeyRef: registryURIFrom}})
	} else if registryURI != "" {
		env = append(env, corev1.EnvVar{Name: registryStoreEnvName, Value: registryURI})
	}

	return env
}

func (e effectiveEnvResolver) envFromValue(ctx context.Context, sources []corev1.EnvFromSource, name string) (string, bool, error) {
	for i := len(sources) - 1; i >= 0; i-- {
		source := sources[i]
		if !strings.HasPrefix(name, source.Prefix) {
			continue
		}
		key := strings.TrimPrefix(name, source.Prefix)
		switch {
		case source.SecretRef != nil:
			secret, found, err := e.secret(ctx, source.SecretRef.Name, optional(source.SecretRef.Optional))
			if err != nil {
				return "", false, err
			}
			if !found {
				continue
			}
			if value, ok := secret.Data[key]; ok {
				return string(value), true, nil
			}
		case source.ConfigMapRef != nil:
			cm, found, err := e.configMap(ctx, source.ConfigMapRef.Name, optional(source.ConfigMapRef.Optional))
			if err != nil {
				return "", false, err
			}
			if !found {
				continue
			}
			if value, ok := cm.Data[key]; ok {
				return value, true, nil
			}
		default:
			continue
		}
	}
	return "", false, nil
}

func (e effectiveEnvResolver) envValue(ctx context.Context, env corev1.EnvVar, previous map[string]string) (string, bool, error) {
	if env.ValueFrom == nil {
		return expandKubeEnv(env.Value, previous), true, nil
	}
	source := env.ValueFrom
	switch {
	case source.SecretKeyRef != nil:
		ref := source.SecretKeyRef
		secret, found, err := e.secret(ctx, ref.Name, optional(ref.Optional))
		if err != nil || !found {
			return "", found, err
		}
		value, ok := secret.Data[ref.Key]
		if !ok && optional(ref.Optional) {
			return "", false, nil
		}
		if !ok {
			return "", false, fmt.Errorf("referenced Secret %s/%s lacks key %q", e.namespace, ref.Name, ref.Key)
		}
		return string(value), true, nil
	case source.ConfigMapKeyRef != nil:
		ref := source.ConfigMapKeyRef
		cm, found, err := e.configMap(ctx, ref.Name, optional(ref.Optional))
		if err != nil || !found {
			return "", found, err
		}
		if value, ok := cm.Data[ref.Key]; ok {
			return value, true, nil
		}
		if optional(ref.Optional) {
			return "", false, nil
		}
		return "", false, fmt.Errorf("referenced ConfigMap %s/%s lacks key %q", e.namespace, ref.Name, ref.Key)
	default:
		return "$(POD_DERIVED)", true, nil
	}
}

func (e effectiveEnvResolver) resolve(ctx context.Context, mlflow *mlflowv1.MLflow, name string) (string, bool, error) {
	if err := validateManagedEnv(mlflow); err != nil {
		return "", false, err
	}
	entries := append(generatedMetadataEnv(mlflow), mlflow.Spec.Env...)
	return e.resolveBefore(ctx, mlflow, entries, name, len(entries))
}

// Resolve only the requested variable and earlier entries used by its expansion.
// An absent optional reference leaves the previous value (or envFrom) in place.
func (e effectiveEnvResolver) resolveBefore(ctx context.Context, mlflow *mlflowv1.MLflow, entries []corev1.EnvVar, name string, before int) (string, bool, error) {
	for i := before - 1; i >= 0; i-- {
		variable := entries[i]
		if variable.Name != name {
			continue
		}
		if variable.ValueFrom == nil {
			value, err := e.expandValue(ctx, mlflow, entries, variable.Value, i)
			return value, true, err
		}
		value, found, err := e.envValue(ctx, variable, nil)
		if err != nil || found {
			return value, found, err
		}
	}
	return e.envFromValue(ctx, mlflow.Spec.EnvFrom, name)
}

func (e effectiveEnvResolver) expandValue(ctx context.Context, mlflow *mlflowv1.MLflow, entries []corev1.EnvVar, value string, before int) (string, error) {
	var resolutionErr error
	result := expandKubeEnvLookup(value, func(name string) (string, bool) {
		if resolutionErr != nil {
			return "", false
		}
		resolved, found, err := e.resolveBefore(ctx, mlflow, entries, name, before)
		resolutionErr = err
		return resolved, found
	})
	return result, resolutionErr
}

func (r *MLflowReconciler) effectiveBackendURI(ctx context.Context, mlflow *mlflowv1.MLflow, namespace string) (string, error) {
	if err := validateManagedEnv(mlflow); err != nil {
		return "", err
	}
	value, found, err := r.envResolver(namespace).resolve(ctx, mlflow, backendStoreEnvName)
	if err != nil {
		return "", err
	}
	if !found || value == "" {
		if !explicitEnv(mlflow, backendStoreEnvName) && mlflow.Spec.BackendStoreURIFrom != nil {
			return "", fmt.Errorf("effective backend Secret reference is absent or empty")
		}
		return "", fmt.Errorf("effective backend env is absent or empty")
	}
	return value, nil
}

func (r *MLflowReconciler) effectiveRollupsRuntime(ctx context.Context, mlflow *mlflowv1.MLflow, namespace string, remoteSQL bool) (bool, error) {
	if explicitEnv(mlflow, rollupsRuntimeEnvName) {
		value, found, err := r.envResolver(namespace).resolve(ctx, mlflow, rollupsRuntimeEnvName)
		if err != nil {
			return false, err
		}
		if !found {
			return false, nil
		}
		switch {
		case strings.EqualFold(value, "true"), value == "1":
			return true, nil
		case strings.EqualFold(value, "false"), value == "0":
			return false, nil
		default:
			return false, fmt.Errorf("effective rollup runtime flag has an invalid boolean value")
		}
	}
	if mlflow.Spec.TraceRollups != nil && mlflow.Spec.TraceRollups.Enabled != nil {
		return *mlflow.Spec.TraceRollups.Enabled, nil
	}
	return remoteSQL, nil
}

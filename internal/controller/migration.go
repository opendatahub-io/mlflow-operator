/*
Copyright 2025.

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
	"fmt"
	"strings"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
)

// mlflowDeploymentName is the prefix used to identify the MLflow Deployment.
// The full name is "mlflow" + resourceSuffix (e.g. "mlflow", "mlflow-dev").
const mlflowDeploymentPrefix = ResourceName

// mlflowContainerName is the name of the main MLflow container in the Deployment.
const mlflowContainerName = ResourceName

// injectMigrationInitContainer injects a db-migration init container into the
// MLflow Deployment. This is an RHOAI-specific post-render mutation that runs
// `mlflow db fix-migration-gap` before the MLflow server starts.
//
// The init container is built by extracting configuration (image, backend store
// URI, CA bundle env vars, security context) from the already-rendered main
// "mlflow" container, so it stays in sync with the chart without duplicating logic.
//
// Safe to run on every startup: exits immediately if no migration gap is detected.
func injectMigrationInitContainer(objects []*unstructured.Unstructured) error {
	for _, obj := range objects {
		if obj.GetKind() != "Deployment" {
			continue
		}

		// Match by name: deployment is named "mlflow" or "mlflow-<suffix>"
		name := obj.GetName()
		if name != mlflowDeploymentPrefix && !strings.HasPrefix(name, mlflowDeploymentPrefix+"-") {
			continue
		}

		// Find the main "mlflow" container by name
		containers, found, err := unstructured.NestedSlice(obj.Object, "spec", "template", "spec", "containers")
		if err != nil {
			return fmt.Errorf("failed to read containers from Deployment %q: %w", name, err)
		}
		if !found || len(containers) == 0 {
			return fmt.Errorf("deployment %q has no containers", name)
		}

		var mainContainer map[string]interface{}
		for i, c := range containers {
			cMap, ok := c.(map[string]interface{})
			if !ok {
				return fmt.Errorf("container %d in Deployment %q is not a valid map", i, name)
			}
			if cMap["name"] == mlflowContainerName {
				mainContainer = cMap
				break
			}
		}
		if mainContainer == nil {
			return fmt.Errorf("container %q not found in Deployment %q", mlflowContainerName, name)
		}

		// Build the init container
		initContainer := map[string]interface{}{
			"name":    "db-migration",
			"image":   mainContainer["image"],
			"command": []interface{}{"mlflow"},
			"args":    []interface{}{"db", "fix-migration-gap"},
		}

		if pullPolicy, ok := mainContainer["imagePullPolicy"]; ok {
			initContainer["imagePullPolicy"] = pullPolicy
		}

		// Copy relevant env vars from the main container:
		// - MLFLOW_BACKEND_STORE_URI: required for the migration command
		// - SSL/CA env vars: needed for TLS connections to the database
		if envVars, found, _ := unstructured.NestedSlice(mainContainer, "env"); found {
			relevantEnvNames := map[string]bool{
				"MLFLOW_BACKEND_STORE_URI": true,
				"SSL_CERT_FILE":            true,
				"REQUESTS_CA_BUNDLE":       true,
				"PGSSLROOTCERT":            true,
				"PGSSLMODE":                true,
			}
			var initEnvVars []interface{}
			for i, env := range envVars {
				envMap, ok := env.(map[string]interface{})
				if !ok {
					return fmt.Errorf("env var %d in container %q is not a valid map", i, mlflowContainerName)
				}
				envName, _ := envMap["name"].(string)
				if relevantEnvNames[envName] {
					initEnvVars = append(initEnvVars, env)
				}
			}
			if len(initEnvVars) > 0 {
				initContainer["env"] = initEnvVars
			}
		}

		if envFrom, found, _ := unstructured.NestedSlice(mainContainer, "envFrom"); found {
			initContainer["envFrom"] = envFrom
		}

		// Copy relevant volume mounts from the main container:
		// - mlflow-storage: needed for SQLite backends
		// - combined-ca-bundle: needed for TLS connections
		if volumeMounts, found, _ := unstructured.NestedSlice(mainContainer, "volumeMounts"); found {
			relevantMounts := map[string]bool{
				"mlflow-storage":     true,
				"combined-ca-bundle": true,
			}
			var initVolumeMounts []interface{}
			for i, vm := range volumeMounts {
				vmMap, ok := vm.(map[string]interface{})
				if !ok {
					return fmt.Errorf("volume mount %d in container %q is not a valid map", i, mlflowContainerName)
				}
				mountName, _ := vmMap["name"].(string)
				if relevantMounts[mountName] {
					initVolumeMounts = append(initVolumeMounts, vm)
				}
			}
			if len(initVolumeMounts) > 0 {
				initContainer["volumeMounts"] = initVolumeMounts
			}
		}

		if secCtx, found, _ := unstructured.NestedMap(mainContainer, "securityContext"); found {
			initContainer["securityContext"] = secCtx
		}

		initContainer["resources"] = map[string]interface{}{
			"requests": map[string]interface{}{
				"cpu":    "100m",
				"memory": "128Mi",
			},
			"limits": map[string]interface{}{
				"cpu":    "500m",
				"memory": "256Mi",
			},
		}

		existingInitContainers, _, _ := unstructured.NestedSlice(obj.Object, "spec", "template", "spec", "initContainers")
		existingInitContainers = append(existingInitContainers, initContainer)

		if err := unstructured.SetNestedSlice(obj.Object, existingInitContainers, "spec", "template", "spec", "initContainers"); err != nil {
			return fmt.Errorf("failed to inject db-migration init container: %w", err)
		}
	}
	return nil
}

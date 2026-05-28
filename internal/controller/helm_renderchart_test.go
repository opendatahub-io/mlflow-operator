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
	"testing"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

// TestRenderChart tests the full helm chart rendering including YAML parsing.
func TestRenderChart(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")

	tests := []struct {
		name         string
		mlflow       *mlflowv1.MLflow
		namespace    string
		wantErr      bool
		validateObjs func(t *testing.T, objs []*unstructured.Unstructured)
	}{
		{
			name: "basic rendering should succeed",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Name: "test-mlflow",
				},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      ptr("sqlite:////mlflow/mlflow.db"),
					RegistryStoreURI:     ptr("sqlite:////mlflow/mlflow.db"),
					ArtifactsDestination: ptr("file:///mlflow/artifacts"),
				},
			},
			namespace: "test-ns",
			wantErr:   false,
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				if len(objs) == 0 {
					t.Fatal("expected rendered objects, got none")
				}

				foundDeployment := false
				for _, obj := range objs {
					if obj.GetKind() == deploymentKind {
						foundDeployment = true
					}
				}
				if !foundDeployment {
					t.Error("Deployment not found in rendered objects")
				}
			},
		},
		{
			name: "deployment should include static prefix in health probes",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Name: "test-mlflow",
				},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      ptr("sqlite:////mlflow/mlflow.db"),
					RegistryStoreURI:     ptr("sqlite:////mlflow/mlflow.db"),
					ArtifactsDestination: ptr("file:///mlflow/artifacts"),
				},
			},
			namespace: "test-ns",
			wantErr:   false,
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				foundDeployment := false
				for _, obj := range objs {
					if obj.GetKind() != deploymentKind {
						continue
					}
					foundDeployment = true

					containers, found, err := unstructured.NestedSlice(obj.Object, "spec", "template", "spec", "containers")
					if err != nil || !found || len(containers) == 0 {
						t.Fatalf("Failed to get containers from deployment: found=%v, err=%v", found, err)
					}

					container := containers[0].(map[string]interface{})
					expectedPath := StaticPrefix + "/health"

					livenessPath, found, err := unstructured.NestedString(container, "livenessProbe", "httpGet", "path")
					if err != nil || !found {
						t.Fatalf("Failed to get livenessProbe path: found=%v, err=%v", found, err)
					}
					if livenessPath != expectedPath {
						t.Errorf("livenessProbe path = %s, want %s", livenessPath, expectedPath)
					}

					readinessPath, found, err := unstructured.NestedString(container, "readinessProbe", "httpGet", "path")
					if err != nil || !found {
						t.Fatalf("Failed to get readinessProbe path: found=%v, err=%v", found, err)
					}
					if readinessPath != expectedPath {
						t.Errorf("readinessProbe path = %s, want %s", readinessPath, expectedPath)
					}
				}
				if !foundDeployment {
					t.Fatal("Deployment not found in rendered objects")
				}
			},
		},
		{
			name: "deployment should have allowed hosts configured",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Name: "test-mlflow",
				},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      ptr("sqlite:////mlflow/mlflow.db"),
					RegistryStoreURI:     ptr("sqlite:////mlflow/mlflow.db"),
					ArtifactsDestination: ptr("file:///mlflow/artifacts"),
				},
			},
			namespace: "test-ns",
			wantErr:   false,
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				foundDeployment := false
				for _, obj := range objs {
					if obj.GetKind() == deploymentKind {
						foundDeployment = true
						containers, found, err := unstructured.NestedSlice(obj.Object, "spec", "template", "spec", "containers")
						if err != nil || !found || len(containers) == 0 {
							t.Fatalf("Failed to get containers from deployment: found=%v, err=%v", found, err)
						}

						container := containers[0].(map[string]interface{})
						args, found, err := unstructured.NestedStringSlice(container, "args")
						if err != nil || !found {
							t.Fatalf("Failed to get args from container: found=%v, err=%v", found, err)
						}

						hasAllowedHosts := false
						for i, arg := range args {
							if arg == "--allowed-hosts" {
								hasAllowedHosts = true
								if i+1 >= len(args) {
									t.Error("--allowed-hosts flag present but missing hosts list value")
									break
								}
								hosts := args[i+1]
								if hosts == "" {
									t.Error("--allowed-hosts flag present but hosts list is empty")
								}
								t.Logf("Allowed hosts: %s", hosts)
								break
							}
						}
						if !hasAllowedHosts {
							t.Error("--allowed-hosts not found in deployment args")
						}

						staticPrefixArg := "--static-prefix=" + StaticPrefix
						hasStaticPrefixArg := false
						for _, arg := range args {
							if arg == staticPrefixArg {
								hasStaticPrefixArg = true
								break
							}
						}
						if !hasStaticPrefixArg {
							t.Errorf("%s not found in deployment args", staticPrefixArg)
						}
					}
				}
				if !foundDeployment {
					t.Fatal("Deployment not found in rendered objects")
				}
			},
		},
		{
			name: "RBAC resources should use static ClusterRole name and resourceSuffix for ClusterRoleBinding",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Name: "my-instance",
				},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      ptr("sqlite:////mlflow/mlflow.db"),
					RegistryStoreURI:     ptr("sqlite:////mlflow/mlflow.db"),
					ArtifactsDestination: ptr("file:///mlflow/artifacts"),
				},
			},
			namespace: "test-ns",
			wantErr:   false,
			validateObjs: func(t *testing.T, objs []*unstructured.Unstructured) {
				expectedSuffix := "-my-instance"
				expectedBindingName := "mlflow" + expectedSuffix
				// ClusterRole is static (shared across all instances)
				expectedClusterRoleName := "mlflow"

				foundClusterRole := false
				foundClusterRoleBinding := false

				for _, obj := range objs {
					switch obj.GetKind() {
					case "ClusterRole":
						foundClusterRole = true
						if obj.GetName() != expectedClusterRoleName {
							t.Errorf("ClusterRole name = %s, want %s (should be static, shared across all MLflow instances)", obj.GetName(), expectedClusterRoleName)
						}

						rules, found, err := unstructured.NestedSlice(obj.Object, "rules")
						if err != nil || !found {
							t.Fatalf("Failed to get ClusterRole rules: found=%v, err=%v", found, err)
						}

						foundArtifactSecretRule := false
						for _, rule := range rules {
							ruleMap, ok := rule.(map[string]interface{})
							if !ok {
								continue
							}

							resources, _, _ := unstructured.NestedStringSlice(ruleMap, "resources")
							resourceNames, _, _ := unstructured.NestedStringSlice(ruleMap, "resourceNames")
							if len(resources) == 1 && resources[0] == "secrets" &&
								len(resourceNames) == 1 && resourceNames[0] == "mlflow-artifact-connection" {
								foundArtifactSecretRule = true

								verbs, found, err := unstructured.NestedStringSlice(ruleMap, "verbs")
								if err != nil || !found {
									t.Fatalf("Failed to get secret rule verbs: found=%v, err=%v", found, err)
								}

								expectedVerbs := map[string]bool{"get": true, "list": true, "watch": true}
								for _, verb := range verbs {
									delete(expectedVerbs, verb)
								}
								if len(expectedVerbs) != 0 {
									t.Errorf("secret rule missing verbs: %v", expectedVerbs)
								}
							}
						}
						if !foundArtifactSecretRule {
							t.Error("ClusterRole secret rule for mlflow-artifact-connection not found")
						}
					case "ClusterRoleBinding":
						foundClusterRoleBinding = true
						if obj.GetName() != expectedBindingName {
							t.Errorf("ClusterRoleBinding name = %s, want %s (should include resourceSuffix)", obj.GetName(), expectedBindingName)
						}
						roleRef, found, err := unstructured.NestedString(obj.Object, "roleRef", "name")
						if err != nil || !found {
							t.Fatalf("Failed to get roleRef.name from ClusterRoleBinding: found=%v, err=%v", found, err)
						}
						if roleRef != expectedClusterRoleName {
							t.Errorf("ClusterRoleBinding roleRef.name = %s, want %s", roleRef, expectedClusterRoleName)
						}
					}
				}

				if !foundClusterRole {
					t.Error("ClusterRole not found in rendered objects")
				}
				if !foundClusterRoleBinding {
					t.Error("ClusterRoleBinding not found in rendered objects")
				}
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			objs, err := renderer.RenderChart(tt.mlflow, tt.namespace, RenderOptions{})
			if (err != nil) != tt.wantErr {
				t.Fatalf("RenderChart() error = %v, wantErr %v", err, tt.wantErr)
			}
			if !tt.wantErr && tt.validateObjs != nil {
				tt.validateObjs(t, objs)
			}
		})
	}
}

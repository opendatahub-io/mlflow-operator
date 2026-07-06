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

func TestIsSharedRBACObject(t *testing.T) {
	tests := []struct {
		name string
		obj  *unstructured.Unstructured
		want bool
	}{
		{
			name: "shared cluster role",
			obj: &unstructured.Unstructured{Object: map[string]interface{}{
				"apiVersion": "rbac.authorization.k8s.io/v1",
				"kind":       "ClusterRole",
				"metadata": map[string]interface{}{
					"name": ClusterRoleName,
				},
			}},
			want: true,
		},
		{
			name: "shared cluster role binding",
			obj: &unstructured.Unstructured{Object: map[string]interface{}{
				"apiVersion": "rbac.authorization.k8s.io/v1",
				"kind":       "ClusterRoleBinding",
				"metadata": map[string]interface{}{
					"name": ClusterRoleBindingName,
				},
			}},
			want: true,
		},
		{
			name: "gc cluster role is not shared",
			obj: &unstructured.Unstructured{Object: map[string]interface{}{
				"apiVersion": "rbac.authorization.k8s.io/v1",
				"kind":       "ClusterRole",
				"metadata": map[string]interface{}{
					"name": "mlflow-gc",
				},
			}},
			want: false,
		},
		{
			name: "namespaced role is not shared",
			obj: &unstructured.Unstructured{Object: map[string]interface{}{
				"apiVersion": "rbac.authorization.k8s.io/v1",
				"kind":       "Role",
				"metadata": map[string]interface{}{
					"name": ClusterRoleName,
				},
			}},
			want: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := isSharedRBACObject(tt.obj); got != tt.want {
				t.Fatalf("isSharedRBACObject() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestSharedRBACObjectToMLflowRequests(t *testing.T) {
	ownerRefs := []metav1.OwnerReference{
		{
			APIVersion: mlflowv1.GroupVersion.String(),
			Kind:       "MLflow",
			Name:       "mlflow-a",
		},
		{
			APIVersion: "apps/v1",
			Kind:       "Deployment",
			Name:       "ignored",
		},
		{
			APIVersion: mlflowv1.GroupVersion.String(),
			Kind:       "MLflow",
			Name:       "mlflow-b",
		},
	}

	obj := &unstructured.Unstructured{Object: map[string]interface{}{
		"apiVersion": "rbac.authorization.k8s.io/v1",
		"kind":       "ClusterRoleBinding",
		"metadata": map[string]interface{}{
			"name": ClusterRoleBindingName,
		},
	}}
	obj.SetOwnerReferences(ownerRefs)

	requests := sharedRBACObjectToMLflowRequests(obj, ClusterRoleBindingName)
	if len(requests) != 2 {
		t.Fatalf("sharedRBACObjectToMLflowRequests() len = %d, want 2", len(requests))
	}
	if requests[0].Name != "mlflow-a" || requests[1].Name != "mlflow-b" {
		t.Fatalf("sharedRBACObjectToMLflowRequests() = %#v, want names mlflow-a/mlflow-b", requests)
	}

	ignored := sharedRBACObjectToMLflowRequests(obj, ClusterRoleName+"-other")
	if len(ignored) != 0 {
		t.Fatalf("sharedRBACObjectToMLflowRequests() with mismatched name = %#v, want no requests", ignored)
	}

	gcObj := &unstructured.Unstructured{Object: map[string]interface{}{
		"apiVersion": "rbac.authorization.k8s.io/v1",
		"kind":       "ClusterRole",
		"metadata": map[string]interface{}{
			"name": GCClusterRBACName,
		},
	}}
	gcObj.SetOwnerReferences(ownerRefs[:1])
	gcRequests := sharedRBACObjectToMLflowRequests(gcObj, GCClusterRBACName)
	if len(gcRequests) != 1 || gcRequests[0].Name != "mlflow-a" {
		t.Fatalf("sharedRBACObjectToMLflowRequests() for GC = %#v, want single request for mlflow-a", gcRequests)
	}
}

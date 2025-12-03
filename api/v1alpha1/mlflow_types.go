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

package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// MLflowSpec defines the desired state of MLflow
type MLflowSpec struct {
	// foo is an example field of MLflow. Edit mlflow_types.go to remove/update
	// +optional
	Foo *string `json:"foo,omitempty"`
}

// MLflowStatus defines the observed state of MLflow.
type MLflowStatus struct {

	// conditions represent the current state of the MLflow resource.
	// Each condition has a unique type and reflects the status of a specific aspect of the resource.
	//
	// Standard condition types include:
	// - "Available": the resource is fully functional
	// - "Progressing": the resource is being created or updated
	// - "Degraded": the resource failed to reach or maintain its desired state
	//
	// The status of each condition is one of True, False, or Unknown.
	// +listType=map
	// +listMapKey=type
	// +optional
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:scope=Cluster
// +kubebuilder:validation:XValidation:rule="self.metadata.name == 'mlflow'",message="MLflow resource name must be 'mlflow'"

// MLflow is the Schema for the mlflows API
type MLflow struct {
	metav1.TypeMeta `json:",inline"`

	// metadata is a standard object metadata
	// +optional
	metav1.ObjectMeta `json:"metadata,omitzero"`

	// spec defines the desired state of MLflow
	// +required
	Spec MLflowSpec `json:"spec"`

	// status defines the observed state of MLflow
	// +optional
	Status MLflowStatus `json:"status,omitzero"`
}

// +kubebuilder:object:root=true

// MLflowList contains a list of MLflow
type MLflowList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitzero"`
	Items           []MLflow `json:"items"`
}

func init() {
	SchemeBuilder.Register(&MLflow{}, &MLflowList{})
}

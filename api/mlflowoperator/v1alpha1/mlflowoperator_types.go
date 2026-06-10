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

package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const (
	MLflowOperatorInstanceName = "default-mlflowoperator"
	MLflowOperatorKind         = "MLflowOperator"
)

// ComponentRelease represents the detailed status of a managed release.
// +kubebuilder:object:generate=true
type ComponentRelease struct {
	Name    string `json:"name"`
	Version string `json:"version,omitempty"`
	RepoURL string `json:"repoUrl,omitempty"`
}

// ComponentReleaseStatus tracks release metadata for the module.
// +kubebuilder:object:generate=true
type ComponentReleaseStatus struct {
	// +patchMergeKey=name
	// +patchStrategy=merge
	// +listType=map
	// +listMapKey=name
	Releases []ComponentRelease `json:"releases,omitempty"`
}

// GatewaySpec projects platform gateway data into the MLflow module.
// +kubebuilder:object:generate=true
type GatewaySpec struct {
	Domain string `json:"domain,omitempty"`
}

// MLflowOperatorCommonSpec captures platform-projected inputs for the MLflow module.
// +kubebuilder:object:generate=true
type MLflowOperatorCommonSpec struct {
	Gateway      *GatewaySpec `json:"gateway,omitempty"`
	GatewayName  string       `json:"gatewayName,omitempty"`
	SectionTitle string       `json:"sectionTitle,omitempty"`
}

// MLflowOperatorSpec defines the desired state of MLflowOperator.
// +kubebuilder:object:generate=true
type MLflowOperatorSpec struct {
	MLflowOperatorCommonSpec `json:",inline"`
}

// MLflowOperatorCommonStatus defines the shared observed state of MLflowOperator.
// +kubebuilder:object:generate=true
type MLflowOperatorCommonStatus struct {
	ComponentReleaseStatus `json:",inline"`
}

// MLflowOperatorStatus defines the observed state of MLflowOperator.
// +kubebuilder:object:generate=true
type MLflowOperatorStatus struct {
	Phase              string `json:"phase,omitempty"`
	ObservedGeneration int64  `json:"observedGeneration,omitempty"`
	// +listType=map
	// +listMapKey=type
	Conditions                 []metav1.Condition `json:"conditions,omitempty"`
	MLflowOperatorCommonStatus `json:",inline"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:scope=Cluster
// +kubebuilder:validation:XValidation:rule="self.metadata.name == 'default-mlflowoperator'",message="MLflowOperator name must be default-mlflowoperator"
// +kubebuilder:printcolumn:name="Ready",type=string,JSONPath=`.status.conditions[?(@.type=="Ready")].status`,description="Ready"
// +kubebuilder:printcolumn:name="Reason",type=string,JSONPath=`.status.conditions[?(@.type=="Ready")].reason`,description="Reason"

// MLflowOperator is the Schema for the MLflowOperators API.
type MLflowOperator struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   MLflowOperatorSpec   `json:"spec,omitempty"`
	Status MLflowOperatorStatus `json:"status,omitempty"`
}

func (c *MLflowOperator) GetConditions() []metav1.Condition {
	return append([]metav1.Condition(nil), c.Status.Conditions...)
}

func (c *MLflowOperator) SetConditions(conditions []metav1.Condition) {
	c.Status.Conditions = append(conditions[:0:0], conditions...)
}

// +kubebuilder:object:root=true

// MLflowOperatorList contains a list of MLflowOperator.
type MLflowOperatorList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []MLflowOperator `json:"items"`
}

func init() {
	SchemeBuilder.Register(&MLflowOperator{}, &MLflowOperatorList{})
}

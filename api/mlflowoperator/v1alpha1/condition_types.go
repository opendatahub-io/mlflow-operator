package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// ConditionSeverity intentionally mirrors the ODH platform operator condition
// severity contract so the shared MLflowOperator CR stays wire-compatible
// during the handoff rollout.
type ConditionSeverity string

const (
	ConditionSeverityError ConditionSeverity = ""
	ConditionSeverityInfo  ConditionSeverity = "Info"
)

// Condition intentionally mirrors opendatahub-operator's common.Condition so
// platform status writers can safely update the shared MLflowOperator CR.
// +kubebuilder:object:generate=true
type Condition struct {
	// type of condition in CamelCase or in foo.example.com/CamelCase.
	//
	// +required
	// +kubebuilder:validation:Required
	// +kubebuilder:validation:Pattern=`^([a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*/)?(([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])$`
	// +kubebuilder:validation:MaxLength=316
	Type string `json:"type"`

	// status of the condition, one of True, False, Unknown.
	// +required
	// +kubebuilder:validation:Required
	// +kubebuilder:validation:Enum=True;False;Unknown
	Status metav1.ConditionStatus `json:"status"`

	// observedGeneration represents the .metadata.generation that the condition was set based upon.
	// For instance, if .metadata.generation is currently 12, but the .status.conditions[x].observedGeneration
	// is 9, the condition is out of date with respect to the current state of the instance.
	//
	// +optional
	// +kubebuilder:validation:Optional
	// +kubebuilder:validation:Minimum=0
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// lastTransitionTime is the last time the condition transitioned from one status to another.
	// This should be when the underlying condition changed.
	// If that is not known, then using the time when the API field changed is acceptable.
	//
	// +optional
	// +kubebuilder:validation:Optional
	// +kubebuilder:validation:Type=string
	// +kubebuilder:validation:Format=date-time
	LastTransitionTime metav1.Time `json:"lastTransitionTime"`

	// reason contains a programmatic identifier indicating the reason for the condition's last transition.
	// The value should be a CamelCase string.
	//
	// +optional
	// +kubebuilder:validation:Optional
	Reason string `json:"reason,omitempty"`

	// message is a human-readable message indicating details about the transition.
	// +optional
	// +kubebuilder:validation:Optional
	Message string `json:"message,omitempty"`

	// Severity with which to treat failures of this type of condition.
	// When this is not specified, it defaults to Error.
	// +optional
	// +kubebuilder:validation:Optional
	Severity ConditionSeverity `json:"severity,omitempty"`

	// The last time we got an update on a given condition, this should not be set and is
	// present only for backward compatibility reasons
	//
	// +optional
	// +kubebuilder:validation:Optional
	LastHeartbeatTime *metav1.Time `json:"lastHeartbeatTime,omitempty"`
}

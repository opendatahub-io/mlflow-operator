package controller

import (
	modulev1alpha1 "github.com/opendatahub-io/mlflow-operator/api/mlflowoperator/v1alpha1"
)

func findModuleStatusCondition(
	conditions []modulev1alpha1.Condition,
) *modulev1alpha1.Condition {
	for i := range conditions {
		if conditions[i].Type == readyConditionType {
			return &conditions[i]
		}
	}

	return nil
}

func setModuleStatusCondition(
	conditions *[]modulev1alpha1.Condition,
	newCondition modulev1alpha1.Condition,
) {
	for i := range *conditions {
		existing := &(*conditions)[i]
		if existing.Type != newCondition.Type {
			continue
		}

		if existing.Status == newCondition.Status &&
			existing.Reason == newCondition.Reason &&
			existing.Message == newCondition.Message &&
			existing.ObservedGeneration == newCondition.ObservedGeneration &&
			existing.Severity == newCondition.Severity {
			newCondition.LastTransitionTime = existing.LastTransitionTime
			newCondition.LastHeartbeatTime = existing.LastHeartbeatTime
		}

		(*conditions)[i] = newCondition
		return
	}

	*conditions = append(*conditions, newCondition)
}

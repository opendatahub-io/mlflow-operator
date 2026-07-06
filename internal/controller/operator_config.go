package controller

import (
	"context"
	"fmt"
	"strings"
	"time"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	apimeta "k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"

	modulev1alpha1 "github.com/opendatahub-io/mlflow-operator/api/mlflowoperator/v1alpha1"
	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	"github.com/opendatahub-io/mlflow-operator/internal/config"
)

const mlflowOperatorReadyConditionType = "MLflowOperatorReady"

// resolveOperatorConfig keeps the legacy env/flag path as the base configuration
// and only overlays module-CR state when the new controller handoff is enabled.
// Operand namespace targeting stays deployment-scoped through startup config so
// cache and RBAC scope continue to match the reconciler's live target namespace.
// Readiness checks for the singleton MLflowOperator CR happen separately.
func (r *MLflowReconciler) resolveOperatorConfig(ctx context.Context) (*config.OperatorConfig, error) {
	return r.resolveOperatorConfigFromBase(ctx, config.GetConfig())
}

func (r *MLflowReconciler) resolveOperatorConfigFromBase(
	ctx context.Context,
	baseConfig *config.OperatorConfig,
) (*config.OperatorConfig, error) {
	base := *baseConfig
	if !base.EnableMLflowOperatorModuleController {
		base.ApplicationsNamespace = r.Namespace
		return &base, nil
	}

	if base.ApplicationsNamespace == "" {
		base.ApplicationsNamespace = r.Namespace
	}

	module, err := r.getMLflowOperatorModule(ctx)
	if err != nil {
		return nil, err
	}
	if module == nil {
		return &base, nil
	}

	if module.Spec.GatewayName != "" {
		base.GatewayName = module.Spec.GatewayName
	}
	if module.Spec.SectionTitle != "" {
		base.SectionTitle = module.Spec.SectionTitle
	}
	if module.Spec.Gateway != nil && module.Spec.Gateway.Domain != "" {
		base.MLflowURL = gatewayDomainToURL(module.Spec.Gateway.Domain)
		base.MLflowURLConfigured = true
	}

	return &base, nil
}

func (r *MLflowReconciler) ensureMLflowOperatorReady(
	ctx context.Context,
	mlflow *mlflowv1.MLflow,
	cfg *config.OperatorConfig,
) (ctrl.Result, bool, error) {
	if cfg == nil || !cfg.EnableMLflowOperatorModuleController {
		apimeta.RemoveStatusCondition(&mlflow.Status.Conditions, mlflowOperatorReadyConditionType)
		return ctrl.Result{}, false, nil
	}

	module, err := r.getMLflowOperatorModule(ctx)
	if err != nil {
		return ctrl.Result{}, true, err
	}

	ready, reason, message := moduleDependencyStatus(module)
	if ready {
		setMLflowOperatorDependencyCondition(
			mlflow,
			metav1.ConditionTrue,
			readyReason,
			"MLflowOperator is ready to manage MLflow custom resources",
		)
		return ctrl.Result{}, false, nil
	}

	setMLflowOperatorDependencyCondition(mlflow, metav1.ConditionFalse, reason, message)
	if !mlflowOperatorDeletionBlocked(module, reason) {
		apimeta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
			Type:    "Available",
			Status:  metav1.ConditionFalse,
			Reason:  reason,
			Message: message,
		})
		apimeta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
			Type:    "Progressing",
			Status:  metav1.ConditionTrue,
			Reason:  reason,
			Message: message,
		})
	}
	if err := r.updateStatus(ctx, mlflow); err != nil {
		return ctrl.Result{}, true, err
	}

	return ctrl.Result{RequeueAfter: 10 * time.Second}, true, nil
}

func setMLflowOperatorDependencyCondition(
	mlflow *mlflowv1.MLflow,
	status metav1.ConditionStatus,
	reason, message string,
) {
	condition := metav1.Condition{
		Type:    mlflowOperatorReadyConditionType,
		Status:  status,
		Reason:  reason,
		Message: message,
	}
	condition.ObservedGeneration = mlflow.Generation
	apimeta.SetStatusCondition(&mlflow.Status.Conditions, condition)
}

func mlflowOperatorDeletionBlocked(module *modulev1alpha1.MLflowOperator, reason string) bool {
	return module != nil && !module.DeletionTimestamp.IsZero() && reason == mlflowInstancesReason
}

func (r *MLflowReconciler) getMLflowOperatorModule(ctx context.Context) (*modulev1alpha1.MLflowOperator, error) {
	module := &modulev1alpha1.MLflowOperator{}
	if err := r.Get(ctx, types.NamespacedName{Name: modulev1alpha1.MLflowOperatorInstanceName}, module); err != nil {
		if apierrors.IsNotFound(err) {
			return nil, nil
		}
		return nil, err
	}

	return module, nil
}

func moduleDependencyStatus(module *modulev1alpha1.MLflowOperator) (bool, string, string) {
	if module == nil {
		return false, "MLflowOperatorMissing", "Waiting for MLflowOperator custom resource to be created before reconciling MLflow custom resources"
	}
	if module.Status.ObservedGeneration > 0 && module.Status.ObservedGeneration < module.Generation {
		return false, "MLflowOperatorStatusStale", fmt.Sprintf(
			"Waiting for MLflowOperator status to observe generation %d before reconciling MLflow custom resources",
			module.Generation,
		)
	}

	readyCondition := findModuleStatusCondition(module.Status.Conditions)
	if readyCondition == nil {
		return false, "MLflowOperatorNotReady", "Waiting for MLflowOperator to report Ready before reconciling MLflow custom resources"
	}
	if readyCondition.Status != metav1.ConditionTrue {
		reason := readyCondition.Reason
		if reason == "" {
			reason = "MLflowOperatorNotReady"
		}
		message := "Waiting for MLflowOperator to become ready before reconciling MLflow custom resources"
		if readyCondition.Message != "" {
			message = fmt.Sprintf("%s: %s", message, readyCondition.Message)
		}
		return false, reason, message
	}

	return true, readyCondition.Reason, readyCondition.Message
}

func gatewayDomainToURL(domain string) string {
	trimmed := strings.TrimSpace(strings.TrimRight(domain, "/"))
	if trimmed == "" {
		return ""
	}
	if strings.HasPrefix(trimmed, "http://") || strings.HasPrefix(trimmed, "https://") {
		return trimmed
	}
	return "https://" + trimmed
}

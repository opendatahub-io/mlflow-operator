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

	"github.com/Masterminds/semver/v3"
	corev1 "k8s.io/api/core/v1"
	apiequality "k8s.io/apimachinery/pkg/api/equality"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	controllerbuilder "sigs.k8s.io/controller-runtime/pkg/builder"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/predicate"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	modulev1alpha1 "github.com/opendatahub-io/mlflow-operator/api/mlflowoperator/v1alpha1"
	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

const (
	mlflowOperatorFinalizer = "mlflow.opendatahub.io/mlflow-operator-protection"
	readyConditionType      = "Ready"
	readyReason             = "Ready"
	configPendingReason     = "ConfigPending"
	mlflowInstancesReason   = "MLflowInstancesPresent"
	phaseProgressing        = "Progressing"
	phaseReady              = "Ready"
	platformConfigMapName   = "odh-mlflowoperator-config"
	platformVersionKey      = "platformVersion"
	platformReleaseName     = "platform"
	mlflowReleaseName       = "MLflow"
	mlflowRepoURL           = "https://github.com/mlflow/mlflow"
	maxReleaseVersionLength = 64
)

// MLflowOperatorReconciler reconciles the singleton MLflowOperator module CR.
type MLflowOperatorReconciler struct {
	client.Client
	Scheme                *runtime.Scheme
	ApplicationsNamespace string
}

// +kubebuilder:rbac:groups=components.platform.opendatahub.io,resources=mlflowoperators,verbs=get;list;watch;update;patch
// +kubebuilder:rbac:groups=components.platform.opendatahub.io,resources=mlflowoperators/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=components.platform.opendatahub.io,resources=mlflowoperators/finalizers,verbs=update
// +kubebuilder:rbac:groups=mlflow.opendatahub.io,resources=mlflows,verbs=get;list;watch

func (r *MLflowOperatorReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := logf.FromContext(ctx)

	module := &modulev1alpha1.MLflowOperator{}
	if err := r.Get(ctx, req.NamespacedName, module); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}

	if module.DeletionTimestamp.IsZero() {
		if !controllerutil.ContainsFinalizer(module, mlflowOperatorFinalizer) {
			controllerutil.AddFinalizer(module, mlflowOperatorFinalizer)
			if err := r.Update(ctx, module); err != nil {
				return ctrl.Result{}, fmt.Errorf("add MLflowOperator finalizer: %w", err)
			}
			return ctrl.Result{}, nil
		}

		if missing := missingRequiredModuleConfig(module); len(missing) > 0 {
			message := fmt.Sprintf(
				"Waiting for MLflowOperator spec fields before managing MLflow custom resources: %s",
				strings.Join(missing, ", "),
			)
			if err := r.updateModuleStatus(ctx, module, metav1.ConditionFalse, configPendingReason, message); err != nil {
				return ctrl.Result{}, err
			}
			return ctrl.Result{}, nil
		}

		if err := r.updateModuleStatus(ctx, module, metav1.ConditionTrue, readyReason, "MLflowOperator is ready to manage MLflow custom resources"); err != nil {
			return ctrl.Result{}, err
		}
		return ctrl.Result{}, nil
	}

	if !controllerutil.ContainsFinalizer(module, mlflowOperatorFinalizer) {
		return ctrl.Result{}, nil
	}

	mlflowList := &mlflowv1.MLflowList{}
	if err := r.List(ctx, mlflowList); err != nil {
		return ctrl.Result{}, fmt.Errorf("list MLflow instances: %w", err)
	}

	if len(mlflowList.Items) > 0 {
		message := fmt.Sprintf("cannot delete MLflowOperator while %d MLflow instance(s) still exist", len(mlflowList.Items))
		if err := r.updateModuleStatus(ctx, module, metav1.ConditionFalse, mlflowInstancesReason, message); err != nil {
			return ctrl.Result{}, err
		}
		log.Info("blocking MLflowOperator deletion until MLflow instances are removed", "count", len(mlflowList.Items))
		return ctrl.Result{}, nil
	}

	controllerutil.RemoveFinalizer(module, mlflowOperatorFinalizer)
	if err := r.Update(ctx, module); err != nil {
		return ctrl.Result{}, fmt.Errorf("remove MLflowOperator finalizer: %w", err)
	}

	return ctrl.Result{}, nil
}

func (r *MLflowOperatorReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&modulev1alpha1.MLflowOperator{}).
		Watches(
			&mlflowv1.MLflow{},
			handler.EnqueueRequestsFromMapFunc(func(context.Context, client.Object) []reconcile.Request {
				return []reconcile.Request{{
					NamespacedName: types.NamespacedName{Name: modulev1alpha1.MLflowOperatorInstanceName},
				}}
			}),
		).
		Watches(
			&corev1.ConfigMap{},
			handler.EnqueueRequestsFromMapFunc(r.platformConfigToMLflowOperatorRequests),
			controllerbuilder.WithPredicates(predicate.NewPredicateFuncs(func(obj client.Object) bool {
				return obj.GetName() == platformConfigMapName
			})),
		).
		Complete(r)
}

func (r *MLflowOperatorReconciler) updateModuleStatus(
	ctx context.Context,
	module *modulev1alpha1.MLflowOperator,
	status metav1.ConditionStatus,
	reason, message string,
) error {
	updated := module.DeepCopy()
	updated.Status.Phase = phaseForReadyCondition(status)
	updated.Status.ObservedGeneration = updated.Generation
	releases, err := r.desiredModuleReleases(ctx)
	if err != nil {
		return err
	}
	updated.Status.Releases = releases
	setModuleStatusCondition(&updated.Status.Conditions, modulev1alpha1.Condition{
		Type:               readyConditionType,
		Status:             status,
		Reason:             reason,
		Message:            message,
		ObservedGeneration: updated.Generation,
		LastTransitionTime: metav1.Now(),
	})

	if apiequality.Semantic.DeepEqual(updated.Status, module.Status) {
		return nil
	}

	if err := r.Status().Update(ctx, updated); err != nil {
		return fmt.Errorf("update MLflowOperator status: %w", err)
	}

	module.Status = updated.Status
	return nil
}

func (r *MLflowOperatorReconciler) desiredModuleReleases(ctx context.Context) ([]modulev1alpha1.ComponentRelease, error) {
	releases := make([]modulev1alpha1.ComponentRelease, 0, 2)
	if SupportedMLflowVersion != "" {
		releases = append(releases, modulev1alpha1.ComponentRelease{
			Name:    mlflowReleaseName,
			Version: SupportedMLflowVersion,
			RepoURL: mlflowRepoURL,
		})
	}

	appsNamespace := r.ApplicationsNamespace
	if appsNamespace == "" {
		return releases, nil
	}

	cm := &corev1.ConfigMap{}
	if err := r.Get(ctx, types.NamespacedName{
		Name:      platformConfigMapName,
		Namespace: appsNamespace,
	}, cm); err != nil {
		if apierrors.IsNotFound(err) {
			return releases, nil
		}
		return nil, fmt.Errorf("get platform config ConfigMap %s/%s: %w", appsNamespace, platformConfigMapName, err)
	}

	if platformVersion := cm.Data[platformVersionKey]; platformVersion != "" {
		if err := validateReleaseVersion(platformVersion); err != nil {
			return nil, fmt.Errorf("invalid platform version in %s/%s: %w", appsNamespace, platformConfigMapName, err)
		}
		releases = append([]modulev1alpha1.ComponentRelease{{
			Name:    platformReleaseName,
			Version: platformVersion,
		}}, releases...)
	}

	return releases, nil
}

func (r *MLflowOperatorReconciler) platformConfigToMLflowOperatorRequests(_ context.Context, obj client.Object) []reconcile.Request {
	appsNamespace := r.ApplicationsNamespace
	if appsNamespace == "" || obj.GetNamespace() != appsNamespace {
		return nil
	}

	return []reconcile.Request{{
		NamespacedName: types.NamespacedName{Name: modulev1alpha1.MLflowOperatorInstanceName},
	}}
}

func validateReleaseVersion(version string) error {
	if len(version) > maxReleaseVersionLength {
		return fmt.Errorf("release version length %d exceeds maximum %d", len(version), maxReleaseVersionLength)
	}
	if _, err := semver.NewVersion(strings.TrimPrefix(version, "v")); err != nil {
		return fmt.Errorf("parse semantic version %q: %w", version, err)
	}
	return nil
}

func phaseForReadyCondition(status metav1.ConditionStatus) string {
	if status == metav1.ConditionTrue {
		return phaseReady
	}
	return phaseProgressing
}

func missingRequiredModuleConfig(module *modulev1alpha1.MLflowOperator) []string {
	var missing []string
	if module.Spec.GatewayName == "" {
		missing = append(missing, "spec.gatewayName")
	}
	if module.Spec.SectionTitle == "" {
		missing = append(missing, "spec.sectionTitle")
	}
	return missing
}

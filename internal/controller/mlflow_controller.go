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
	"context"
	"fmt"
	"time"

	consolev1 "github.com/openshift/api/console/v1"
	monitoringv1 "github.com/prometheus-operator/prometheus-operator/pkg/apis/monitoring/v1"
	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/fields"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/util/retry"
	ctrl "sigs.k8s.io/controller-runtime"
	controllerbuilder "sigs.k8s.io/controller-runtime/pkg/builder"
	crcache "sigs.k8s.io/controller-runtime/pkg/cache"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/predicate"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
	"sigs.k8s.io/controller-runtime/pkg/source"
	gatewayv1 "sigs.k8s.io/gateway-api/apis/v1"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	"github.com/opendatahub-io/mlflow-operator/internal/config"
)

const (
	chartPath = "charts/mlflow"
)

// MLflowReconciler reconciles a MLflow object
type MLflowReconciler struct {
	client.Client
	Scheme                  *runtime.Scheme
	Namespace               string
	ChartPath               string
	ConsoleLinkAvailable    bool
	HTTPRouteAvailable      bool
	ServiceMonitorAvailable bool
	GCRBACWatchCache        crcache.Cache
}

// +kubebuilder:rbac:groups=mlflow.opendatahub.io,resources=mlflows,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=mlflow.opendatahub.io,resources=mlflows/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=mlflow.opendatahub.io,resources=mlflows/finalizers,verbs=update
// +kubebuilder:rbac:groups="",resources=secrets,resourceNames=mlflow-artifact-connection,verbs=get;list;watch
// +kubebuilder:rbac:groups=mlflow.kubeflow.org,resources=mlflowconfigs,verbs=get;list;watch
// +kubebuilder:rbac:groups="",resources=namespaces,verbs=get;list;watch
// Shared server RBAC objects are statically named `mlflow` and watched through metadata.name
// field selectors so list/watch remains compatible with resourceNames-scoped authorization.
// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=clusterroles,verbs=create
// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=clusterroles,resourceNames=mlflow,verbs=get;list;watch;update;patch;delete
// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=clusterrolebindings,verbs=create
// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=clusterrolebindings,resourceNames=mlflow,verbs=get;list;watch;update;patch;delete
// GC RBAC objects still use the chart suffix, but under the current singleton-only operator model
// the effective names remain `mlflow-gc`. Revisit these resourceNames when multi-instance support
// is added or when `mlflow gc` stops relying on artifact-proxy authorization.
// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=clusterroles,resourceNames=mlflow-gc,verbs=list;watch;update;patch;delete
// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=clusterrolebindings,resourceNames=mlflow-gc,verbs=list;watch;update;patch;delete
// +kubebuilder:rbac:groups=console.openshift.io,resources=consolelinks,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=gateway.networking.k8s.io,resources=httproutes,verbs=get;list;watch;create;update;patch;delete
//
// Namespace-scoped permissions (serviceaccounts, secrets, services, persistentvolumeclaims, deployments, networkpolicies)
// are granted via the Role in config/rbac/namespace_role.yaml instead of the ClusterRole above.
// This allows the operator to manage resources in target namespaces where MLflow instances are deployed.

// Reconcile is part of the main kubernetes reconciliation loop which aims to
// move the current state of the cluster closer to the desired state.
func (r *MLflowReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := logf.FromContext(ctx)

	// Fetch the MLflow instance
	mlflow := &mlflowv1.MLflow{}
	err := r.Get(ctx, req.NamespacedName, mlflow)
	if err != nil {
		if errors.IsNotFound(err) {
			log.Info("MLflow resource not found. Ignoring since object must be deleted")
			return ctrl.Result{}, nil
		}
		log.Error(err, "Failed to get MLflow")
		return ctrl.Result{}, err
	}

	// Use configured target namespace
	targetNamespace := r.Namespace
	cfg := config.GetConfig()
	mlflow.Status.Address = buildStatusAddress(mlflow.Name, targetNamespace)

	// Handle deletion - all resources are cleaned up via owner references
	if mlflow.GetDeletionTimestamp() != nil {
		return ctrl.Result{}, nil
	}

	// Clean up GC resources when garbage collection is disabled.
	if mlflow.Spec.GarbageCollection == nil {
		gcSuffix := "-gc" + getResourceSuffix(mlflow.Name)
		gcResources := []struct {
			obj  client.Object
			kind string
			name string
			ns   string
		}{
			{&batchv1.CronJob{}, "CronJob", ResourceName + gcSuffix, targetNamespace},
			{&corev1.ServiceAccount{}, "ServiceAccount", GCServiceAccountName, targetNamespace},
			{&rbacv1.ClusterRoleBinding{}, "ClusterRoleBinding", ResourceName + gcSuffix, ""},
			{&rbacv1.ClusterRole{}, "ClusterRole", ResourceName + gcSuffix, ""},
		}
		for _, res := range gcResources {
			existing := res.obj.DeepCopyObject().(client.Object)
			existing.SetName(res.name)
			existing.SetNamespace(res.ns)
			if err := r.Delete(ctx, existing); err != nil {
				if errors.IsNotFound(err) {
					continue
				}
				log.Error(err, "Failed to delete GC resource", "kind", res.kind, "name", res.name)
				return ctrl.Result{}, err
			}
			log.Info("Deleted GC resource", "kind", res.kind, "name", res.name)
		}
	}

	// Validate user-provided CA bundle ConfigMap if specified
	if mlflow.Spec.CABundleConfigMap != nil {
		customCABundleConfigMap := &corev1.ConfigMap{}
		err = r.Get(ctx, types.NamespacedName{
			Name:      mlflow.Spec.CABundleConfigMap.Name,
			Namespace: targetNamespace,
		}, customCABundleConfigMap)
		if err != nil {
			var msg string
			if errors.IsNotFound(err) {
				msg = fmt.Sprintf("CA bundle ConfigMap %q not found in namespace %q", mlflow.Spec.CABundleConfigMap.Name, targetNamespace)
			} else {
				msg = fmt.Sprintf("Failed to get CA bundle ConfigMap %q: %v", mlflow.Spec.CABundleConfigMap.Name, err)
			}
			log.Error(err, msg)
			meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
				Type:    "Available",
				Status:  metav1.ConditionFalse,
				Reason:  "CABundleConfigMapError",
				Message: msg,
			})
			if statusErr := r.Status().Update(ctx, mlflow); statusErr != nil {
				log.Error(statusErr, "Failed to update MLflow status")
			}
			return ctrl.Result{}, fmt.Errorf("%s", msg)
		}
		log.V(1).Info("Found custom CA bundle ConfigMap",
			"configmap", mlflow.Spec.CABundleConfigMap.Name,
			"namespace", targetNamespace)
	}

	// Check if platform CA bundle ConfigMap exists in target namespace
	platformCABundleExists := false
	platformCABundleConfigMap := &corev1.ConfigMap{}
	err = r.Get(ctx, types.NamespacedName{
		Name:      PlatformTrustedCABundleConfigMapName,
		Namespace: targetNamespace,
	}, platformCABundleConfigMap)
	if err == nil {
		// Platform CA bundle ConfigMap exists
		platformCABundleExists = true
		log.V(1).Info("Found platform CA bundle ConfigMap", "name", PlatformTrustedCABundleConfigMapName, "namespace", targetNamespace)
	} else if !errors.IsNotFound(err) {
		// Real error (not just ConfigMap NotFound) - this indicates a serious issue
		// like RBAC permissions or API server problems that the admin must fix
		msg := fmt.Sprintf("Failed to check for platform CA bundle ConfigMap %q: %v", PlatformTrustedCABundleConfigMapName, err)
		log.Error(err, msg)
		meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
			Type:    "Available",
			Status:  metav1.ConditionFalse,
			Reason:  "PlatformCABundleError",
			Message: msg,
		})
		if statusErr := r.Status().Update(ctx, mlflow); statusErr != nil {
			log.Error(statusErr, "Failed to update MLflow status")
		}
		return ctrl.Result{}, fmt.Errorf("%s", msg)
	}

	// Render the Helm chart
	helmChartPath := r.ChartPath
	if helmChartPath == "" {
		helmChartPath = chartPath
	}
	renderer := NewHelmRenderer(helmChartPath)
	renderOpts := RenderOptions{
		PlatformTrustedCABundleExists: platformCABundleExists,
		// If ConsoleLink is available, we can assume we are on OpenShift
		IsOpenShift:             r.ConsoleLinkAvailable,
		ServiceMonitorAvailable: r.ServiceMonitorAvailable,
	}
	objects, err := renderer.RenderChart(mlflow, targetNamespace, renderOpts)
	if err != nil {
		log.Error(err, "Failed to render Helm chart")
		meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
			Type:    "Available",
			Status:  metav1.ConditionFalse,
			Reason:  "RenderFailed",
			Message: fmt.Sprintf("Failed to render Helm chart: %v", err),
		})
		meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
			Type:    "Progressing",
			Status:  metav1.ConditionFalse,
			Reason:  "RenderFailed",
			Message: fmt.Sprintf("Failed to render Helm chart: %v", err),
		})
		if statusErr := r.updateStatus(ctx, mlflow); statusErr != nil {
			log.Error(statusErr, "Failed to update MLflow status after retries")
		}
		return ctrl.Result{}, err
	}

	if result, handled, err := r.handleMigration(ctx, mlflow, targetNamespace, objects); err != nil {
		log.Error(err, "Failed to reconcile migration")
		if statusErr := r.recordMigrationError(ctx, mlflow, "MigrationError", fmt.Sprintf("Failed to reconcile migration: %v", err)); statusErr != nil {
			log.Error(statusErr, "Failed to update MLflow status after retries")
		}
		return ctrl.Result{}, err
	} else if handled {
		return result, nil
	}

	if err := r.applyRenderedObjects(ctx, mlflow, objects); err != nil {
		log.Error(err, "Failed to apply rendered objects")
		meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
			Type:    "Available",
			Status:  metav1.ConditionFalse,
			Reason:  "ApplyFailed",
			Message: fmt.Sprintf("Failed to apply resources: %v", err),
		})
		meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
			Type:    "Progressing",
			Status:  metav1.ConditionFalse,
			Reason:  "ApplyFailed",
			Message: fmt.Sprintf("Failed to apply resources: %v", err),
		})
		if statusErr := r.updateStatus(ctx, mlflow); statusErr != nil {
			log.Error(statusErr, "Failed to update MLflow status after retries")
		}
		return ctrl.Result{}, err
	}

	// Reconcile ConsoleLink (if available in cluster)
	if err := r.reconcileConsoleLink(ctx, mlflow); err != nil {
		log.Error(err, "Failed to reconcile ConsoleLink")
		meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
			Type:    "Available",
			Status:  metav1.ConditionFalse,
			Reason:  "ConsoleLinkFailed",
			Message: fmt.Sprintf("Failed to reconcile ConsoleLink: %v", err),
		})
		if statusErr := r.updateStatus(ctx, mlflow); statusErr != nil {
			log.Error(statusErr, "Failed to update MLflow status after retries")
		}
		return ctrl.Result{}, err
	}

	// Reconcile HttpRoute
	if err := r.reconcileHttpRoute(ctx, mlflow, targetNamespace); err != nil {
		setObservedURLs(mlflow, targetNamespace, false, cfg)
		log.Error(err, "Failed to reconcile HttpRoute")
		meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
			Type:    "Available",
			Status:  metav1.ConditionFalse,
			Reason:  "HttpRouteFailed",
			Message: fmt.Sprintf("Failed to reconcile HttpRoute: %v", err),
		})
		if statusErr := r.updateStatus(ctx, mlflow); statusErr != nil {
			log.Error(statusErr, "Failed to update MLflow status after retries")
		}
		return ctrl.Result{}, err
	}

	setObservedURLs(mlflow, targetNamespace, r.HTTPRouteAvailable, cfg)

	// Get deployment name using the resource suffix
	deploymentName := ResourceName + getResourceSuffix(mlflow.Name)

	// Check deployment readiness
	deployment := &appsv1.Deployment{}
	err = r.Get(ctx, types.NamespacedName{Name: deploymentName, Namespace: targetNamespace}, deployment)
	if err != nil {
		if !errors.IsNotFound(err) {
			log.Error(err, "Failed to get Deployment")
			return ctrl.Result{}, err
		}
		// Deployment not created yet, requeue
		return ctrl.Result{RequeueAfter: 5 * time.Second}, nil
	}

	// Check if deployment is ready
	// Get desired replica count from deployment spec
	desiredReplicas := int32(1)
	if deployment.Spec.Replicas != nil {
		desiredReplicas = *deployment.Spec.Replicas
	}

	// Only mark as ready if:
	// 1. Desired replicas > 0 (not scaled down)
	// 2. All desired replicas are ready
	if desiredReplicas > 0 && deployment.Status.ReadyReplicas >= desiredReplicas {
		migrationJob := &batchv1.Job{}
		jobErr := r.Get(ctx, types.NamespacedName{Name: migrationJobName(mlflow), Namespace: targetNamespace}, migrationJob)
		switch {
		case jobErr == nil && isJobSuccessful(migrationJob):
			if err := r.markMigrationSuccessful(ctx, mlflow); err != nil {
				log.Error(err, "Failed to finalize migration status after rollout became ready")
				return ctrl.Result{}, err
			}
		case jobErr != nil && !errors.IsNotFound(jobErr):
			log.Error(jobErr, "Failed to get migration Job")
			return ctrl.Result{}, jobErr
		}

		// Deployment is ready
		meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
			Type:    "Available",
			Status:  metav1.ConditionTrue,
			Reason:  "DeploymentReady",
			Message: "MLflow deployment is ready and available",
		})
		meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
			Type:    "Progressing",
			Status:  metav1.ConditionFalse,
			Reason:  "ReconcileComplete",
			Message: "MLflow reconciliation completed successfully",
		})
	} else {
		// Deployment not ready yet
		message := fmt.Sprintf("MLflow deployment not ready: %d/%d replicas ready", deployment.Status.ReadyReplicas, desiredReplicas)
		if desiredReplicas == 0 {
			message = "MLflow deployment scaled to zero replicas"
		}
		meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
			Type:    "Available",
			Status:  metav1.ConditionFalse,
			Reason:  "DeploymentNotReady",
			Message: message,
		})
		meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
			Type:    "Progressing",
			Status:  metav1.ConditionTrue,
			Reason:  "DeploymentProgressing",
			Message: message,
		})
		// Keep requeuing until ready
		if err := r.updateStatus(ctx, mlflow); err != nil {
			log.Error(err, "Failed to update MLflow status after retries")
			return ctrl.Result{}, err
		}
		return ctrl.Result{RequeueAfter: 10 * time.Second}, nil
	}

	if err := r.updateStatus(ctx, mlflow); err != nil {
		log.Error(err, "Failed to update MLflow status after retries")
		return ctrl.Result{}, err
	}

	log.Info("Successfully reconciled MLflow")
	return ctrl.Result{}, nil
}

// applyObject applies a single Kubernetes object using Server-Side Apply
func (r *MLflowReconciler) applyObject(ctx context.Context, obj client.Object) error {
	log := logf.FromContext(ctx)

	// Special handling for PVCs - check if it exists first since specs are immutable
	if obj.GetObjectKind().GroupVersionKind().Kind == "PersistentVolumeClaim" {
		existing := obj.DeepCopyObject().(client.Object)
		err := r.Get(ctx, client.ObjectKeyFromObject(obj), existing)
		if err == nil {
			// PVC already exists, skip to avoid immutability errors
			log.V(1).Info("PVC already exists, skipping (PVC specs are immutable)", "name", obj.GetName(), "namespace", obj.GetNamespace())
			return nil
		} else if !errors.IsNotFound(err) {
			return err
		}
		// PVC doesn't exist, fall through to create it via SSA
	}

	// Use Server-Side Apply - the API server handles all the merge logic
	// This avoids unnecessary updates when only metadata changes
	err := r.Patch(ctx, obj, client.Apply, client.ForceOwnership, client.FieldOwner("mlflow-operator"))
	if err != nil {
		log.Error(err, "Failed to apply object", "kind", obj.GetObjectKind().GroupVersionKind().Kind, "name", obj.GetName(), "namespace", obj.GetNamespace())
		return err
	}

	log.V(1).Info("Applied object", "kind", obj.GetObjectKind().GroupVersionKind().Kind, "name", obj.GetName(), "namespace", obj.GetNamespace())
	return nil
}

// SetupWithManager sets up the controller with the Manager.
func (r *MLflowReconciler) SetupWithManager(mgr ctrl.Manager) error {
	log := ctrl.Log.WithName("setup")

	if r.GCRBACWatchCache == nil {
		return fmt.Errorf("GCRBACWatchCache must be configured")
	}

	builder := ctrl.NewControllerManagedBy(mgr).
		For(&mlflowv1.MLflow{}).
		Owns(&appsv1.Deployment{}).
		Owns(&batchv1.Job{}).
		Owns(&batchv1.CronJob{}).
		Owns(&corev1.Secret{}).
		Owns(&corev1.Service{}).
		Owns(&corev1.ServiceAccount{}).
		Owns(&corev1.PersistentVolumeClaim{}).
		// For shared cluster-scoped RBAC objects, we use Watches instead of Owns because:
		// 1. The shared objects can have multiple non-controller owner references (one per MLflow instance)
		// 2. Owns() only triggers on controller owner references
		// This handler enqueues all MLflow instances listed in the owner references.
		Watches(&rbacv1.ClusterRole{}, handler.EnqueueRequestsFromMapFunc(r.sharedClusterRoleToMLflowRequests)).
		Watches(&rbacv1.ClusterRoleBinding{}, handler.EnqueueRequestsFromMapFunc(r.sharedClusterRoleBindingToMLflowRequests)).
		// Watch platform CA bundle ConfigMap to trigger reconciliation when it appears/disappears
		// Note: We don't restart pods on content changes - kubelet automatically updates mounted ConfigMaps
		// This watch ensures we update the Deployment spec when the ConfigMap existence changes
		Watches(
			&corev1.ConfigMap{},
			handler.EnqueueRequestsFromMapFunc(r.configMapToMLflowRequests),
			controllerbuilder.WithPredicates(predicate.NewPredicateFuncs(func(obj client.Object) bool {
				return obj.GetName() == PlatformTrustedCABundleConfigMapName
			})),
		)

	// Use a separate raw source for `mlflow-gc` RBAC watches instead of widening the main cache.
	// This is a workaround for two Kubernetes/controller-runtime constraints:
	// 1. tight resourceNames-scoped RBAC for list/watch only works when the watch is restricted to
	//    an exact metadata.name field selector; and
	// 2. the main cache can only carry one selector per GVK, which is already used for the shared
	//    `mlflow` ClusterRole/ClusterRoleBinding objects.
	// The dedicated cache lets us watch the singleton `mlflow-gc` objects too without reopening
	// broad label-scoped RBAC on all ClusterRoles/ClusterRoleBindings.
	builder = builder.
		WatchesRawSource(
			source.Kind(
				r.GCRBACWatchCache,
				&rbacv1.ClusterRole{},
				handler.TypedEnqueueRequestsFromMapFunc(r.gcClusterRoleToMLflowRequests),
			),
		).
		WatchesRawSource(
			source.Kind(
				r.GCRBACWatchCache,
				&rbacv1.ClusterRoleBinding{},
				handler.TypedEnqueueRequestsFromMapFunc(r.gcClusterRoleBindingToMLflowRequests),
			),
		)

	// Conditionally watch ConsoleLink if available in the cluster
	if r.ConsoleLinkAvailable {
		log.Info("ConsoleLink CRD available, adding to watch list")
		builder = builder.Owns(&consolev1.ConsoleLink{})
	} else {
		log.Info("ConsoleLink CRD not available, skipping watch")
	}

	// Conditionally watch HTTPRoute if available in the cluster
	if r.HTTPRouteAvailable {
		log.Info("HTTPRoute CRD available, adding to watch list")
		builder = builder.Owns(&gatewayv1.HTTPRoute{})
	} else {
		log.Info("HTTPRoute CRD not available, skipping watch")
	}

	// Conditionally watch ServiceMonitor if available in the cluster
	if r.ServiceMonitorAvailable {
		log.Info("ServiceMonitor CRD available, adding to watch list")
		builder = builder.Owns(&monitoringv1.ServiceMonitor{})
	} else {
		log.Info("ServiceMonitor CRD not available, skipping watch")
	}

	return builder.Complete(r)
}

func (r *MLflowReconciler) applyRenderedObjects(ctx context.Context, mlflow *mlflowv1.MLflow, objects []*unstructured.Unstructured) error {
	log := logf.FromContext(ctx)
	for _, obj := range objects {
		if obj.GetKind() != "Namespace" {
			if isSharedRBACObject(obj) {
				if err := r.appendOwnerReference(ctx, mlflow, obj); err != nil {
					log.Error(err, "Failed to append owner reference", "object", obj.GetKind(), "name", obj.GetName())
					return fmt.Errorf("append owner reference to %s/%s: %w", obj.GetKind(), obj.GetName(), err)
				}
			} else {
				if err := controllerutil.SetControllerReference(mlflow, obj, r.Scheme); err != nil {
					log.Error(err, "Failed to set controller reference", "object", obj.GetKind(), "name", obj.GetName())
					return fmt.Errorf("set controller reference on %s/%s: %w", obj.GetKind(), obj.GetName(), err)
				}
			}
		}

		if err := r.applyObject(ctx, obj); err != nil {
			log.Error(err, "Failed to apply object", "kind", obj.GetKind(), "name", obj.GetName())
			return fmt.Errorf("apply %s/%s: %w", obj.GetKind(), obj.GetName(), err)
		}
	}
	return nil
}

// sharedClusterRoleToMLflowRequests maps the shared ClusterRole to MLflow reconcile requests.
func (r *MLflowReconciler) sharedClusterRoleToMLflowRequests(ctx context.Context, obj client.Object) []reconcile.Request {
	return sharedRBACObjectToMLflowRequests(obj, ClusterRoleName)
}

// sharedClusterRoleBindingToMLflowRequests maps the shared ClusterRoleBinding to MLflow reconcile requests.
func (r *MLflowReconciler) sharedClusterRoleBindingToMLflowRequests(ctx context.Context, obj client.Object) []reconcile.Request {
	return sharedRBACObjectToMLflowRequests(obj, ClusterRoleBindingName)
}

// gcClusterRoleToMLflowRequests maps the singleton GC ClusterRole to MLflow reconcile requests.
func (r *MLflowReconciler) gcClusterRoleToMLflowRequests(ctx context.Context, obj *rbacv1.ClusterRole) []reconcile.Request {
	return sharedRBACObjectToMLflowRequests(obj, GCClusterRBACName)
}

// gcClusterRoleBindingToMLflowRequests maps the singleton GC ClusterRoleBinding to MLflow reconcile requests.
func (r *MLflowReconciler) gcClusterRoleBindingToMLflowRequests(ctx context.Context, obj *rbacv1.ClusterRoleBinding) []reconcile.Request {
	return sharedRBACObjectToMLflowRequests(obj, GCClusterRBACName)
}

// configMapToMLflowRequests maps ConfigMap events to MLflow reconcile requests.
// When the platform CA bundle ConfigMap is created/updated/deleted, we need to reconcile
// all MLflow instances in that namespace to update their Deployment spec.
// Note: Content changes don't require pod restarts - kubelet auto-updates mounted ConfigMaps.
func (r *MLflowReconciler) configMapToMLflowRequests(ctx context.Context, obj client.Object) []reconcile.Request {
	log := logf.FromContext(ctx)

	// List all MLflow instances in the same namespace as the ConfigMap
	mlflowList := &mlflowv1.MLflowList{}
	if err := r.List(ctx, mlflowList); err != nil {
		log.Error(err, "Failed to list MLflow instances for ConfigMap watch")
		return nil
	}

	requests := make([]reconcile.Request, 0, len(mlflowList.Items))
	for _, mlflow := range mlflowList.Items {
		log.V(1).Info("Enqueueing MLflow reconciliation due to platform CA bundle change",
			"mlflow", mlflow.Name,
			"configmap", obj.GetName(),
			"configmap-namespace", obj.GetNamespace())
		requests = append(requests, reconcile.Request{
			NamespacedName: types.NamespacedName{
				Name:      mlflow.Name,
				Namespace: mlflow.Namespace,
			},
		})
	}
	return requests
}

// updateStatus updates the MLflow status with retry on conflict
func (r *MLflowReconciler) updateStatus(ctx context.Context, mlflow *mlflowv1.MLflow) error {
	return retry.RetryOnConflict(retry.DefaultRetry, func() error {
		// Get the latest version before updating
		latest := &mlflowv1.MLflow{}
		if err := r.Get(ctx, types.NamespacedName{Name: mlflow.Name, Namespace: mlflow.Namespace}, latest); err != nil {
			return err
		}
		// Copy the status from our in-memory version to the latest version
		latest.Status = mlflow.Status
		// Update the status
		return r.Status().Update(ctx, latest)
	})
}

// appendOwnerReference appends an owner reference to the object without removing existing ones.
// This is used for shared resources like ClusterRole and ClusterRoleBinding where multiple MLflow
// instances may reference the same resource.
// Unlike SetControllerReference, this allows multiple owners (but none are marked as controller).
// It fetches the existing object from the cluster to preserve owner references from other MLflow instances.
func (r *MLflowReconciler) appendOwnerReference(ctx context.Context, mlflow *mlflowv1.MLflow, obj client.Object) error {
	// Build the owner reference for this MLflow instance
	gvk := mlflowv1.GroupVersion.WithKind("MLflow")
	ownerRef := metav1.OwnerReference{
		APIVersion: gvk.GroupVersion().String(),
		Kind:       gvk.Kind,
		Name:       mlflow.Name,
		UID:        mlflow.UID,
	}

	// Try to get the existing object from the cluster to preserve its owner references
	existing := obj.DeepCopyObject().(client.Object)
	err := r.Get(ctx, client.ObjectKeyFromObject(obj), existing)
	if err != nil {
		if !errors.IsNotFound(err) {
			return err
		}
		// Object doesn't exist yet, just set this owner reference
		obj.SetOwnerReferences([]metav1.OwnerReference{ownerRef})
		return nil
	}

	// Get existing owner references from the cluster object
	existingRefs := existing.GetOwnerReferences()

	// Check if this owner reference already exists
	for _, ref := range existingRefs {
		if ref.UID == ownerRef.UID {
			// Already exists, set the existing refs on the object to apply
			obj.SetOwnerReferences(existingRefs)
			return nil
		}
	}

	// Append the new owner reference
	existingRefs = append(existingRefs, ownerRef)
	obj.SetOwnerReferences(existingRefs)

	return nil
}

func sharedRBACObjectToMLflowRequests(obj client.Object, expectedName string) []reconcile.Request {
	if obj.GetName() != expectedName {
		return nil
	}

	var requests []reconcile.Request
	for _, ownerRef := range obj.GetOwnerReferences() {
		if ownerRef.APIVersion == mlflowv1.GroupVersion.String() && ownerRef.Kind == "MLflow" {
			requests = append(requests, reconcile.Request{
				NamespacedName: types.NamespacedName{
					Name: ownerRef.Name,
				},
			})
		}
	}
	return requests
}

func isSharedRBACObject(obj client.Object) bool {
	switch obj.GetObjectKind().GroupVersionKind().Kind {
	case "ClusterRole":
		return obj.GetName() == ClusterRoleName
	case "ClusterRoleBinding":
		return obj.GetName() == ClusterRoleBindingName
	default:
		return false
	}
}

func NewGCRBACWatchCache(cfg *rest.Config, scheme *runtime.Scheme) (crcache.Cache, error) {
	gcClusterRBACFieldSelector := fields.OneTermEqualSelector("metadata.name", GCClusterRBACName)
	return crcache.New(cfg, crcache.Options{
		Scheme: scheme,
		ByObject: map[client.Object]crcache.ByObject{
			&rbacv1.ClusterRole{}:        {Field: gcClusterRBACFieldSelector},
			&rbacv1.ClusterRoleBinding{}: {Field: gcClusterRBACFieldSelector},
		},
	})
}

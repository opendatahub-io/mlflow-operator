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
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/discovery"
	"k8s.io/client-go/util/retry"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	gatewayv1 "sigs.k8s.io/gateway-api/apis/v1"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

const (
	mlflowFinalizer = "mlflow.opendatahub.io/finalizer"
	chartPath       = "charts/mlflow"
)

// MLflowReconciler reconciles a MLflow object
type MLflowReconciler struct {
	client.Client
	Scheme          *runtime.Scheme
	Namespace       string
	ChartPath       string
	DiscoveryClient discovery.DiscoveryInterface
}

// +kubebuilder:rbac:groups=mlflow.opendatahub.io,resources=mlflows,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=mlflow.opendatahub.io,resources=mlflows/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=mlflow.opendatahub.io,resources=mlflows/finalizers,verbs=update
// +kubebuilder:rbac:groups="",resources=namespaces,verbs=get;list;watch
// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=clusterroles,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=clusterrolebindings,verbs=get;list;watch;create;update;patch;delete
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

	// Handle deletion
	if mlflow.GetDeletionTimestamp() != nil {
		if controllerutil.ContainsFinalizer(mlflow, mlflowFinalizer) {
			if err := r.cleanupResources(ctx, mlflow, targetNamespace); err != nil {
				log.Error(err, "Failed to cleanup resources")
				return ctrl.Result{}, err
			}
			controllerutil.RemoveFinalizer(mlflow, mlflowFinalizer)
			if err := r.Update(ctx, mlflow); err != nil {
				return ctrl.Result{}, err
			}
		}
		return ctrl.Result{}, nil
	}

	// Add finalizer if not present
	if !controllerutil.ContainsFinalizer(mlflow, mlflowFinalizer) {
		controllerutil.AddFinalizer(mlflow, mlflowFinalizer)
		if err := r.Update(ctx, mlflow); err != nil {
			return ctrl.Result{}, err
		}
	}

	// Render Helm chart
	helmChartPath := r.ChartPath
	if helmChartPath == "" {
		helmChartPath = chartPath
	}
	renderer := NewHelmRenderer(helmChartPath)
	objects, err := renderer.RenderChart(mlflow, targetNamespace)
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

	// Apply rendered manifests
	for _, obj := range objects {
		// Set owner reference for namespaced resources (except namespace itself)
		if obj.GetKind() != "Namespace" && obj.GetKind() != "ClusterRole" && obj.GetKind() != "ClusterRoleBinding" {
			if err := controllerutil.SetControllerReference(mlflow, obj, r.Scheme); err != nil {
				log.Error(err, "Failed to set controller reference", "object", obj.GetKind(), "name", obj.GetName())
				// Continue with other objects
				continue
			}
		}

		// Apply the object
		if err := r.applyObject(ctx, obj); err != nil {
			log.Error(err, "Failed to apply object", "kind", obj.GetKind(), "name", obj.GetName())
			meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
				Type:    "Available",
				Status:  metav1.ConditionFalse,
				Reason:  "ApplyFailed",
				Message: fmt.Sprintf("Failed to apply %s/%s: %v", obj.GetKind(), obj.GetName(), err),
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
	}

	// Reconcile ConsoleLink (if available in cluster)
	if err := r.reconcileConsoleLink(ctx, mlflow, r.DiscoveryClient); err != nil {
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

	// Get deployment name using the resource suffix
	deploymentName := "mlflow" + getResourceSuffix(mlflow.Name)

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

// cleanupResources cleans up resources when MLflow is deleted
// nolint:unparam // error return is kept for consistency with reconciler pattern, cleanup is best-effort
func (r *MLflowReconciler) cleanupResources(ctx context.Context, mlflow *mlflowv1.MLflow, namespace string) error {
	log := logf.FromContext(ctx)
	log.Info("Cleaning up MLflow resources", "namespace", namespace)

	// Most resources will be automatically deleted via owner references
	// Here we clean up cluster-scoped resources that don't have owner references

	// Delete ClusterRole
	clusterRole := &rbacv1.ClusterRole{
		ObjectMeta: metav1.ObjectMeta{
			Name: getClusterRoleName(mlflow.Name),
		},
	}
	if err := r.Delete(ctx, clusterRole); err != nil && !errors.IsNotFound(err) {
		log.Error(err, "Failed to delete ClusterRole")
	}

	// Delete ClusterRoleBinding
	clusterRoleBinding := &rbacv1.ClusterRoleBinding{
		ObjectMeta: metav1.ObjectMeta{
			Name: getClusterRoleBindingName(mlflow.Name),
		},
	}
	if err := r.Delete(ctx, clusterRoleBinding); err != nil && !errors.IsNotFound(err) {
		log.Error(err, "Failed to delete ClusterRoleBinding")
	}

	return nil
}

// SetupWithManager sets up the controller with the Manager.
func (r *MLflowReconciler) SetupWithManager(mgr ctrl.Manager) error {
	log := ctrl.Log.WithName("setup")

	builder := ctrl.NewControllerManagedBy(mgr).
		For(&mlflowv1.MLflow{}).
		Owns(&appsv1.Deployment{}).
		Owns(&corev1.Secret{}).
		Owns(&corev1.Service{}).
		Owns(&corev1.ServiceAccount{}).
		Owns(&corev1.PersistentVolumeClaim{})

	// Conditionally watch ConsoleLink if available in the cluster
	consoleLinkAvailable, err := IsConsoleLinkAvailable(r.DiscoveryClient)
	if err != nil {
		log.Error(err, "Failed to check ConsoleLink availability, proceeding without watching ConsoleLinks")
	} else if consoleLinkAvailable {
		log.Info("ConsoleLink CRD available, adding to watch list")
		builder = builder.Owns(&consolev1.ConsoleLink{})
	} else {
		log.Info("ConsoleLink CRD not available, skipping watch")
	}

	// Conditionally watch HTTPRoute if available in the cluster
	httpRouteAvailable, err := IsHTTPRouteAvailable(r.DiscoveryClient)
	if err != nil {
		log.Error(err, "Failed to check HTTPRoute availability, proceeding without watching HTTPRoutes")
	} else if httpRouteAvailable {
		log.Info("HTTPRoute CRD available, adding to watch list")
		builder = builder.Owns(&gatewayv1.HTTPRoute{})
	} else {
		log.Info("HTTPRoute CRD not available, skipping watch")
	}

	return builder.Complete(r)
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

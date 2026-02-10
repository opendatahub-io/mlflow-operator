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
	"k8s.io/client-go/util/retry"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
	gatewayv1 "sigs.k8s.io/gateway-api/apis/v1"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

const (
	chartPath = "charts/mlflow"
)

// MLflowReconciler reconciles a MLflow object
type MLflowReconciler struct {
	client.Client
	Scheme               *runtime.Scheme
	Namespace            string
	ChartPath            string
	ConsoleLinkAvailable bool
	HTTPRouteAvailable   bool
}

// +kubebuilder:rbac:groups=mlflow.opendatahub.io,resources=mlflows,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=mlflow.opendatahub.io,resources=mlflows/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=mlflow.opendatahub.io,resources=mlflows/finalizers,verbs=update
// +kubebuilder:rbac:groups="",resources=secrets,resourceNames=mlflow-artifact-connection,verbs=get
// +kubebuilder:rbac:groups=mlflow.kubeflow.org,resources=mlflowconfigs,verbs=get;list;watch
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

	// Handle deletion - all resources are cleaned up via owner references
	if mlflow.GetDeletionTimestamp() != nil {
		return ctrl.Result{}, nil
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
		// MLflow CR is cluster-scoped so set owner reference for all resources
		if obj.GetKind() != "Namespace" {
			// For the shared "mlflow" ClusterRole, append owner references instead of using
			// SetControllerReference which only allows one controller owner. This allows
			// multiple MLflow instances to share the same ClusterRole.
			if obj.GetKind() == "ClusterRole" && obj.GetName() == ClusterRoleName {
				if err := r.appendOwnerReference(ctx, mlflow, obj); err != nil {
					log.Error(err, "Failed to append owner reference", "object", obj.GetKind(), "name", obj.GetName())
					// Continue with other objects
					continue
				}
			} else {
				if err := controllerutil.SetControllerReference(mlflow, obj, r.Scheme); err != nil {
					log.Error(err, "Failed to set controller reference", "object", obj.GetKind(), "name", obj.GetName())
					// Continue with other objects
					continue
				}
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

// SetupWithManager sets up the controller with the Manager.
func (r *MLflowReconciler) SetupWithManager(mgr ctrl.Manager) error {
	log := ctrl.Log.WithName("setup")

	builder := ctrl.NewControllerManagedBy(mgr).
		For(&mlflowv1.MLflow{}).
		Owns(&appsv1.Deployment{}).
		Owns(&corev1.Secret{}).
		Owns(&corev1.Service{}).
		Owns(&corev1.ServiceAccount{}).
		Owns(&corev1.PersistentVolumeClaim{}).
		// For the shared ClusterRole, we use Watches instead of Owns because:
		// 1. The ClusterRole has multiple non-controller owner references (one per MLflow instance)
		// 2. Owns() only triggers on controller owner references
		// This handler enqueues all MLflow instances listed in the owner references
		Watches(&rbacv1.ClusterRole{}, handler.EnqueueRequestsFromMapFunc(r.clusterRoleToMLflowRequests)).
		Owns(&rbacv1.ClusterRoleBinding{})

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

	return builder.Complete(r)
}

// clusterRoleToMLflowRequests maps ClusterRole events to MLflow reconcile requests.
// Since the shared ClusterRole can have multiple MLflow owner references (not controller refs),
// we need to manually extract and enqueue all referenced MLflow instances.
func (r *MLflowReconciler) clusterRoleToMLflowRequests(ctx context.Context, obj client.Object) []reconcile.Request {
	// Only handle the shared ClusterRole
	if obj.GetName() != ClusterRoleName {
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
// This is used for shared resources like ClusterRole where multiple MLflow instances may reference the same resource.
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

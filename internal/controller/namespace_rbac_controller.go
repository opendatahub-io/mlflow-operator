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

	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/fields"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/rest"
	ctrl "sigs.k8s.io/controller-runtime"
	controllerbuilder "sigs.k8s.io/controller-runtime/pkg/builder"
	crcache "sigs.k8s.io/controller-runtime/pkg/cache"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/event"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/predicate"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
	"sigs.k8s.io/controller-runtime/pkg/source"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

var authGVK = schema.GroupVersionKind{
	Group:   "services.platform.opendatahub.io",
	Version: "v1alpha1",
	Kind:    "Auth",
}

// NamespaceRBACReconciler reconciles RoleBindings in namespaces labeled for MLflow workspace access.
type NamespaceRBACReconciler struct {
	client.Client
	Scheme              *runtime.Scheme
	ViewRBWatchCache    crcache.Cache
	EditRBWatchCache    crcache.Cache
	ViewClusterRoleName string
	EditClusterRoleName string
}

// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=rolebindings,verbs=create
// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=rolebindings,resourceNames=odh-group-mlflow-view;odh-group-mlflow-edit,verbs=get;list;watch;update;patch;delete
// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=clusterroles,resourceNames=mlflow-operator-mlflow-view;mlflow-operator-mlflow-edit,verbs=bind
// +kubebuilder:rbac:groups=services.platform.opendatahub.io,resources=auths,resourceNames=auth,verbs=get;list;watch

// SetupWithManager registers watches for Namespace (primary), Auth CR, MLflow CR, and managed RoleBindings.
func (r *NamespaceRBACReconciler) SetupWithManager(mgr ctrl.Manager) error {
	if r.ViewRBWatchCache == nil {
		return fmt.Errorf("ViewRBWatchCache must be configured")
	}
	if r.EditRBWatchCache == nil {
		return fmt.Errorf("EditRBWatchCache must be configured")
	}
	if r.ViewClusterRoleName == "" {
		return fmt.Errorf("ViewClusterRoleName must be configured")
	}
	if r.EditClusterRoleName == "" {
		return fmt.Errorf("EditClusterRoleName must be configured")
	}

	authObj := &unstructured.Unstructured{}
	authObj.SetGroupVersionKind(authGVK)

	// Both RoleBinding watches use dedicated caches with metadata.name field selectors
	// so that list/watch stays compatible with resourceNames-scoped RBAC.
	// Two caches are needed because controller-runtime only allows one field selector
	// per GVK per cache.
	return ctrl.NewControllerManagedBy(mgr).
		For(&corev1.Namespace{}, controllerbuilder.WithPredicates(namespacePredicate())).
		WatchesRawSource(
			source.Kind(r.ViewRBWatchCache, &rbacv1.RoleBinding{},
				handler.TypedEnqueueRequestsFromMapFunc(r.mapRoleBindingToNamespace)),
		).
		WatchesRawSource(
			source.Kind(r.EditRBWatchCache, &rbacv1.RoleBinding{},
				handler.TypedEnqueueRequestsFromMapFunc(r.mapRoleBindingToNamespace)),
		).
		Watches(&mlflowv1.MLflow{},
			handler.EnqueueRequestsFromMapFunc(r.mapMLflowToNamespaces),
		).
		Watches(authObj,
			handler.EnqueueRequestsFromMapFunc(r.mapAuthToNamespaces),
		).
		Complete(r)
}

// namespacePredicate filters namespace events to only those where the workspace
// label is (or was) present. For Update, both old and new objects are checked so
// that label removal still triggers reconciliation for cleanup.
func namespacePredicate() predicate.Predicate {
	return predicate.Funcs{
		CreateFunc: func(e event.CreateEvent) bool {
			_, has := e.Object.GetLabels()[NamespaceWorkspaceLabelKey]
			return has
		},
		UpdateFunc: func(e event.UpdateEvent) bool {
			_, oldHas := e.ObjectOld.GetLabels()[NamespaceWorkspaceLabelKey]
			_, newHas := e.ObjectNew.GetLabels()[NamespaceWorkspaceLabelKey]
			return oldHas || newHas
		},
		DeleteFunc: func(e event.DeleteEvent) bool {
			_, has := e.Object.GetLabels()[NamespaceWorkspaceLabelKey]
			return has
		},
		GenericFunc: func(e event.GenericEvent) bool {
			_, has := e.Object.GetLabels()[NamespaceWorkspaceLabelKey]
			return has
		},
	}
}

func (r *NamespaceRBACReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := logf.FromContext(ctx)

	ns := &corev1.Namespace{}
	if err := r.Get(ctx, req.NamespacedName, ns); err != nil {
		if errors.IsNotFound(err) {
			return ctrl.Result{}, nil
		}
		return ctrl.Result{}, fmt.Errorf("failed to get Namespace %s: %w", req.Name, err)
	}

	mlflowCRName, hasLabel := ns.Labels[NamespaceWorkspaceLabelKey]
	if !hasLabel || mlflowCRName == "" {
		return ctrl.Result{}, r.cleanupRoleBindings(ctx, req.Name)
	}

	mlflow := &mlflowv1.MLflow{}
	if err := r.Get(ctx, types.NamespacedName{Name: mlflowCRName}, mlflow); err != nil {
		if errors.IsNotFound(err) {
			log.V(1).Info("MLflow CR not found, cleaning up namespace RBAC", "mlflowCR", mlflowCRName, "namespace", req.Name)
			return ctrl.Result{}, r.cleanupRoleBindings(ctx, req.Name)
		}
		return ctrl.Result{}, fmt.Errorf("failed to get MLflow CR %s: %w", mlflowCRName, err)
	}

	allowedGroups, adminGroups, err := r.getAuthGroups(ctx)
	if err != nil {
		if errors.IsNotFound(err) {
			log.V(1).Info("Auth CR not found, cleaning up namespace RBAC", "namespace", req.Name)
			return ctrl.Result{}, r.cleanupRoleBindings(ctx, req.Name)
		}
		return ctrl.Result{}, fmt.Errorf("failed to get auth groups: %w", err)
	}

	if err := r.applyRoleBindings(ctx, req.Name, mlflow, allowedGroups, adminGroups); err != nil {
		return ctrl.Result{}, err
	}

	log.V(1).Info("Reconciled namespace RBAC", "namespace", req.Name)
	return ctrl.Result{}, nil
}

func (r *NamespaceRBACReconciler) getAuthGroups(ctx context.Context) (allowedGroups, adminGroups []string, err error) {
	log := logf.FromContext(ctx)

	auth := &unstructured.Unstructured{}
	auth.SetGroupVersionKind(authGVK)
	if err := r.Get(ctx, types.NamespacedName{Name: AuthCRName}, auth); err != nil {
		return nil, nil, err
	}

	allowedGroups, found, err := unstructured.NestedStringSlice(auth.Object, "spec", "allowedGroups")
	if err != nil {
		return nil, nil, fmt.Errorf("failed to read spec.allowedGroups from Auth CR: %w", err)
	}
	if !found {
		log.V(1).Info("spec.allowedGroups not set in Auth CR")
	}

	adminGroups, found, err = unstructured.NestedStringSlice(auth.Object, "spec", "adminGroups")
	if err != nil {
		return nil, nil, fmt.Errorf("failed to read spec.adminGroups from Auth CR: %w", err)
	}
	if !found {
		log.V(1).Info("spec.adminGroups not set in Auth CR")
	}

	return allowedGroups, adminGroups, nil
}

func (r *NamespaceRBACReconciler) rbReader(name string) client.Reader {
	if name == RoleBindingViewName {
		return r.ViewRBWatchCache
	}
	return r.EditRBWatchCache
}

func (r *NamespaceRBACReconciler) applyRoleBindings(ctx context.Context, namespace string, mlflow *mlflowv1.MLflow, allowedGroups, adminGroups []string) error {
	viewSubjects := buildGroupSubjects(uniqueGroups(allowedGroups, adminGroups))
	editSubjects := buildGroupSubjects(adminGroups)

	managedLabels := map[string]string{
		ManagedByLabelKey: ManagedByLabelValue,
	}

	viewRB := &rbacv1.RoleBinding{
		TypeMeta: metav1.TypeMeta{APIVersion: "rbac.authorization.k8s.io/v1", Kind: "RoleBinding"},
		ObjectMeta: metav1.ObjectMeta{
			Name:      RoleBindingViewName,
			Namespace: namespace,
			Labels:    managedLabels,
		},
		RoleRef: rbacv1.RoleRef{
			APIGroup: "rbac.authorization.k8s.io",
			Kind:     "ClusterRole",
			Name:     r.ViewClusterRoleName,
		},
		Subjects: viewSubjects,
	}

	editRB := &rbacv1.RoleBinding{
		TypeMeta: metav1.TypeMeta{APIVersion: "rbac.authorization.k8s.io/v1", Kind: "RoleBinding"},
		ObjectMeta: metav1.ObjectMeta{
			Name:      RoleBindingEditName,
			Namespace: namespace,
			Labels:    managedLabels,
		},
		RoleRef: rbacv1.RoleRef{
			APIGroup: "rbac.authorization.k8s.io",
			Kind:     "ClusterRole",
			Name:     r.EditClusterRoleName,
		},
		Subjects: editSubjects,
	}

	log := logf.FromContext(ctx)
	for _, rb := range []*rbacv1.RoleBinding{viewRB, editRB} {
		if err := controllerutil.SetControllerReference(mlflow, rb, r.Scheme); err != nil {
			return fmt.Errorf("failed to set owner reference on RoleBinding %s/%s: %w", rb.Namespace, rb.Name, err)
		}

		existing := &rbacv1.RoleBinding{}
		err := r.rbReader(rb.Name).Get(ctx, client.ObjectKeyFromObject(rb), existing)
		// roleRef is immutable; a mismatch requires delete + recreate.
		// https://kubernetes.io/docs/reference/access-authn-authz/rbac/#rolebinding-and-clusterrolebinding
		if err == nil && existing.RoleRef != rb.RoleRef {
			log.Info("RoleBinding roleRef mismatch detected, deleting before re-apply",
				"rolebinding", rb.Name, "namespace", rb.Namespace,
				"existingRoleRef", existing.RoleRef.Name, "desiredRoleRef", rb.RoleRef.Name)
			if delErr := r.Delete(ctx, existing); delErr != nil && !errors.IsNotFound(delErr) {
				return fmt.Errorf("failed to delete RoleBinding %s/%s with mismatched roleRef: %w", rb.Namespace, rb.Name, delErr)
			}
		} else if err != nil && !errors.IsNotFound(err) {
			return fmt.Errorf("failed to get existing RoleBinding %s/%s: %w", rb.Namespace, rb.Name, err)
		}

		if err := r.Patch(ctx, rb, client.Apply, client.ForceOwnership, client.FieldOwner("mlflow-operator")); err != nil { //nolint:staticcheck // matches existing applyObject pattern
			return fmt.Errorf("failed to apply RoleBinding %s/%s: %w", rb.Namespace, rb.Name, err)
		}
	}
	return nil
}

func (r *NamespaceRBACReconciler) cleanupRoleBindings(ctx context.Context, namespace string) error {
	for _, name := range []string{RoleBindingViewName, RoleBindingEditName} {
		existing := &rbacv1.RoleBinding{}
		err := r.rbReader(name).Get(ctx, types.NamespacedName{Name: name, Namespace: namespace}, existing)
		if errors.IsNotFound(err) {
			continue
		}
		if err != nil {
			return fmt.Errorf("failed to get RoleBinding %s/%s for cleanup: %w", namespace, name, err)
		}
		if err := r.Delete(ctx, existing); err != nil && !errors.IsNotFound(err) {
			return fmt.Errorf("failed to delete RoleBinding %s/%s: %w", namespace, name, err)
		}
	}
	return nil
}

func (r *NamespaceRBACReconciler) mapAuthToNamespaces(ctx context.Context, obj client.Object) []reconcile.Request {
	if obj.GetName() != AuthCRName {
		return nil
	}
	return r.listLabeledNamespaceRequests(ctx)
}

func (r *NamespaceRBACReconciler) mapMLflowToNamespaces(ctx context.Context, obj client.Object) []reconcile.Request {
	nsList := &corev1.NamespaceList{}
	if err := r.List(ctx, nsList, client.MatchingLabels{NamespaceWorkspaceLabelKey: obj.GetName()}); err != nil {
		logf.FromContext(ctx).Error(err, "Failed to list namespaces for MLflow fan-out", "mlflow", obj.GetName())
		return nil
	}
	requests := make([]reconcile.Request, 0, len(nsList.Items))
	for _, ns := range nsList.Items {
		requests = append(requests, reconcile.Request{
			NamespacedName: types.NamespacedName{Name: ns.Name},
		})
	}
	return requests
}

func (r *NamespaceRBACReconciler) mapRoleBindingToNamespace(_ context.Context, obj *rbacv1.RoleBinding) []reconcile.Request {
	return []reconcile.Request{{
		NamespacedName: types.NamespacedName{Name: obj.GetNamespace()},
	}}
}

func (r *NamespaceRBACReconciler) listLabeledNamespaceRequests(ctx context.Context) []reconcile.Request {
	nsList := &corev1.NamespaceList{}
	if err := r.List(ctx, nsList, client.HasLabels{NamespaceWorkspaceLabelKey}); err != nil {
		logf.FromContext(ctx).Error(err, "Failed to list labeled namespaces")
		return nil
	}
	requests := make([]reconcile.Request, 0, len(nsList.Items))
	for _, ns := range nsList.Items {
		requests = append(requests, reconcile.Request{
			NamespacedName: types.NamespacedName{Name: ns.Name},
		})
	}
	return requests
}

func buildGroupSubjects(groups []string) []rbacv1.Subject {
	subjects := make([]rbacv1.Subject, 0, len(groups))
	for _, g := range groups {
		subjects = append(subjects, rbacv1.Subject{
			APIGroup: "rbac.authorization.k8s.io",
			Kind:     "Group",
			Name:     g,
		})
	}
	return subjects
}

func uniqueGroups(slices ...[]string) []string {
	seen := make(map[string]struct{})
	var result []string
	for _, s := range slices {
		for _, g := range s {
			if _, ok := seen[g]; !ok {
				seen[g] = struct{}{}
				result = append(result, g)
			}
		}
	}
	return result
}

func NewRoleBindingWatchCache(cfg *rest.Config, scheme *runtime.Scheme, name string) (crcache.Cache, error) {
	return crcache.New(cfg, crcache.Options{
		Scheme: scheme,
		ByObject: map[client.Object]crcache.ByObject{
			&rbacv1.RoleBinding{}: {
				Field: fields.OneTermEqualSelector("metadata.name", name),
				Namespaces: map[string]crcache.Config{
					crcache.AllNamespaces: {},
				},
			},
		},
	})
}

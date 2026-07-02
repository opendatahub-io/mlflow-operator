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
	Scheme         *runtime.Scheme
	AuthWatchCache crcache.Cache
}

// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=rolebindings,verbs=create;list;watch
// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=rolebindings,resourceNames=mlflow-view;mlflow-edit,verbs=get;update;patch;delete
// +kubebuilder:rbac:groups=services.platform.opendatahub.io,resources=auths,verbs=get;list;watch

func (r *NamespaceRBACReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := logf.FromContext(ctx)

	ns := &corev1.Namespace{}
	if err := r.Get(ctx, req.NamespacedName, ns); err != nil {
		if errors.IsNotFound(err) {
			return ctrl.Result{}, nil
		}
		return ctrl.Result{}, err
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
		return ctrl.Result{}, err
	}

	allowedGroups, adminGroups, err := r.getAuthGroups(ctx)
	if err != nil {
		if errors.IsNotFound(err) {
			log.V(1).Info("Auth CR not found, requeueing", "namespace", req.Name)
			return ctrl.Result{RequeueAfter: 30_000_000_000}, nil // 30s
		}
		return ctrl.Result{}, err
	}

	if err := r.applyRoleBindings(ctx, req.Name, allowedGroups, adminGroups); err != nil {
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

func (r *NamespaceRBACReconciler) applyRoleBindings(ctx context.Context, namespace string, allowedGroups, adminGroups []string) error {
	viewSubjects := buildGroupSubjects(uniqueGroups(allowedGroups, adminGroups))
	editSubjects := buildGroupSubjects(adminGroups)

	managedLabels := map[string]string{
		NamespaceRBACLabelKey: "true",
		ManagedByLabelKey:     ManagedByLabelValue,
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
			Name:     ViewClusterRoleName,
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
			Name:     EditClusterRoleName,
		},
		Subjects: editSubjects,
	}

	log := logf.FromContext(ctx)
	for _, rb := range []*rbacv1.RoleBinding{viewRB, editRB} {
		existing := &rbacv1.RoleBinding{}
		err := r.Get(ctx, client.ObjectKeyFromObject(rb), existing)
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
	rbList := &rbacv1.RoleBindingList{}
	if err := r.List(ctx, rbList,
		client.InNamespace(namespace),
		client.MatchingLabels{NamespaceRBACLabelKey: "true"},
	); err != nil {
		return err
	}
	for i := range rbList.Items {
		if err := r.Delete(ctx, &rbList.Items[i]); err != nil && !errors.IsNotFound(err) {
			return err
		}
	}
	return nil
}

// SetupWithManager registers watches for Namespace (primary), Auth CR, MLflow CR, and managed RoleBindings.
func (r *NamespaceRBACReconciler) SetupWithManager(mgr ctrl.Manager) error {
	if r.AuthWatchCache == nil {
		return fmt.Errorf("AuthWatchCache must be configured")
	}

	authObj := &unstructured.Unstructured{}
	authObj.SetGroupVersionKind(authGVK)

	return ctrl.NewControllerManagedBy(mgr).
		For(&corev1.Namespace{}, controllerbuilder.WithPredicates(predicate.Or(
			predicate.NewPredicateFuncs(func(obj client.Object) bool {
				_, hasLabel := obj.GetLabels()[NamespaceWorkspaceLabelKey]
				return hasLabel
			}),
			predicate.LabelChangedPredicate{},
		))).
		Watches(&rbacv1.RoleBinding{},
			handler.EnqueueRequestsFromMapFunc(r.mapRoleBindingToNamespace),
		).
		Watches(&mlflowv1.MLflow{},
			handler.EnqueueRequestsFromMapFunc(r.mapMLflowToNamespaces),
		).
		WatchesRawSource(
			source.Kind(
				r.AuthWatchCache,
				authObj,
				handler.TypedEnqueueRequestsFromMapFunc(r.mapAuthToNamespaces),
			),
		).
		Complete(r)
}

func (r *NamespaceRBACReconciler) mapAuthToNamespaces(ctx context.Context, obj *unstructured.Unstructured) []reconcile.Request {
	if obj.GetName() != AuthCRName {
		return nil
	}
	return r.listLabeledNamespaceRequests(ctx)
}

func (r *NamespaceRBACReconciler) mapMLflowToNamespaces(ctx context.Context, obj client.Object) []reconcile.Request {
	nsList := &corev1.NamespaceList{}
	if err := r.List(ctx, nsList, client.HasLabels{NamespaceWorkspaceLabelKey}); err != nil {
		return nil
	}
	var requests []reconcile.Request
	for _, ns := range nsList.Items {
		if ns.Labels[NamespaceWorkspaceLabelKey] == obj.GetName() {
			requests = append(requests, reconcile.Request{
				NamespacedName: types.NamespacedName{Name: ns.Name},
			})
		}
	}
	return requests
}

func (r *NamespaceRBACReconciler) mapRoleBindingToNamespace(ctx context.Context, obj client.Object) []reconcile.Request {
	if obj.GetLabels()[NamespaceRBACLabelKey] != "true" {
		return nil
	}
	return []reconcile.Request{{
		NamespacedName: types.NamespacedName{Name: obj.GetNamespace()},
	}}
}

func (r *NamespaceRBACReconciler) listLabeledNamespaceRequests(ctx context.Context) []reconcile.Request {
	nsList := &corev1.NamespaceList{}
	if err := r.List(ctx, nsList, client.HasLabels{NamespaceWorkspaceLabelKey}); err != nil {
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

// NewAuthWatchCache creates a dedicated cache for the singleton Auth CR.
func NewAuthWatchCache(cfg *rest.Config, scheme *runtime.Scheme) (crcache.Cache, error) {
	authObj := &unstructured.Unstructured{}
	authObj.SetGroupVersionKind(authGVK)
	authFieldSelector := fields.OneTermEqualSelector("metadata.name", AuthCRName)
	return crcache.New(cfg, crcache.Options{
		Scheme: scheme,
		ByObject: map[client.Object]crcache.ByObject{
			authObj: {Field: authFieldSelector},
		},
	})
}

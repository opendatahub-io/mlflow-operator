package controller

import (
	"context"
	"testing"

	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

// ---- helpers ----

func newTestScheme(t *testing.T) *runtime.Scheme {
	t.Helper()
	s := runtime.NewScheme()
	for _, add := range []func(*runtime.Scheme) error{
		corev1.AddToScheme,
		rbacv1.AddToScheme,
		mlflowv1.AddToScheme,
	} {
		if err := add(s); err != nil {
			t.Fatalf("add scheme: %v", err)
		}
	}
	return s
}

func newAuthCR(allowedGroups, adminGroups []string) *unstructured.Unstructured {
	auth := &unstructured.Unstructured{}
	auth.SetGroupVersionKind(authGVK)
	auth.SetName(AuthCRName)
	spec := map[string]interface{}{}
	if allowedGroups != nil {
		iface := make([]interface{}, len(allowedGroups))
		for i, g := range allowedGroups {
			iface[i] = g
		}
		spec["allowedGroups"] = iface
	}
	if adminGroups != nil {
		iface := make([]interface{}, len(adminGroups))
		for i, g := range adminGroups {
			iface[i] = g
		}
		spec["adminGroups"] = iface
	}
	auth.Object["spec"] = spec
	return auth
}

func newLabeledNamespace(name, mlflowCRName string) *corev1.Namespace {
	return &corev1.Namespace{
		ObjectMeta: metav1.ObjectMeta{
			Name:   name,
			Labels: map[string]string{NamespaceWorkspaceLabelKey: mlflowCRName},
		},
	}
}

func newUnlabeledNamespace(name string) *corev1.Namespace {
	return &corev1.Namespace{
		ObjectMeta: metav1.ObjectMeta{Name: name},
	}
}

func newMLflowCR(name string) *mlflowv1.MLflow {
	return &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: name},
	}
}

func newManagedRoleBinding(name, namespace string) *rbacv1.RoleBinding {
	return &rbacv1.RoleBinding{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: namespace,
			Labels: map[string]string{
				NamespaceRBACLabelKey: "true",
				ManagedByLabelKey:     ManagedByLabelValue,
			},
		},
		RoleRef: rbacv1.RoleRef{
			APIGroup: "rbac.authorization.k8s.io",
			Kind:     "ClusterRole",
			Name:     ViewClusterRoleName,
		},
	}
}

func buildReconciler(t *testing.T, objects ...client.Object) (*NamespaceRBACReconciler, client.Client) {
	t.Helper()
	scheme := newTestScheme(t)
	c := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(objects...).
		Build()
	return &NamespaceRBACReconciler{Client: c, Scheme: scheme}, c
}

func reconcileNamespace(t *testing.T, r *NamespaceRBACReconciler, name string) reconcile.Result {
	t.Helper()
	result, err := r.Reconcile(context.Background(), reconcile.Request{
		NamespacedName: types.NamespacedName{Name: name},
	})
	if err != nil {
		t.Fatalf("reconcile %q: %v", name, err)
	}
	return result
}

func getRoleBindings(t *testing.T, c client.Client, namespace string) []rbacv1.RoleBinding {
	t.Helper()
	list := &rbacv1.RoleBindingList{}
	if err := c.List(context.Background(), list, client.InNamespace(namespace)); err != nil {
		t.Fatalf("list rolebindings in %q: %v", namespace, err)
	}
	return list.Items
}

func findRoleBinding(rbs []rbacv1.RoleBinding, name string) *rbacv1.RoleBinding {
	for i := range rbs {
		if rbs[i].Name == name {
			return &rbs[i]
		}
	}
	return nil
}

func subjectNames(subjects []rbacv1.Subject) []string {
	names := make([]string, len(subjects))
	for i, s := range subjects {
		names[i] = s.Name
	}
	return names
}

func containsAll(haystack, needles []string) bool {
	set := make(map[string]struct{}, len(haystack))
	for _, s := range haystack {
		set[s] = struct{}{}
	}
	for _, n := range needles {
		if _, ok := set[n]; !ok {
			return false
		}
	}
	return true
}

// ---- Trigger 1: Namespace label changes ----

func TestReconcileCreatesRoleBindingsForLabeledNamespace(t *testing.T) {
	r, c := buildReconciler(t,
		newLabeledNamespace("team-a", "mlflow"),
		newMLflowCR("mlflow"),
		newAuthCR([]string{"system:authenticated"}, []string{"rhods-admins"}),
	)

	reconcileNamespace(t, r, "team-a")

	rbs := getRoleBindings(t, c, "team-a")
	if len(rbs) != 2 {
		t.Fatalf("expected 2 rolebindings, got %d", len(rbs))
	}

	viewRB := findRoleBinding(rbs, RoleBindingViewName)
	if viewRB == nil {
		t.Fatal("mlflow-view RoleBinding not found")
	}
	if viewRB.RoleRef.Name != ViewClusterRoleName {
		t.Fatalf("expected roleRef %q, got %q", ViewClusterRoleName, viewRB.RoleRef.Name)
	}
	viewNames := subjectNames(viewRB.Subjects)
	if !containsAll(viewNames, []string{"system:authenticated", "rhods-admins"}) {
		t.Fatalf("view subjects should include both groups, got %v", viewNames)
	}

	editRB := findRoleBinding(rbs, RoleBindingEditName)
	if editRB == nil {
		t.Fatal("mlflow-edit RoleBinding not found")
	}
	if editRB.RoleRef.Name != EditClusterRoleName {
		t.Fatalf("expected roleRef %q, got %q", EditClusterRoleName, editRB.RoleRef.Name)
	}
	editNames := subjectNames(editRB.Subjects)
	if len(editNames) != 1 || editNames[0] != "rhods-admins" {
		t.Fatalf("edit subjects should be [rhods-admins], got %v", editNames)
	}

	if viewRB.Labels[NamespaceRBACLabelKey] != "true" {
		t.Fatal("view RoleBinding missing managed label")
	}
	if viewRB.Labels[ManagedByLabelKey] != ManagedByLabelValue {
		t.Fatal("view RoleBinding missing managed-by label")
	}
}

func TestReconcileRequeuesWhenAuthCRMissing(t *testing.T) {
	r, c := buildReconciler(t,
		newLabeledNamespace("team-a", "mlflow"),
		newMLflowCR("mlflow"),
	)

	result := reconcileNamespace(t, r, "team-a")

	if result.RequeueAfter == 0 {
		t.Fatal("expected requeue when Auth CR is missing")
	}

	rbs := getRoleBindings(t, c, "team-a")
	if len(rbs) != 0 {
		t.Fatalf("expected 0 rolebindings when Auth CR missing, got %d", len(rbs))
	}
}

func TestReconcileSkipsWhenMLflowCRMissing(t *testing.T) {
	r, c := buildReconciler(t,
		newLabeledNamespace("team-a", "mlflow"),
		newAuthCR([]string{"system:authenticated"}, []string{"rhods-admins"}),
	)

	reconcileNamespace(t, r, "team-a")

	rbs := getRoleBindings(t, c, "team-a")
	if len(rbs) != 0 {
		t.Fatalf("expected 0 rolebindings when MLflow CR missing, got %d", len(rbs))
	}
}

func TestReconcileCleansUpWhenMLflowCRDeleted(t *testing.T) {
	r, c := buildReconciler(t,
		newLabeledNamespace("team-a", "mlflow"),
		newMLflowCR("mlflow"),
		newAuthCR([]string{"system:authenticated"}, []string{"rhods-admins"}),
	)

	reconcileNamespace(t, r, "team-a")

	rbs := getRoleBindings(t, c, "team-a")
	if len(rbs) != 2 {
		t.Fatalf("expected 2 rolebindings before deletion, got %d", len(rbs))
	}

	if err := c.Delete(context.Background(), newMLflowCR("mlflow")); err != nil {
		t.Fatalf("delete mlflow CR: %v", err)
	}

	reconcileNamespace(t, r, "team-a")

	rbs = getRoleBindings(t, c, "team-a")
	if len(rbs) != 0 {
		t.Fatalf("expected 0 rolebindings after MLflow CR deleted, got %d", len(rbs))
	}
}

func TestReconcilePreservesRoleBindingsWhenAuthCRDeleted(t *testing.T) {
	r, c := buildReconciler(t,
		newLabeledNamespace("team-a", "mlflow"),
		newMLflowCR("mlflow"),
		newAuthCR([]string{"system:authenticated"}, []string{"rhods-admins"}),
	)

	reconcileNamespace(t, r, "team-a")

	rbs := getRoleBindings(t, c, "team-a")
	if len(rbs) != 2 {
		t.Fatalf("expected 2 rolebindings before Auth CR deletion, got %d", len(rbs))
	}

	auth := &unstructured.Unstructured{}
	auth.SetGroupVersionKind(authGVK)
	auth.SetName(AuthCRName)
	if err := c.Delete(context.Background(), auth); err != nil {
		t.Fatalf("delete auth CR: %v", err)
	}

	result := reconcileNamespace(t, r, "team-a")

	if result.RequeueAfter == 0 {
		t.Fatal("expected requeue when Auth CR is deleted")
	}

	rbs = getRoleBindings(t, c, "team-a")
	if len(rbs) != 2 {
		t.Fatalf("expected 2 rolebindings preserved after Auth CR deletion, got %d", len(rbs))
	}
}

func TestReconcileCleansUpWhenLabelRemoved(t *testing.T) {
	r, c := buildReconciler(t,
		newUnlabeledNamespace("team-a"),
		newManagedRoleBinding(RoleBindingViewName, "team-a"),
		newManagedRoleBinding(RoleBindingEditName, "team-a"),
	)

	reconcileNamespace(t, r, "team-a")

	rbs := getRoleBindings(t, c, "team-a")
	if len(rbs) != 0 {
		t.Fatalf("expected 0 rolebindings after label removal, got %d", len(rbs))
	}
}

func TestReconcileCleanupNoopsWhenNoRoleBindings(t *testing.T) {
	r, _ := buildReconciler(t, newUnlabeledNamespace("team-b"))

	reconcileNamespace(t, r, "team-b")
}

func TestReconcileHandlesDeletedNamespace(t *testing.T) {
	r, _ := buildReconciler(t)

	reconcileNamespace(t, r, "nonexistent")
}

func TestReconcileMultipleNamespaces(t *testing.T) {
	r, c := buildReconciler(t,
		newLabeledNamespace("team-a", "mlflow"),
		newLabeledNamespace("team-b", "mlflow"),
		newMLflowCR("mlflow"),
		newAuthCR([]string{"system:authenticated"}, []string{"rhods-admins"}),
	)

	reconcileNamespace(t, r, "team-a")
	reconcileNamespace(t, r, "team-b")

	rbsA := getRoleBindings(t, c, "team-a")
	rbsB := getRoleBindings(t, c, "team-b")
	if len(rbsA) != 2 {
		t.Fatalf("expected 2 rolebindings in team-a, got %d", len(rbsA))
	}
	if len(rbsB) != 2 {
		t.Fatalf("expected 2 rolebindings in team-b, got %d", len(rbsB))
	}
}

// ---- Trigger 2: Auth CR changes ----

func TestReconcileUpdatesSubjectsWhenAuthGroupsChange(t *testing.T) {
	r, c := buildReconciler(t,
		newLabeledNamespace("team-a", "mlflow"),
		newMLflowCR("mlflow"),
		newAuthCR([]string{"system:authenticated"}, []string{"rhods-admins"}),
	)

	reconcileNamespace(t, r, "team-a")

	// Simulate Auth CR update: fetch current, modify, then update
	existing := &unstructured.Unstructured{}
	existing.SetGroupVersionKind(authGVK)
	if err := c.Get(context.Background(), types.NamespacedName{Name: AuthCRName}, existing); err != nil {
		t.Fatalf("get auth CR: %v", err)
	}
	existing.Object["spec"] = map[string]interface{}{
		"allowedGroups": []interface{}{"system:authenticated"},
		"adminGroups":   []interface{}{"dedicated-admins"},
	}
	if err := c.Update(context.Background(), existing); err != nil {
		t.Fatalf("update auth CR: %v", err)
	}

	reconcileNamespace(t, r, "team-a")

	rbs := getRoleBindings(t, c, "team-a")
	editRB := findRoleBinding(rbs, RoleBindingEditName)
	if editRB == nil {
		t.Fatal("mlflow-edit not found after update")
	}
	editNames := subjectNames(editRB.Subjects)
	if len(editNames) != 1 || editNames[0] != "dedicated-admins" {
		t.Fatalf("expected [dedicated-admins], got %v", editNames)
	}

	viewRB := findRoleBinding(rbs, RoleBindingViewName)
	viewNames := subjectNames(viewRB.Subjects)
	if !containsAll(viewNames, []string{"system:authenticated", "dedicated-admins"}) {
		t.Fatalf("view subjects should include updated admin group, got %v", viewNames)
	}
}

func TestReconcileHandlesEmptyGroups(t *testing.T) {
	r, c := buildReconciler(t,
		newLabeledNamespace("team-a", "mlflow"),
		newMLflowCR("mlflow"),
		newAuthCR([]string{}, []string{}),
	)

	reconcileNamespace(t, r, "team-a")

	rbs := getRoleBindings(t, c, "team-a")
	if len(rbs) != 2 {
		t.Fatalf("expected 2 rolebindings even with empty groups, got %d", len(rbs))
	}

	viewRB := findRoleBinding(rbs, RoleBindingViewName)
	if len(viewRB.Subjects) != 0 {
		t.Fatalf("expected 0 view subjects with empty groups, got %d", len(viewRB.Subjects))
	}
}

func TestReconcileFailsOnMalformedAuthGroups(t *testing.T) {
	cases := []struct {
		name string
		spec map[string]interface{}
	}{
		{
			name: "malformed allowedGroups",
			spec: map[string]interface{}{
				"allowedGroups": "not-a-slice",
				"adminGroups":   []interface{}{"rhods-admins"},
			},
		},
		{
			name: "malformed adminGroups",
			spec: map[string]interface{}{
				"allowedGroups": []interface{}{"rhods-users"},
				"adminGroups":   "not-a-slice",
			},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			auth := &unstructured.Unstructured{}
			auth.SetGroupVersionKind(authGVK)
			auth.SetName(AuthCRName)
			auth.Object["spec"] = tc.spec

			r, c := buildReconciler(t,
				newLabeledNamespace("team-a", "mlflow"),
				newMLflowCR("mlflow"),
				auth,
			)

			_, err := r.Reconcile(context.Background(), reconcile.Request{
				NamespacedName: types.NamespacedName{Name: "team-a"},
			})
			if err == nil {
				t.Fatalf("expected error when %s is malformed", tc.name)
			}

			rbs := getRoleBindings(t, c, "team-a")
			if len(rbs) != 0 {
				t.Fatalf("expected 0 rolebindings when Auth CR is malformed, got %d", len(rbs))
			}
		})
	}
}

func TestReconcileDeduplicatesOverlappingGroups(t *testing.T) {
	r, c := buildReconciler(t,
		newLabeledNamespace("team-a", "mlflow"),
		newMLflowCR("mlflow"),
		newAuthCR([]string{"shared-group", "users-only"}, []string{"shared-group", "admins-only"}),
	)

	reconcileNamespace(t, r, "team-a")

	rbs := getRoleBindings(t, c, "team-a")
	viewRB := findRoleBinding(rbs, RoleBindingViewName)
	if viewRB == nil {
		t.Fatal("view RoleBinding not found")
	}
	viewNames := subjectNames(viewRB.Subjects)
	seen := make(map[string]int)
	for _, n := range viewNames {
		seen[n]++
	}
	if seen["shared-group"] != 1 {
		t.Fatalf("shared-group should appear once in view subjects, got %d times in %v", seen["shared-group"], viewNames)
	}
	if len(viewNames) != 3 {
		t.Fatalf("expected 3 unique view subjects, got %d: %v", len(viewNames), viewNames)
	}
}

// ---- Trigger 3: RoleBinding drift/deletion (self-healing) ----

func TestReconcileRecreatesDeletedRoleBinding(t *testing.T) {
	r, c := buildReconciler(t,
		newLabeledNamespace("team-a", "mlflow"),
		newMLflowCR("mlflow"),
		newAuthCR([]string{"system:authenticated"}, []string{"rhods-admins"}),
	)

	reconcileNamespace(t, r, "team-a")

	rbs := getRoleBindings(t, c, "team-a")
	if len(rbs) != 2 {
		t.Fatalf("expected 2 rolebindings, got %d", len(rbs))
	}

	// Delete one RoleBinding externally
	viewRB := findRoleBinding(rbs, RoleBindingViewName)
	if err := c.Delete(context.Background(), viewRB); err != nil {
		t.Fatalf("delete view rb: %v", err)
	}

	// Reconcile should recreate it
	reconcileNamespace(t, r, "team-a")

	rbs = getRoleBindings(t, c, "team-a")
	if len(rbs) != 2 {
		t.Fatalf("expected 2 rolebindings after self-heal, got %d", len(rbs))
	}
}

func TestReconcileRestoresSubjectsAfterTampering(t *testing.T) {
	r, c := buildReconciler(t,
		newLabeledNamespace("team-a", "mlflow"),
		newMLflowCR("mlflow"),
		newAuthCR([]string{"system:authenticated"}, []string{"rhods-admins"}),
	)

	reconcileNamespace(t, r, "team-a")

	rbs := getRoleBindings(t, c, "team-a")
	viewRB := findRoleBinding(rbs, RoleBindingViewName)
	if viewRB == nil {
		t.Fatal("mlflow-view RoleBinding not found")
	}
	viewRB.Subjects = append(viewRB.Subjects, rbacv1.Subject{
		APIGroup: "rbac.authorization.k8s.io",
		Kind:     "Group",
		Name:     "rogue-group",
	})
	if err := c.Update(context.Background(), viewRB); err != nil {
		t.Fatalf("tamper view RB subjects: %v", err)
	}

	reconcileNamespace(t, r, "team-a")

	rbs = getRoleBindings(t, c, "team-a")
	viewRB = findRoleBinding(rbs, RoleBindingViewName)
	viewNames := subjectNames(viewRB.Subjects)
	if containsAll(viewNames, []string{"rogue-group"}) {
		t.Fatalf("rogue-group should have been removed, subjects: %v", viewNames)
	}
	if !containsAll(viewNames, []string{"system:authenticated", "rhods-admins"}) {
		t.Fatalf("expected authorized groups restored, got %v", viewNames)
	}
	if len(viewNames) != 2 {
		t.Fatalf("expected exactly 2 view subjects after restore, got %d: %v", len(viewNames), viewNames)
	}
}

func TestReconcileCorrectsTamperedRoleRef(t *testing.T) {
	r, c := buildReconciler(t,
		newLabeledNamespace("team-a", "mlflow"),
		newMLflowCR("mlflow"),
		newAuthCR([]string{"system:authenticated"}, []string{"rhods-admins"}),
	)

	reconcileNamespace(t, r, "team-a")

	rbs := getRoleBindings(t, c, "team-a")
	viewRB := findRoleBinding(rbs, RoleBindingViewName)
	if viewRB == nil {
		t.Fatal("mlflow-view RoleBinding not found")
	}
	viewRB.RoleRef.Name = "attacker-role"
	if err := c.Update(context.Background(), viewRB); err != nil {
		t.Fatalf("tamper view RB roleRef: %v", err)
	}

	reconcileNamespace(t, r, "team-a")

	rbs = getRoleBindings(t, c, "team-a")
	if len(rbs) != 2 {
		t.Fatalf("expected 2 rolebindings after roleRef correction, got %d", len(rbs))
	}
	viewRB = findRoleBinding(rbs, RoleBindingViewName)
	if viewRB == nil {
		t.Fatal("mlflow-view RoleBinding not found after correction")
	}
	if viewRB.RoleRef.Name != ViewClusterRoleName {
		t.Fatalf("expected roleRef %q after correction, got %q", ViewClusterRoleName, viewRB.RoleRef.Name)
	}
	viewNames := subjectNames(viewRB.Subjects)
	if !containsAll(viewNames, []string{"system:authenticated", "rhods-admins"}) {
		t.Fatalf("expected correct subjects after roleRef correction, got %v", viewNames)
	}
}

// ---- Mapper function tests ----

func TestMapAuthToNamespacesReturnsAllLabeledNamespaces(t *testing.T) {
	r, _ := buildReconciler(t,
		newLabeledNamespace("team-a", "mlflow"),
		newLabeledNamespace("team-b", "mlflow"),
		newUnlabeledNamespace("team-c"),
	)

	auth := &unstructured.Unstructured{}
	auth.SetGroupVersionKind(authGVK)
	auth.SetName(AuthCRName)

	requests := r.mapAuthToNamespaces(context.Background(), auth)

	if len(requests) != 2 {
		t.Fatalf("expected 2 requests, got %d", len(requests))
	}
	names := make(map[string]bool)
	for _, req := range requests {
		names[req.Name] = true
	}
	if !names["team-a"] || !names["team-b"] {
		t.Fatalf("expected team-a and team-b, got %v", names)
	}
}

func TestMapAuthToNamespacesSkipsNonAuthCR(t *testing.T) {
	r, _ := buildReconciler(t, newLabeledNamespace("team-a", "mlflow"))

	auth := &unstructured.Unstructured{}
	auth.SetGroupVersionKind(authGVK)
	auth.SetName("not-auth")

	requests := r.mapAuthToNamespaces(context.Background(), auth)
	if len(requests) != 0 {
		t.Fatalf("expected 0 requests for non-auth CR, got %d", len(requests))
	}
}

func TestMapRoleBindingToNamespace(t *testing.T) {
	r, _ := buildReconciler(t)

	rb := newManagedRoleBinding(RoleBindingViewName, "team-a")
	requests := r.mapRoleBindingToNamespace(context.Background(), rb)
	if len(requests) != 1 || requests[0].Name != "team-a" {
		t.Fatalf("expected [team-a], got %v", requests)
	}
}

func TestMapRoleBindingToNamespaceSkipsUnmanagedRB(t *testing.T) {
	r, _ := buildReconciler(t)

	rb := &rbacv1.RoleBinding{
		ObjectMeta: metav1.ObjectMeta{Name: "unmanaged", Namespace: "team-a"},
	}
	requests := r.mapRoleBindingToNamespace(context.Background(), rb)
	if len(requests) != 0 {
		t.Fatalf("expected 0 requests for unmanaged rb, got %d", len(requests))
	}
}

func TestMapMLflowToNamespacesFiltersbyLabelValue(t *testing.T) {
	r, _ := buildReconciler(t,
		newLabeledNamespace("team-a", "mlflow"),
		newLabeledNamespace("team-b", "other-mlflow"),
		newLabeledNamespace("team-c", "mlflow"),
	)

	mlflow := newMLflowCR("mlflow")
	requests := r.mapMLflowToNamespaces(context.Background(), mlflow)

	if len(requests) != 2 {
		t.Fatalf("expected 2 requests for mlflow CR, got %d", len(requests))
	}
	names := make(map[string]bool)
	for _, req := range requests {
		names[req.Name] = true
	}
	if !names["team-a"] || !names["team-c"] {
		t.Fatalf("expected team-a and team-c, got %v", names)
	}

	// Verify that a different MLflow CR name only matches its label value
	other := newMLflowCR("other-mlflow")
	otherRequests := r.mapMLflowToNamespaces(context.Background(), other)
	if len(otherRequests) != 1 || otherRequests[0].Name != "team-b" {
		t.Fatalf("expected [team-b] for other-mlflow CR, got %v", otherRequests)
	}
}

// ---- Helper function tests ----

func TestUniqueGroups(t *testing.T) {
	result := uniqueGroups([]string{"a", "b"}, []string{"b", "c"})
	if len(result) != 3 {
		t.Fatalf("expected 3 unique groups, got %d: %v", len(result), result)
	}
}

func TestBuildGroupSubjects(t *testing.T) {
	subjects := buildGroupSubjects([]string{"group-a", "group-b"})
	if len(subjects) != 2 {
		t.Fatalf("expected 2 subjects, got %d", len(subjects))
	}
	if subjects[0].Kind != "Group" || subjects[0].APIGroup != "rbac.authorization.k8s.io" {
		t.Fatalf("unexpected subject: %+v", subjects[0])
	}
}

package controller

import (
	"context"
	"strings"
	"testing"
	"time"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	modulev1alpha1 "github.com/opendatahub-io/mlflow-operator/api/mlflowoperator/v1alpha1"
	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

func TestMLflowOperatorReconcileAddsFinalizerAndReadyStatus(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := modulev1alpha1.AddToScheme(scheme); err != nil {
		t.Fatalf("add MLflowOperator scheme: %v", err)
	}
	if err := mlflowv1.AddToScheme(scheme); err != nil {
		t.Fatalf("add MLflow scheme: %v", err)
	}

	module := &modulev1alpha1.MLflowOperator{
		ObjectMeta: metav1.ObjectMeta{
			Name:       modulev1alpha1.MLflowOperatorInstanceName,
			Generation: 7,
		},
		Spec: modulev1alpha1.MLflowOperatorSpec{
			MLflowOperatorCommonSpec: modulev1alpha1.MLflowOperatorCommonSpec{
				GatewayName:  "data-science-gateway",
				SectionTitle: "OpenShift Open Data Hub",
			},
		},
	}

	client := fake.NewClientBuilder().
		WithScheme(scheme).
		WithStatusSubresource(&modulev1alpha1.MLflowOperator{}).
		WithObjects(module).
		Build()

	reconciler := &MLflowOperatorReconciler{Client: client, Scheme: scheme}
	request := reconcile.Request{NamespacedName: types.NamespacedName{Name: modulev1alpha1.MLflowOperatorInstanceName}}

	if _, err := reconciler.Reconcile(context.Background(), request); err != nil {
		t.Fatalf("first reconcile: %v", err)
	}
	if err := client.Get(context.Background(), request.NamespacedName, module); err != nil {
		t.Fatalf("get module after first reconcile: %v", err)
	}
	if !containsString(module.Finalizers, mlflowOperatorFinalizer) {
		t.Fatalf("expected finalizer %q, got %v", mlflowOperatorFinalizer, module.Finalizers)
	}

	if _, err := reconciler.Reconcile(context.Background(), request); err != nil {
		t.Fatalf("second reconcile: %v", err)
	}
	if err := client.Get(context.Background(), request.NamespacedName, module); err != nil {
		t.Fatalf("get module after second reconcile: %v", err)
	}

	ready := findModuleStatusCondition(module.Status.Conditions)
	if ready == nil {
		t.Fatalf("expected Ready condition, got none")
	}
	if ready.Status != metav1.ConditionTrue {
		t.Fatalf("expected Ready=True, got %s", ready.Status)
	}
	if ready.Reason != readyReason {
		t.Fatalf("expected Ready reason %q, got %q", readyReason, ready.Reason)
	}
	if ready.Message != "MLflowOperator is ready to manage MLflow custom resources" {
		t.Fatalf("expected Ready message to explain module scope, got %q", ready.Message)
	}
	if module.Status.ObservedGeneration != module.Generation {
		t.Fatalf("expected observedGeneration %d, got %d", module.Generation, module.Status.ObservedGeneration)
	}
	if module.Status.Phase != phaseReady {
		t.Fatalf("expected phase %q, got %q", phaseReady, module.Status.Phase)
	}
}

func TestMLflowOperatorReconcileBlocksReadyUntilRequiredProjectedFieldsExist(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := modulev1alpha1.AddToScheme(scheme); err != nil {
		t.Fatalf("add MLflowOperator scheme: %v", err)
	}
	if err := mlflowv1.AddToScheme(scheme); err != nil {
		t.Fatalf("add MLflow scheme: %v", err)
	}

	module := &modulev1alpha1.MLflowOperator{
		ObjectMeta: metav1.ObjectMeta{
			Name:       modulev1alpha1.MLflowOperatorInstanceName,
			Generation: 4,
		},
		Spec: modulev1alpha1.MLflowOperatorSpec{
			MLflowOperatorCommonSpec: modulev1alpha1.MLflowOperatorCommonSpec{
				GatewayName: "data-science-gateway",
			},
		},
	}

	client := fake.NewClientBuilder().
		WithScheme(scheme).
		WithStatusSubresource(&modulev1alpha1.MLflowOperator{}).
		WithObjects(module).
		Build()

	reconciler := &MLflowOperatorReconciler{Client: client, Scheme: scheme}
	request := reconcile.Request{NamespacedName: types.NamespacedName{Name: modulev1alpha1.MLflowOperatorInstanceName}}

	if _, err := reconciler.Reconcile(context.Background(), request); err != nil {
		t.Fatalf("first reconcile: %v", err)
	}
	if _, err := reconciler.Reconcile(context.Background(), request); err != nil {
		t.Fatalf("second reconcile: %v", err)
	}
	if err := client.Get(context.Background(), request.NamespacedName, module); err != nil {
		t.Fatalf("get module after reconcile: %v", err)
	}

	ready := findModuleStatusCondition(module.Status.Conditions)
	if ready == nil {
		t.Fatalf("expected Ready condition, got none")
	}
	if ready.Status != metav1.ConditionFalse {
		t.Fatalf("expected Ready=False when required projected fields are missing, got %s", ready.Status)
	}
	if ready.Reason != configPendingReason {
		t.Fatalf("expected Ready reason %q, got %q", configPendingReason, ready.Reason)
	}
	if !strings.Contains(ready.Message, "spec.sectionTitle") {
		t.Fatalf("expected Ready message to mention missing spec.sectionTitle, got %q", ready.Message)
	}
	if module.Status.Phase != phaseProgressing {
		t.Fatalf("expected phase %q while config is incomplete, got %q", phaseProgressing, module.Status.Phase)
	}
}

func TestMLflowOperatorReconcileAllowsOptionalGatewayDomainToBeEmpty(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := modulev1alpha1.AddToScheme(scheme); err != nil {
		t.Fatalf("add MLflowOperator scheme: %v", err)
	}
	if err := mlflowv1.AddToScheme(scheme); err != nil {
		t.Fatalf("add MLflow scheme: %v", err)
	}

	module := &modulev1alpha1.MLflowOperator{
		ObjectMeta: metav1.ObjectMeta{
			Name:       modulev1alpha1.MLflowOperatorInstanceName,
			Generation: 5,
		},
		Spec: modulev1alpha1.MLflowOperatorSpec{
			MLflowOperatorCommonSpec: modulev1alpha1.MLflowOperatorCommonSpec{
				GatewayName:  "data-science-gateway",
				SectionTitle: "OpenShift Open Data Hub",
			},
		},
	}

	client := fake.NewClientBuilder().
		WithScheme(scheme).
		WithStatusSubresource(&modulev1alpha1.MLflowOperator{}).
		WithObjects(module).
		Build()

	reconciler := &MLflowOperatorReconciler{Client: client, Scheme: scheme}
	request := reconcile.Request{NamespacedName: types.NamespacedName{Name: modulev1alpha1.MLflowOperatorInstanceName}}

	if _, err := reconciler.Reconcile(context.Background(), request); err != nil {
		t.Fatalf("first reconcile: %v", err)
	}
	if _, err := reconciler.Reconcile(context.Background(), request); err != nil {
		t.Fatalf("second reconcile: %v", err)
	}
	if err := client.Get(context.Background(), request.NamespacedName, module); err != nil {
		t.Fatalf("get module after reconcile: %v", err)
	}

	ready := findModuleStatusCondition(module.Status.Conditions)
	if ready == nil || ready.Status != metav1.ConditionTrue {
		t.Fatalf("expected Ready=True without gateway domain when required projected fields exist, got %#v", ready)
	}
}

func TestMLflowOperatorDeletionBlockedWhenMLflowInstancesExist(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := modulev1alpha1.AddToScheme(scheme); err != nil {
		t.Fatalf("add MLflowOperator scheme: %v", err)
	}
	if err := mlflowv1.AddToScheme(scheme); err != nil {
		t.Fatalf("add MLflow scheme: %v", err)
	}

	now := metav1.NewTime(time.Now())
	module := &modulev1alpha1.MLflowOperator{
		ObjectMeta: metav1.ObjectMeta{
			Name:              modulev1alpha1.MLflowOperatorInstanceName,
			Generation:        3,
			Finalizers:        []string{mlflowOperatorFinalizer},
			DeletionTimestamp: &now,
		},
	}
	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
	}

	client := fake.NewClientBuilder().
		WithScheme(scheme).
		WithStatusSubresource(&modulev1alpha1.MLflowOperator{}).
		WithObjects(module, mlflow).
		Build()

	reconciler := &MLflowOperatorReconciler{Client: client, Scheme: scheme}
	request := reconcile.Request{NamespacedName: types.NamespacedName{Name: modulev1alpha1.MLflowOperatorInstanceName}}

	if _, err := reconciler.Reconcile(context.Background(), request); err != nil {
		t.Fatalf("reconcile deleting module: %v", err)
	}
	if err := client.Get(context.Background(), request.NamespacedName, module); err != nil {
		t.Fatalf("get module after reconcile: %v", err)
	}
	if !containsString(module.Finalizers, mlflowOperatorFinalizer) {
		t.Fatalf("expected finalizer to remain while MLflow instances exist")
	}

	ready := findModuleStatusCondition(module.Status.Conditions)
	if ready == nil {
		t.Fatalf("expected Ready condition while deletion is blocked")
	}
	if ready.Status != metav1.ConditionFalse {
		t.Fatalf("expected Ready=False, got %s", ready.Status)
	}
	if ready.Reason != mlflowInstancesReason {
		t.Fatalf("expected reason %q, got %q", mlflowInstancesReason, ready.Reason)
	}
	if module.Status.Phase != phaseProgressing {
		t.Fatalf("expected phase %q while deletion is blocked, got %q", phaseProgressing, module.Status.Phase)
	}
}

func TestMLflowOperatorDeletionRemovesFinalizerWhenSafe(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := modulev1alpha1.AddToScheme(scheme); err != nil {
		t.Fatalf("add MLflowOperator scheme: %v", err)
	}
	if err := mlflowv1.AddToScheme(scheme); err != nil {
		t.Fatalf("add MLflow scheme: %v", err)
	}

	now := metav1.NewTime(time.Now())
	module := &modulev1alpha1.MLflowOperator{
		ObjectMeta: metav1.ObjectMeta{
			Name:              modulev1alpha1.MLflowOperatorInstanceName,
			Finalizers:        []string{mlflowOperatorFinalizer},
			DeletionTimestamp: &now,
		},
	}

	client := fake.NewClientBuilder().
		WithScheme(scheme).
		WithStatusSubresource(&modulev1alpha1.MLflowOperator{}).
		WithObjects(module).
		Build()

	reconciler := &MLflowOperatorReconciler{Client: client, Scheme: scheme}
	request := reconcile.Request{NamespacedName: types.NamespacedName{Name: modulev1alpha1.MLflowOperatorInstanceName}}

	if _, err := reconciler.Reconcile(context.Background(), request); err != nil {
		t.Fatalf("reconcile deleting module without MLflows: %v", err)
	}
	if err := client.Get(context.Background(), request.NamespacedName, module); !apierrors.IsNotFound(err) {
		t.Fatalf("expected deleting module to disappear after finalizer removal, got err=%v", err)
	}
}

func containsString(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

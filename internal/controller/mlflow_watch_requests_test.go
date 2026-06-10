package controller

import (
	"context"
	"slices"
	"testing"

	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	modulev1alpha1 "github.com/opendatahub-io/mlflow-operator/api/mlflowoperator/v1alpha1"
	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

func TestMLflowOperatorToMLflowRequestsEnqueuesAllMLflows(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := modulev1alpha1.AddToScheme(scheme); err != nil {
		t.Fatalf("add MLflowOperator scheme: %v", err)
	}
	if err := mlflowv1.AddToScheme(scheme); err != nil {
		t.Fatalf("add MLflow scheme: %v", err)
	}

	module := &modulev1alpha1.MLflowOperator{}
	module.Name = modulev1alpha1.MLflowOperatorInstanceName

	mlflowA := &mlflowv1.MLflow{}
	mlflowA.Name = "mlflow-a"
	mlflowB := &mlflowv1.MLflow{}
	mlflowB.Name = "mlflow-b"

	client := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(module, mlflowA, mlflowB).
		Build()

	reconciler := &MLflowReconciler{Client: client, Scheme: scheme}
	requests := reconciler.mlflowOperatorToMLflowRequests(context.Background(), module)
	if len(requests) != 2 {
		t.Fatalf("expected 2 MLflow reconcile requests, got %d", len(requests))
	}

	gotNames := []string{requests[0].Name, requests[1].Name}
	slices.Sort(gotNames)
	if !slices.Equal(gotNames, []string{"mlflow-a", "mlflow-b"}) {
		t.Fatalf("expected MLflow names [mlflow-a mlflow-b], got %v", gotNames)
	}
}

func TestMLflowOperatorToMLflowRequestsIgnoresUnexpectedModuleName(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := modulev1alpha1.AddToScheme(scheme); err != nil {
		t.Fatalf("add MLflowOperator scheme: %v", err)
	}
	if err := mlflowv1.AddToScheme(scheme); err != nil {
		t.Fatalf("add MLflow scheme: %v", err)
	}

	module := &modulev1alpha1.MLflowOperator{}
	module.Name = "other-mlflowoperator"

	client := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(module).
		Build()

	reconciler := &MLflowReconciler{Client: client, Scheme: scheme}
	requests := reconciler.mlflowOperatorToMLflowRequests(context.Background(), module)
	if len(requests) != 0 {
		t.Fatalf("expected no requests for unexpected module name, got %d", len(requests))
	}
}

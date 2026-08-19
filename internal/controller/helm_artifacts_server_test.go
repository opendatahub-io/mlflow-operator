package controller

import (
	"slices"
	"strings"
	"testing"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	"github.com/opendatahub-io/mlflow-operator/internal/config"
)

func TestRenderChartArtifactsServer(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")
	operatorConfig := &config.OperatorConfig{
		MLflowImage:         "quay.io/opendatahub/mlflow:test",
		MLflowURL:           "https://gateway.example.com/base/",
		MLflowURLConfigured: true,
	}
	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: ResourceName},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI:      ptr("postgresql://db.example.com/mlflow"),
			ArtifactsDestination: ptr("s3://bucket/artifacts"),
			Workers:              ptr(int32(3)),
			WorkspaceLabelSelector: &metav1.LabelSelector{
				MatchLabels: map[string]string{"mlflow-workspace": "true"},
			},
			Env: []corev1.EnvVar{{Name: "AWS_DEFAULT_REGION", Value: "us-east-1"}},
			EnvFrom: []corev1.EnvFromSource{{
				SecretRef: &corev1.SecretEnvSource{LocalObjectReference: corev1.LocalObjectReference{Name: "s3-credentials"}},
			}},
			ArtifactsServer: &mlflowv1.ArtifactsServerSpec{
				Enabled:  true,
				Replicas: ptr(int32(2)),
				Workers:  ptr(int32(4)),
				Resources: &corev1.ResourceRequirements{
					Requests: corev1.ResourceList{corev1.ResourceCPU: resource.MustParse("500m")},
				},
			},
		},
	}

	objects, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{IsOpenShift: true}, operatorConfig)
	if err != nil {
		t.Fatalf("RenderChart() error = %v", err)
	}

	tracking, err := renderedDeployment(objects, ResourceName, "test-ns")
	if err != nil {
		t.Fatal(err)
	}
	artifacts, err := renderedDeployment(objects, ArtifactsResourceName, "test-ns")
	if err != nil {
		t.Fatal(err)
	}
	if got := artifacts.Labels["app"]; got != ResourceName {
		t.Errorf("artifact Deployment cache label = %q, want %q", got, ResourceName)
	}

	trackingArgs := tracking.Spec.Template.Spec.Containers[0].Args
	if !slices.Contains(trackingArgs, "--no-serve-artifacts") {
		t.Errorf("tracking args missing --no-serve-artifacts: %v", trackingArgs)
	}
	if !slices.Contains(trackingArgs, "--default-artifact-root=https://gateway.example.com/base/mlflow-artifacts/api/2.0/mlflow-artifacts/artifacts") {
		t.Errorf("tracking args missing generated artifact root: %v", trackingArgs)
	}
	if slices.Contains(trackingArgs, "--artifacts-only") {
		t.Errorf("tracking args unexpectedly contain --artifacts-only: %v", trackingArgs)
	}
	if !slices.Contains(trackingArgs, "--workers=3") {
		t.Errorf("tracking args missing custom worker count: %v", trackingArgs)
	}

	if artifacts.Spec.Replicas == nil || *artifacts.Spec.Replicas != 2 {
		t.Fatalf("artifact replicas = %v, want 2", artifacts.Spec.Replicas)
	}
	container := artifacts.Spec.Template.Spec.Containers[0]
	for _, arg := range []string{
		"--artifacts-only",
		"--artifacts-destination=s3://bucket/artifacts",
		"--app-name=kubernetes-auth",
		"--enable-workspaces",
		"--workspace-store-uri=kubernetes://",
		"--static-prefix=/mlflow-artifacts",
		"--workers=4",
	} {
		if !slices.Contains(container.Args, arg) {
			t.Errorf("artifact args missing %q: %v", arg, container.Args)
		}
	}
	if container.ReadinessProbe == nil || container.ReadinessProbe.HTTPGet == nil || container.ReadinessProbe.HTTPGet.Path != "/mlflow-artifacts/health" {
		t.Errorf("artifact readiness probe = %#v, want /mlflow-artifacts/health", container.ReadinessProbe)
	}
	if container.Resources.Requests.Cpu().String() != "500m" {
		t.Errorf("artifact CPU request = %s, want 500m", container.Resources.Requests.Cpu().String())
	}
	if len(container.EnvFrom) != 1 || container.EnvFrom[0].SecretRef == nil || container.EnvFrom[0].SecretRef.Name != "s3-credentials" {
		t.Errorf("artifact envFrom = %#v, want s3-credentials", container.EnvFrom)
	}
	if !hasEnvValue(container.Env, "MLFLOW_K8S_WORKSPACE_LABEL_SELECTOR", "mlflow-workspace=true") {
		t.Errorf("artifact workspace selector env missing: %#v", container.Env)
	}

	artifactService := findObject(objects, "Service", ArtifactsResourceName)
	if artifactService == nil {
		t.Fatal("artifacts Service was not rendered")
	} else if got := artifactService.GetLabels()["app"]; got != ResourceName {
		t.Errorf("artifact Service cache label = %q, want %q", got, ResourceName)
	}
	if !hasSecretVolume(artifacts.Spec.Template.Spec.Volumes, ArtifactsTLSSecretName) {
		t.Errorf("artifacts Deployment does not mount TLS secret %q", ArtifactsTLSSecretName)
	}

	if artifacts.Spec.Template.Spec.ServiceAccountName != tracking.Spec.Template.Spec.ServiceAccountName {
		t.Errorf(
			"artifact service account = %q, want shared tracking service account %q",
			artifacts.Spec.Template.Spec.ServiceAccountName,
			tracking.Spec.Template.Spec.ServiceAccountName,
		)
	}
	sharedNetworkPolicy := findObject(objects, "NetworkPolicy", ResourceName)
	if sharedNetworkPolicy == nil {
		t.Fatal("shared MLflow NetworkPolicy was not rendered")
		return
	}
	instanceLabel, found, err := unstructured.NestedString(
		sharedNetworkPolicy.Object,
		"spec", "podSelector", "matchLabels", "app.kubernetes.io/instance",
	)
	if err != nil || !found {
		t.Fatalf("shared NetworkPolicy instance selector: found=%v, err=%v", found, err)
	}
	if got := artifacts.Spec.Template.Labels["app.kubernetes.io/instance"]; got != instanceLabel {
		t.Errorf("artifact instance label = %q, want shared NetworkPolicy selector %q", got, instanceLabel)
	}
	if renderedArtifactObjectExists(objects, "NetworkPolicy", "test-ns") {
		t.Fatal("artifact-specific NetworkPolicy was rendered instead of reusing the shared policy")
	}
	if renderedArtifactObjectExists(objects, "ClusterRoleBinding", "") {
		t.Fatal("artifact-specific ClusterRoleBinding was rendered instead of reusing server RBAC")
	}
}

func TestRenderChartArtifactsServerDisabled(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")
	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: ResourceName},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI:     ptr("postgresql://db.example.com/mlflow"),
			DefaultArtifactRoot: ptr("s3://bucket/artifacts"),
		},
	}

	objects, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{}, nil)
	if err != nil {
		t.Fatalf("RenderChart() error = %v", err)
	}
	if renderedArtifactObjectExists(objects, "Deployment", "test-ns") || renderedArtifactObjectExists(objects, "Service", "test-ns") {
		t.Fatal("artifacts resources rendered while artifactsServer is disabled")
	}
}

func TestRenderChartArtifactsServerInheritsResources(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")
	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: ResourceName},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI:      ptr("postgresql://db.example.com/mlflow"),
			ArtifactsDestination: ptr("s3://bucket/artifacts"),
			Resources: &corev1.ResourceRequirements{
				Requests: corev1.ResourceList{corev1.ResourceCPU: resource.MustParse("250m")},
			},
			ArtifactsServer: &mlflowv1.ArtifactsServerSpec{Enabled: true},
		},
	}

	objects, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{}, &config.OperatorConfig{
		MLflowURL:           "https://gateway.example.com",
		MLflowURLConfigured: true,
	})
	if err != nil {
		t.Fatalf("RenderChart() error = %v", err)
	}
	artifacts, err := renderedDeployment(objects, ArtifactsResourceName, "test-ns")
	if err != nil {
		t.Fatal(err)
	}
	if got := artifacts.Spec.Template.Spec.Containers[0].Resources.Requests.Cpu().String(); got != "250m" {
		t.Errorf("artifact CPU request = %s, want inherited 250m", got)
	}
	if artifacts.Spec.Replicas == nil || *artifacts.Spec.Replicas != 1 {
		t.Fatalf("artifact replicas = %v, want default 1", artifacts.Spec.Replicas)
	}
	if args := artifacts.Spec.Template.Spec.Containers[0].Args; !slices.Contains(args, "--workers=1") {
		t.Errorf("artifact args missing default worker count: %v", args)
	}
}

func TestRenderChartArtifactsServerRequiresExternalURL(t *testing.T) {
	renderer := NewHelmRenderer("../../charts/mlflow")
	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: ResourceName},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI:      ptr("postgresql://db.example.com/mlflow"),
			ArtifactsDestination: ptr("s3://bucket/artifacts"),
			ArtifactsServer:      &mlflowv1.ArtifactsServerSpec{Enabled: true},
		},
	}

	_, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{}, nil)
	if err == nil || !strings.Contains(err.Error(), "requires an explicitly configured external MLflow URL") {
		t.Fatalf("RenderChart() error = %v, want external URL requirement", err)
	}
}

func renderedArtifactObjectExists(objects []*unstructured.Unstructured, kind, namespace string) bool {
	for _, object := range objects {
		if object.GetKind() == kind && object.GetName() == ArtifactsResourceName && object.GetNamespace() == namespace {
			return true
		}
	}
	return false
}

func hasEnvValue(env []corev1.EnvVar, name, value string) bool {
	for _, variable := range env {
		if variable.Name == name && variable.Value == value {
			return true
		}
	}
	return false
}

func hasSecretVolume(volumes []corev1.Volume, secretName string) bool {
	for _, volume := range volumes {
		if volume.Secret != nil && volume.Secret.SecretName == secretName {
			return true
		}
	}
	return false
}

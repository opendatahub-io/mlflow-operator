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
	"errors"
	"os"
	"strings"
	"testing"
	"time"

	gomega "github.com/onsi/gomega"
	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

type listErrorClient struct {
	client.Client
	listErr error
}

func (c listErrorClient) List(ctx context.Context, list client.ObjectList, opts ...client.ListOption) error {
	return c.listErr
}

func TestMigrationRequested(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name   string
		mlflow *mlflowv1.MLflow
		want   bool
	}{
		{
			name: "automatic defaults to requested when status version empty",
			mlflow: &mlflowv1.MLflow{
				Spec: mlflowv1.MLflowSpec{},
			},
			want: true,
		},
		{
			name: "automatic skips when supported version already recorded",
			mlflow: &mlflowv1.MLflow{
				Status: mlflowv1.MLflowStatus{Version: SupportedMLflowVersion},
			},
			want: false,
		},
		{
			name: "automatic runs when status version differs",
			mlflow: &mlflowv1.MLflow{
				Status: mlflowv1.MLflowStatus{Version: "3.9.0"},
			},
			want: true,
		},
		{
			name: "always runs even when supported version already recorded",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Generation: 3},
				Spec: mlflowv1.MLflowSpec{
					Migration: &mlflowv1.MLflowMigrationConfig{Mode: mlflowv1.MLflowMigrateAlways},
				},
				Status: mlflowv1.MLflowStatus{Version: SupportedMLflowVersion},
			},
			want: true,
		},
		{
			name: "always skips when current desired generation already migrated",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Generation: 3},
				Spec: mlflowv1.MLflowSpec{
					Migration: &mlflowv1.MLflowMigrationConfig{Mode: mlflowv1.MLflowMigrateAlways},
				},
				Status: mlflowv1.MLflowStatus{
					Version: SupportedMLflowVersion,
					Conditions: []metav1.Condition{{
						Type:               migrationConditionType,
						Status:             metav1.ConditionTrue,
						ObservedGeneration: 3,
						Reason:             "MigrationSucceeded",
					}},
				},
			},
			want: false,
		},
		{
			name: "automatic still runs when supported version changed after current generation succeeded",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Generation: 3},
				Status: mlflowv1.MLflowStatus{
					Version: "3.9.0",
					Conditions: []metav1.Condition{{
						Type:               migrationConditionType,
						Status:             metav1.ConditionTrue,
						ObservedGeneration: 3,
						Reason:             "MigrationSucceeded",
					}},
				},
			},
			want: true,
		},
		{
			name: "terminal migration failure keeps migration flow handled for current generation",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Generation: 3},
				Status: mlflowv1.MLflowStatus{
					Version: "3.9.0",
					Conditions: []metav1.Condition{{
						Type:               migrationConditionType,
						Status:             metav1.ConditionFalse,
						ObservedGeneration: 3,
						Reason:             "MigrationFailed",
					}},
				},
			},
			want: true,
		},
		{
			name: "in-progress migration keeps migration flow handled for current generation",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Generation: 3},
				Status: mlflowv1.MLflowStatus{
					Version: "3.9.0",
					Conditions: []metav1.Condition{{
						Type:               migrationConditionType,
						Status:             metav1.ConditionUnknown,
						ObservedGeneration: 3,
						Reason:             "MigrationRunning",
					}},
				},
			},
			want: true,
		},
		{
			name: "force annotation triggers migration",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Annotations: map[string]string{forceMigrateAnnotation: ""},
				},
				Status: mlflowv1.MLflowStatus{Version: SupportedMLflowVersion},
			},
			want: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := migrationRequested(tt.mlflow); got != tt.want {
				t.Fatalf("migrationRequested() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestMigrationConditionWasForceTriggered(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name      string
		condition *metav1.Condition
		want      bool
	}{
		{
			name:      "nil condition",
			condition: nil,
			want:      false,
		},
		{
			name: "generic running reason",
			condition: &metav1.Condition{
				Reason: migrationReasonRunning,
			},
			want: false,
		},
		{
			name: "force running reason",
			condition: &metav1.Condition{
				Reason: migrationReasonForceRunning,
			},
			want: true,
		},
		{
			name: "force restart requested reason",
			condition: &metav1.Condition{
				Reason: migrationReasonForceRestartRequested,
			},
			want: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := migrationConditionWasForceTriggered(tt.condition); got != tt.want {
				t.Fatalf("migrationConditionWasForceTriggered() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestBuildMigrationJobFromDeployment(t *testing.T) {
	g := gomega.NewWithT(t)
	renderer := NewHelmRenderer("../../charts/mlflow")

	objs, err := renderer.RenderChart(&mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURIFrom: &corev1.SecretKeySelector{
				LocalObjectReference: corev1.LocalObjectReference{Name: "db-credentials"},
				Key:                  "backend-store-uri",
			},
			RegistryStoreURIFrom: &corev1.SecretKeySelector{
				LocalObjectReference: corev1.LocalObjectReference{Name: "registry-credentials"},
				Key:                  "registry-store-uri",
			},
			Storage:           &corev1.PersistentVolumeClaimSpec{},
			CABundleConfigMap: &mlflowv1.CABundleConfigMapSpec{Name: "custom-ca"},
			PodLabels: map[string]string{
				"team": "ml-platform",
			},
			PodAnnotations: map[string]string{
				"sidecar.istio.io/inject": "true",
			},
			ResourceClaims: []corev1.PodResourceClaim{{
				Name:                      "shared-gpu",
				ResourceClaimTemplateName: ptr("shared-gpu-template"),
			}},
			Resources: &corev1.ResourceRequirements{
				Claims: []corev1.ResourceClaim{{
					Name:    "shared-gpu",
					Request: "gpu",
				}},
			},
		},
	}, "test-ns", RenderOptions{PlatformTrustedCABundleExists: true}, nil)
	g.Expect(err).NotTo(gomega.HaveOccurred())

	deployment, err := renderedDeployment(objs, "mlflow", "test-ns")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	deployment.Spec.Template.Labels["custom-label"] = "custom-value"

	job, err := buildMigrationJobFromDeployment(&mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
	}, deployment, "test-ns")
	g.Expect(err).NotTo(gomega.HaveOccurred())

	g.Expect(job.Spec.Template.Spec.InitContainers).To(gomega.HaveLen(1))
	g.Expect(job.Spec.Template.Spec.InitContainers[0].Name).To(gomega.Equal("combine-ca-bundles"))
	g.Expect(job.Spec.Template.Spec.Containers).To(gomega.HaveLen(1))
	g.Expect(job.Spec.Template.Spec.ResourceClaims).To(gomega.BeNil())
	g.Expect(job.Spec.TTLSecondsAfterFinished).NotTo(gomega.BeNil())
	g.Expect(*job.Spec.TTLSecondsAfterFinished).To(gomega.Equal(int32(24 * 60 * 60)))
	g.Expect(job.Labels).To(gomega.HaveKeyWithValue("component", "mlflow-migration"))
	g.Expect(job.Labels).To(gomega.HaveKeyWithValue(MigrationJobLabelKey, "true"))
	g.Expect(job.Labels).To(gomega.HaveKeyWithValue(migrationJobInstanceLabel, "mlflow"))
	g.Expect(job.Spec.Template.Labels).To(gomega.HaveKeyWithValue("component", "mlflow-migration"))
	g.Expect(job.Spec.Template.Labels).To(gomega.HaveKeyWithValue(MigrationJobLabelKey, "true"))
	g.Expect(job.Spec.Template.Labels).To(gomega.HaveKeyWithValue(migrationJobInstanceLabel, "mlflow"))
	g.Expect(job.Spec.Template.Labels).To(gomega.HaveKeyWithValue("team", "ml-platform"))
	g.Expect(job.Spec.Template.Labels).To(gomega.HaveKeyWithValue("custom-label", "custom-value"))
	g.Expect(job.Spec.Template.Labels).NotTo(gomega.HaveKey("app"))
	g.Expect(job.Spec.Template.Annotations).To(gomega.BeNil())

	container := job.Spec.Template.Spec.Containers[0]
	g.Expect(container.Name).To(gomega.Equal(migrationJobContainerName))
	g.Expect(container.Command).To(gomega.Equal([]string{"/bin/sh", "-ec"}))
	g.Expect(container.Args).To(gomega.HaveLen(1))
	g.Expect(container.Args[0]).To(gomega.ContainSubstring("python3.12"))
	g.Expect(container.Args[0]).To(gomega.ContainSubstring("MIGRATION_PYTHON_SCRIPT"))
	g.Expect(container.Resources.Claims).To(gomega.BeNil())
	g.Expect(job.Spec.BackoffLimit).NotTo(gomega.BeNil())
	g.Expect(*job.Spec.BackoffLimit).To(gomega.Equal(int32(3)))

	envByName := map[string]corev1.EnvVar{}
	for _, env := range container.Env {
		envByName[env.Name] = env
	}
	g.Expect(envByName).To(gomega.HaveKey("MIGRATION_PYTHON_SCRIPT"))
	g.Expect(envByName["MIGRATION_PYTHON_SCRIPT"].Value).To(gomega.ContainSubstring("_initialize_tables"))
	g.Expect(envByName["MIGRATION_PYTHON_SCRIPT"].Value).To(gomega.ContainSubstring("registry_uri != backend_uri"))
	g.Expect(envByName).To(gomega.HaveKeyWithValue(supportedVersionEnvName, corev1.EnvVar{
		Name:  supportedVersionEnvName,
		Value: SupportedMLflowVersion,
	}))

	mountNames := make([]string, 0, len(container.VolumeMounts))
	for _, mount := range container.VolumeMounts {
		mountNames = append(mountNames, mount.Name)
	}
	g.Expect(mountNames).To(gomega.ContainElements("tmp", "mlflow-storage", "combined-ca-bundle"))

	volumeNames := make([]string, 0, len(job.Spec.Template.Spec.Volumes))
	for _, volume := range job.Spec.Template.Spec.Volumes {
		volumeNames = append(volumeNames, volume.Name)
	}
	g.Expect(volumeNames).To(gomega.ContainElements("tmp", "mlflow-storage", "combined-ca-bundle"))

	g.Expect(envByName).To(gomega.HaveKey("MLFLOW_BACKEND_STORE_URI"))
	g.Expect(envByName["MLFLOW_BACKEND_STORE_URI"].ValueFrom.SecretKeyRef.Name).To(gomega.Equal("db-credentials"))
	g.Expect(envByName).To(gomega.HaveKey("MLFLOW_REGISTRY_STORE_URI"))
	g.Expect(envByName["MLFLOW_REGISTRY_STORE_URI"].ValueFrom.SecretKeyRef.Name).To(gomega.Equal("registry-credentials"))
	g.Expect(envByName).To(gomega.HaveKeyWithValue("SSL_CERT_FILE", corev1.EnvVar{
		Name:  "SSL_CERT_FILE",
		Value: caCombinedBundle,
	}))

	customTTL := int32(60)
	job, err = buildMigrationJobFromDeployment(&mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec: mlflowv1.MLflowSpec{
			Migration: &mlflowv1.MLflowMigrationConfig{
				TTLSecondsAfterFinished: &customTTL,
			},
		},
	}, deployment, "test-ns")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(job.Spec.TTLSecondsAfterFinished).NotTo(gomega.BeNil())
	g.Expect(*job.Spec.TTLSecondsAfterFinished).To(gomega.Equal(customTTL))
}

func TestSupportedVersionEarlierThanStatusVersion(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name   string
		mlflow *mlflowv1.MLflow
		want   bool
	}{
		{
			name: "returns true when status version is newer",
			mlflow: &mlflowv1.MLflow{
				Status: mlflowv1.MLflowStatus{Version: "99.0.0"},
			},
			want: true,
		},
		{
			name: "returns false when versions match",
			mlflow: &mlflowv1.MLflow{
				Status: mlflowv1.MLflowStatus{Version: SupportedMLflowVersion},
			},
			want: false,
		},
		{
			name: "returns false when status version is not semver",
			mlflow: &mlflowv1.MLflow{
				Status: mlflowv1.MLflowStatus{Version: "custom-build"},
			},
			want: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := supportedVersionEarlierThanStatusVersion(tt.mlflow); got != tt.want {
				t.Fatalf("supportedVersionEarlierThanStatusVersion() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestIsJobFailedRequiresTerminalFailureCondition(t *testing.T) {
	t.Parallel()

	job := &batchv1.Job{
		Status: batchv1.JobStatus{
			Failed: 1,
			Conditions: []batchv1.JobCondition{
				{Type: batchv1.JobFailureTarget, Status: corev1.ConditionTrue},
			},
		},
	}

	if isJobFailed(job) {
		t.Fatal("isJobFailed() returned true before the JobFailed condition was present")
	}
	if isJobFinished(job) {
		t.Fatal("isJobFinished() returned true while the Job was still retrying")
	}
}

func TestNamespaceRoleIncludesJobsAndPods(t *testing.T) {
	t.Parallel()

	content, err := os.ReadFile("../../config/rbac/namespace_role.yaml")
	if err != nil {
		t.Fatalf("read namespace role: %v", err)
	}
	if !strings.Contains(string(content), "- jobs") {
		t.Fatal("namespace_role.yaml does not grant batch/jobs access")
	}
	if !strings.Contains(string(content), "- pods") {
		t.Fatal("namespace_role.yaml does not grant core/pods access")
	}
}

func TestMigrationScriptSupportsDriverQualifiedSQLAlchemyURIs(t *testing.T) {
	t.Parallel()

	if !strings.Contains(migrationPythonScript, `split("+", 1)[0]`) {
		t.Fatal("migrationPythonScript does not normalize SQLAlchemy driver-qualified URIs")
	}
}

func TestMigrationScriptIncludesRHOAIBackendGapFixHook(t *testing.T) {
	t.Parallel()

	if !strings.Contains(migrationPythonScript, "fix_migration_gap_if_needed") {
		t.Fatal("migrationPythonScript does not include the RHOAI 3.3 -> 3.4 gap fix hook")
	}
	if !strings.Contains(migrationPythonScript, `name != "backend"`) {
		t.Fatal("migrationPythonScript does not scope the RHOAI 3.3 -> 3.4 gap fix to the backend store")
	}
}

func TestMigrationScriptValidatesSupportedVersion(t *testing.T) {
	t.Parallel()

	if !strings.Contains(migrationPythonScript, "SUPPORTED_MLFLOW_VERSION") {
		t.Fatal("migrationPythonScript does not read the supported MLflow version from the environment")
	}
	if !strings.Contains(migrationPythonScript, "migration image reports MLflow") {
		t.Fatal("migrationPythonScript does not fail clearly when the MLflow image version does not match")
	}
}

func TestMigrationScriptDefinesSpecificExitCodes(t *testing.T) {
	t.Parallel()

	for _, snippet := range []string{
		"EXIT_VERSION_MISMATCH = 10",
		"EXIT_UNSUPPORTED_BACKEND = 11",
		"EXIT_UNSUPPORTED_REGISTRY = 12",
		"EXIT_REVISION_MISMATCH = 13",
		"EXIT_REVISION_RESOLUTION_FAILURE = 14",
		"EXIT_RETRYABLE_FAILURE = 15",
	} {
		if !strings.Contains(migrationPythonScript, snippet) {
			t.Fatalf("migrationPythonScript is missing %q", snippet)
		}
	}
}

func TestMigrationFailureMessageForExitCode(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name     string
		exitCode int32
		want     string
		ok       bool
	}{
		{
			name:     "version mismatch",
			exitCode: migrationScriptExitCodeVersionMismatch,
			want:     "migration image reports an unexpected MLflow version; expected " + SupportedMLflowVersion,
			ok:       true,
		},
		{
			name:     "unsupported backend",
			exitCode: migrationScriptExitCodeUnsupportedBackend,
			want:     "operator-managed migration only supports SQL backend store URIs",
			ok:       true,
		},
		{
			name:     "unsupported registry",
			exitCode: migrationScriptExitCodeUnsupportedRegistry,
			want:     "operator-managed migration only supports SQL registry store URIs",
			ok:       true,
		},
		{
			name:     "revision mismatch",
			exitCode: migrationScriptExitCodeRevisionMismatch,
			want:     "migration completed but the resulting schema revision does not match the image's Alembic head",
			ok:       true,
		},
		{
			name:     "revision resolution",
			exitCode: migrationScriptExitCodeRevisionResolution,
			want:     "migration failed because Alembic could not resolve the schema revision graph",
			ok:       true,
		},
		{
			name:     "retryable failure",
			exitCode: migrationScriptExitCodeRetryableFailure,
			want:     "migration failed due to a retryable database or migration error",
			ok:       true,
		},
		{
			name:     "unknown",
			exitCode: 99,
			want:     "",
			ok:       false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			got, ok := migrationFailureMessageForExitCode(tt.exitCode)
			if got != tt.want || ok != tt.ok {
				t.Fatalf("migrationFailureMessageForExitCode(%d) = (%q, %v), want (%q, %v)", tt.exitCode, got, ok, tt.want, tt.ok)
			}
		})
	}
}

func TestClassifyMigrationFailure(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name    string
		details migrationFailureDetails
		want    bool
	}{
		{
			name:    "mapped terminal exit code",
			details: migrationFailureDetails{exitCode: migrationScriptExitCodeVersionMismatch, hasExitCode: true, message: "version mismatch"},
			want:    true,
		},
		{
			name:    "revision mismatch exit code is terminal",
			details: migrationFailureDetails{exitCode: migrationScriptExitCodeRevisionMismatch, hasExitCode: true, message: "revision mismatch"},
			want:    true,
		},
		{
			name:    "revision resolution exit code is terminal",
			details: migrationFailureDetails{exitCode: migrationScriptExitCodeRevisionResolution, hasExitCode: true, message: "revision resolution"},
			want:    true,
		},
		{
			name:    "generic failure remains retryable",
			details: migrationFailureDetails{exitCode: migrationScriptExitCodeRetryableFailure, hasExitCode: true, message: "retryable"},
			want:    false,
		},
		{
			name:    "job condition failure remains retryable without exit code",
			details: migrationFailureDetails{message: "job condition failure message"},
			want:    false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := classifyMigrationFailure(tt.details); got != tt.want {
				t.Fatalf("classifyMigrationFailure() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestJobFailureDetailsReturnsPodListError(t *testing.T) {
	t.Parallel()

	scheme := runtime.NewScheme()
	if err := batchv1.AddToScheme(scheme); err != nil {
		t.Fatalf("add batchv1 to scheme: %v", err)
	}
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatalf("add corev1 to scheme: %v", err)
	}

	expectedErr := errors.New("pod list failed")
	reconciler := &MLflowReconciler{
		Client: listErrorClient{
			Client:  fake.NewClientBuilder().WithScheme(scheme).Build(),
			listErr: expectedErr,
		},
	}

	_, err := reconciler.jobFailureDetails(context.Background(), &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "migration-job",
			Namespace: "test-ns",
		},
	})
	if err == nil {
		t.Fatal("jobFailureDetails() error = nil, want pod list error")
	}
	if !strings.Contains(err.Error(), expectedErr.Error()) {
		t.Fatalf("jobFailureDetails() error = %q, want substring %q", err.Error(), expectedErr.Error())
	}
}

func TestJobFailureDetailsPrefersMappedExitCodeOverTerminationMessage(t *testing.T) {
	t.Parallel()

	scheme := runtime.NewScheme()
	if err := batchv1.AddToScheme(scheme); err != nil {
		t.Fatalf("add batchv1 to scheme: %v", err)
	}
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatalf("add corev1 to scheme: %v", err)
	}

	job := &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "migration-job",
			Namespace: "test-ns",
		},
	}
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "migration-pod",
			Namespace: "test-ns",
			Labels: map[string]string{
				"job-name": "migration-job",
			},
		},
		Status: corev1.PodStatus{
			ContainerStatuses: []corev1.ContainerStatus{{
				Name: migrationJobContainerName,
				State: corev1.ContainerState{
					Terminated: &corev1.ContainerStateTerminated{
						ExitCode: migrationScriptExitCodeUnsupportedBackend,
						Message:  "postgresql://user:secret@db.example.com:5432/mlflow",
					},
				},
			}},
		},
	}

	reconciler := &MLflowReconciler{
		Client: fake.NewClientBuilder().WithScheme(scheme).WithObjects(job, pod).Build(),
	}

	got, err := reconciler.jobFailureDetails(context.Background(), job)
	if err != nil {
		t.Fatalf("jobFailureDetails() error = %v", err)
	}
	if !got.hasExitCode || got.exitCode != migrationScriptExitCodeUnsupportedBackend {
		t.Fatalf("jobFailureDetails() exit code = (%v, %d), want (%v, %d)", got.hasExitCode, got.exitCode, true, migrationScriptExitCodeUnsupportedBackend)
	}
	wantMessage := "operator-managed migration only supports SQL backend store URIs"
	if got.message != wantMessage {
		t.Fatalf("jobFailureDetails() message = %q, want %q", got.message, wantMessage)
	}
}

func TestJobFailedConditionUsesFailedTransitionTime(t *testing.T) {
	t.Parallel()

	transitionTime := metav1.NewTime(time.Now().Add(-time.Minute))
	job := &batchv1.Job{
		Status: batchv1.JobStatus{
			Conditions: []batchv1.JobCondition{{
				Type:               batchv1.JobFailed,
				Status:             corev1.ConditionTrue,
				LastTransitionTime: transitionTime,
			}},
		},
	}

	condition := jobFailedCondition(job)
	if condition == nil {
		t.Fatal("jobFailedCondition() returned nil, want terminal failed condition")
	}
	if !condition.LastTransitionTime.Equal(&transitionTime) {
		t.Fatalf("jobFailedCondition().LastTransitionTime = %v, want %v", condition.LastTransitionTime, transitionTime)
	}
}

func TestDeploymentHasActiveReplicas(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name       string
		deployment *appsv1.Deployment
		want       bool
	}{
		{
			name:       "zero replicas is quiesced",
			deployment: &appsv1.Deployment{},
			want:       false,
		},
		{
			name: "non-ready replica still blocks migration",
			deployment: &appsv1.Deployment{
				Status: appsv1.DeploymentStatus{
					Replicas:      1,
					ReadyReplicas: 0,
				},
			},
			want: true,
		},
		{
			name: "ready replicas block migration",
			deployment: &appsv1.Deployment{
				Status: appsv1.DeploymentStatus{
					Replicas:      1,
					ReadyReplicas: 1,
				},
			},
			want: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := deploymentHasActiveReplicas(tt.deployment); got != tt.want {
				t.Fatalf("deploymentHasActiveReplicas() = %v, want %v", got, tt.want)
			}
		})
	}
}

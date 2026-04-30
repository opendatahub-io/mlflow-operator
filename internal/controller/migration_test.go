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
	"os"
	"strings"
	"testing"

	gomega "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

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
				Spec:   mlflowv1.MLflowSpec{Migrate: mlflowv1.MLflowMigrateAlways},
				Status: mlflowv1.MLflowStatus{Version: SupportedMLflowVersion},
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
		},
	}, "test-ns", RenderOptions{PlatformTrustedCABundleExists: true})
	g.Expect(err).NotTo(gomega.HaveOccurred())

	deployment, err := renderedDeployment(objs, "mlflow", "test-ns")
	g.Expect(err).NotTo(gomega.HaveOccurred())

	job, err := buildMigrationJobFromDeployment(&mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
	}, deployment, "test-ns")
	g.Expect(err).NotTo(gomega.HaveOccurred())

	g.Expect(job.Spec.Template.Spec.InitContainers).To(gomega.HaveLen(1))
	g.Expect(job.Spec.Template.Spec.InitContainers[0].Name).To(gomega.Equal("combine-ca-bundles"))
	g.Expect(job.Spec.Template.Spec.Containers).To(gomega.HaveLen(1))
	g.Expect(job.Spec.TTLSecondsAfterFinished).To(gomega.BeNil())
	g.Expect(job.Labels).To(gomega.HaveKeyWithValue("component", "mlflow-migration"))
	g.Expect(job.Labels).To(gomega.HaveKeyWithValue(migrationJobLabelKey, "true"))
	g.Expect(job.Labels).To(gomega.HaveKeyWithValue(migrationJobInstanceLabel, "mlflow"))
	g.Expect(job.Spec.Template.Labels).To(gomega.HaveKeyWithValue("component", "mlflow-migration"))
	g.Expect(job.Spec.Template.Labels).To(gomega.HaveKeyWithValue(migrationJobLabelKey, "true"))
	g.Expect(job.Spec.Template.Labels).To(gomega.HaveKeyWithValue(migrationJobInstanceLabel, "mlflow"))

	container := job.Spec.Template.Spec.Containers[0]
	g.Expect(container.Name).To(gomega.Equal(migrationJobContainerName))
	g.Expect(container.Command).To(gomega.Equal([]string{"/bin/sh", "-ec"}))
	g.Expect(container.Args).To(gomega.HaveLen(1))
	g.Expect(container.Args[0]).To(gomega.ContainSubstring("python3.12"))
	g.Expect(container.Args[0]).To(gomega.ContainSubstring("MIGRATION_PYTHON_SCRIPT"))
	g.Expect(job.Spec.BackoffLimit).NotTo(gomega.BeNil())
	g.Expect(*job.Spec.BackoffLimit).To(gomega.Equal(int32(3)))

	envByName := map[string]corev1.EnvVar{}
	for _, env := range container.Env {
		envByName[env.Name] = env
	}
	g.Expect(envByName).To(gomega.HaveKey("MIGRATION_PYTHON_SCRIPT"))
	g.Expect(envByName["MIGRATION_PYTHON_SCRIPT"].Value).To(gomega.ContainSubstring("_initialize_tables"))
	g.Expect(envByName["MIGRATION_PYTHON_SCRIPT"].Value).To(gomega.ContainSubstring("registry_uri != backend_uri"))

	mountNames := make([]string, 0, len(container.VolumeMounts))
	for _, mount := range container.VolumeMounts {
		mountNames = append(mountNames, mount.Name)
	}
	g.Expect(mountNames).To(gomega.ConsistOf("tmp", "mlflow-storage", "combined-ca-bundle"))

	volumeNames := make([]string, 0, len(job.Spec.Template.Spec.Volumes))
	for _, volume := range job.Spec.Template.Spec.Volumes {
		volumeNames = append(volumeNames, volume.Name)
	}
	g.Expect(volumeNames).To(gomega.ContainElements("tmp", "mlflow-storage", "combined-ca-bundle"))
	for _, name := range volumeNames {
		if strings.HasPrefix(name, "ca-bundle-") {
			continue
		}
		if name == "tmp" || name == "mlflow-storage" || name == "combined-ca-bundle" {
			continue
		}
		t.Fatalf("unexpected volume %q in migration Job", name)
	}

	g.Expect(envByName).To(gomega.HaveKey("MLFLOW_BACKEND_STORE_URI"))
	g.Expect(envByName["MLFLOW_BACKEND_STORE_URI"].ValueFrom.SecretKeyRef.Name).To(gomega.Equal("db-credentials"))
	g.Expect(envByName).To(gomega.HaveKey("MLFLOW_REGISTRY_STORE_URI"))
	g.Expect(envByName["MLFLOW_REGISTRY_STORE_URI"].ValueFrom.SecretKeyRef.Name).To(gomega.Equal("registry-credentials"))
	g.Expect(envByName).To(gomega.HaveKeyWithValue("SSL_CERT_FILE", corev1.EnvVar{
		Name:  "SSL_CERT_FILE",
		Value: caCombinedBundle,
	}))
}

func TestNamespaceRoleIncludesJobs(t *testing.T) {
	t.Parallel()

	content, err := os.ReadFile("../../config/rbac/namespace_role.yaml")
	if err != nil {
		t.Fatalf("read namespace role: %v", err)
	}
	if !strings.Contains(string(content), "- jobs") {
		t.Fatal("namespace_role.yaml does not grant batch/jobs access")
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

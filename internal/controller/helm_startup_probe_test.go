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
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	"github.com/opendatahub-io/mlflow-operator/internal/config"
)

func TestRenderChartStartupProbe(t *testing.T) {
	tests := []struct {
		name                             string
		probe                            *mlflowv1.StartupProbeSpec
		delay, timeout, period, failures int32
	}{
		{name: "omitted", timeout: 1, period: 10, failures: 18},
		{name: "empty", probe: &mlflowv1.StartupProbeSpec{}, timeout: 1, period: 10, failures: 18},
		{
			name:    "partial override retains defaults",
			probe:   &mlflowv1.StartupProbeSpec{FailureThreshold: ptr(int32(30))},
			timeout: 1, period: 10, failures: 30,
		},
		{
			name: "all timings overridden",
			probe: &mlflowv1.StartupProbeSpec{
				InitialDelaySeconds: ptr(int32(20)), TimeoutSeconds: ptr(int32(5)),
				PeriodSeconds: ptr(int32(15)), FailureThreshold: ptr(int32(24)),
			},
			delay: 20, timeout: 5, period: 15, failures: 24,
		},
		{
			name:    "explicit zero initial delay",
			probe:   &mlflowv1.StartupProbeSpec{InitialDelaySeconds: ptr(int32(0))},
			timeout: 1, period: 10, failures: 18,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: ResourceName},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      ptr("postgresql://db.example.com/mlflow"),
					ArtifactsDestination: ptr("s3://bucket/artifacts"),
					ArtifactsServer:      &mlflowv1.ArtifactsServerSpec{Enabled: true},
					StartupProbe:         tt.probe,
				},
			}
			objects, err := NewHelmRenderer("../../charts/mlflow").RenderChart(
				mlflow, "test-ns", RenderOptions{}, &config.OperatorConfig{
					MLflowImage: "quay.io/opendatahub/mlflow:test",
					MLflowURL:   "https://gateway.example.com", MLflowURLConfigured: true,
				},
			)
			if err != nil {
				t.Fatal(err)
			}
			for name, prefix := range map[string]string{
				ResourceName: "/mlflow", ArtifactsResourceName: "/mlflow-artifacts",
			} {
				deployment, err := renderedDeployment(objects, name, "test-ns")
				if err != nil {
					t.Fatal(err)
				}
				container := deployment.Spec.Template.Spec.Containers[0]
				probe := container.StartupProbe
				if probe == nil || probe.HTTPGet == nil {
					t.Fatalf("%s: missing HTTP startup probe", name)
				}
				if probe.HTTPGet.Path != prefix+"/api/3.0/mlflow/server-info" ||
					probe.HTTPGet.Port.StrVal != "https" || probe.HTTPGet.Scheme != corev1.URISchemeHTTPS {
					t.Errorf("%s: unexpected startup endpoint: %+v", name, probe.HTTPGet)
				}
				if probe.InitialDelaySeconds != tt.delay || probe.TimeoutSeconds != tt.timeout ||
					probe.PeriodSeconds != tt.period || probe.FailureThreshold != tt.failures || probe.SuccessThreshold != 1 {
					t.Errorf("%s: unexpected startup timings: %+v", name, probe)
				}
				if container.LivenessProbe == nil || container.LivenessProbe.InitialDelaySeconds != 30 ||
					container.LivenessProbe.PeriodSeconds != 10 || container.LivenessProbe.FailureThreshold != 3 {
					t.Errorf("%s: liveness timing changed: %+v", name, container.LivenessProbe)
				}
				if container.ReadinessProbe == nil || container.ReadinessProbe.InitialDelaySeconds != 5 ||
					container.ReadinessProbe.PeriodSeconds != 5 || container.ReadinessProbe.FailureThreshold != 3 {
					t.Errorf("%s: readiness timing changed: %+v", name, container.ReadinessProbe)
				}
			}
		})
	}
}

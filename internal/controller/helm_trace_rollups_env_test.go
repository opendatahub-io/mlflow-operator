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
	"fmt"
	"reflect"
	"testing"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	"github.com/opendatahub-io/mlflow-operator/internal/config"
	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
)

const traceRollupsEnabledEnv = "MLFLOW_SQL_TRACE_ROLLUPS_ENABLED"

func traceRollupsEnvOverrides() []corev1.EnvVar {
	return []corev1.EnvVar{
		{Name: traceRollupsEnabledEnv, Value: "true"},
		{Name: traceRollupsEnabledEnv, Value: "false"},
		{Name: traceRollupsEnabledEnv, ValueFrom: &corev1.EnvVarSource{
			SecretKeyRef: &corev1.SecretKeySelector{
				LocalObjectReference: corev1.LocalObjectReference{Name: "rollup-settings"}, Key: "enabled",
			},
		}},
	}
}

func renderTraceRollupsEnvWorkloads(namespace string, override *corev1.EnvVar, schedulingEnabled bool) ([]*unstructured.Unstructured, error) {
	instance := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: ResourceName},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI:      ptr("postgresql://database/mlflow"),
			ArtifactsDestination: ptr("s3://bucket/artifacts"),
			ArtifactsServer:      &mlflowv1.ArtifactsServerSpec{Enabled: true},
			TraceRollups:         &mlflowv1.TraceRollupsSpec{Enabled: ptr(schedulingEnabled)},
		},
	}
	if override != nil {
		instance.Spec.Env = []corev1.EnvVar{*override}
	}
	return NewHelmRenderer("../../charts/mlflow").RenderChart(instance, namespace, RenderOptions{}, &config.OperatorConfig{
		MLflowImage:         controllerTestMLflowImage,
		MLflowURL:           "https://gateway.example.com",
		MLflowURLConfigured: true,
	})
}

func traceRollupsWorkloadEnv(obj *unstructured.Unstructured) ([]corev1.EnvVar, error) {
	switch obj.GetKind() {
	case "Deployment":
		deployment := &appsv1.Deployment{}
		if err := runtime.DefaultUnstructuredConverter.FromUnstructured(obj.Object, deployment); err != nil {
			return nil, err
		}
		return deployment.Spec.Template.Spec.Containers[0].Env, nil
	case "CronJob":
		cronJob := &batchv1.CronJob{}
		if err := runtime.DefaultUnstructuredConverter.FromUnstructured(obj.Object, cronJob); err != nil {
			return nil, err
		}
		return cronJob.Spec.JobTemplate.Spec.Template.Spec.Containers[0].Env, nil
	default:
		return nil, fmt.Errorf("unexpected workload kind %s", obj.GetKind())
	}
}

func TestTraceRollupsWorkloadEnvironment(t *testing.T) {
	overrides := traceRollupsEnvOverrides()
	cases := make([]*corev1.EnvVar, 1, len(overrides)+1)
	for _, override := range overrides {
		cases = append(cases, &override)
	}
	for i, override := range cases {
		for _, schedulingEnabled := range []bool{true, false} {
			t.Run(fmt.Sprintf("override-%d/scheduling-%t", i, schedulingEnabled), func(t *testing.T) {
				objects, err := renderTraceRollupsEnvWorkloads("apps", override, schedulingEnabled)
				if err != nil {
					t.Fatal(err)
				}
				want := corev1.EnvVar{Name: traceRollupsEnabledEnv, Value: "true"}
				if override != nil {
					want = *override
				}
				for _, workload := range []struct{ kind, name string }{
					{"Deployment", ResourceName},
					{"Deployment", ArtifactsResourceName},
					{"CronJob", "mlflow-trace-rollups"},
				} {
					obj := findObject(objects, workload.kind, workload.name)
					if workload.kind == "CronJob" && !schedulingEnabled {
						if obj != nil {
							t.Fatal("scheduling opt-out must omit the CronJob")
						}
						continue
					}
					if obj == nil {
						t.Fatalf("missing %s %s", workload.kind, workload.name)
					}
					env, err := traceRollupsWorkloadEnv(obj)
					if err != nil {
						t.Fatal(err)
					}
					count := 0
					for _, variable := range env {
						if variable.Name != traceRollupsEnabledEnv {
							continue
						}
						count++
						if !reflect.DeepEqual(variable, want) {
							t.Errorf("%s rollup setting = %#v, want %#v", workload.name, variable, want)
						}
					}
					if count != 1 {
						t.Errorf("%s has %d rollup environment entries, want exactly one", workload.name, count)
					}
				}
			})
		}
	}
}

var _ = Describe("SQL trace rollup environment server-side apply", func() {
	It("applies explicit true, false, and valueFrom to both servers and the CronJob", func() {
		namespace := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{GenerateName: "trace-rollups-env-"}}
		Expect(k8sClient.Create(ctx, namespace)).To(Succeed())
		DeferCleanup(func() { Expect(k8sClient.Delete(ctx, namespace)).To(Succeed()) })
		reconciler := &MLflowReconciler{Client: k8sClient}
		overrides := traceRollupsEnvOverrides()
		cases := make([]*corev1.EnvVar, 1, len(overrides)+1)
		for _, override := range overrides {
			cases = append(cases, &override)
		}
		for _, override := range cases {
			objects, err := renderTraceRollupsEnvWorkloads(namespace.Name, override, true)
			Expect(err).NotTo(HaveOccurred())
			for _, workload := range []struct{ kind, name string }{
				{"Deployment", ResourceName},
				{"Deployment", ArtifactsResourceName},
				{"CronJob", "mlflow-trace-rollups"},
			} {
				obj := findObject(objects, workload.kind, workload.name)
				Expect(obj).NotTo(BeNil())
				// Use the controller's actual SSA path, including updates from the default.
				Expect(reconciler.applyObject(ctx, obj)).To(Succeed(), "%s %s", workload.kind, workload.name)
			}
		}
	})
})

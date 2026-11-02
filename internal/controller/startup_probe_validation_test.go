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
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

var _ = Describe("Startup probe admission", func() {
	newMLflow := func(probe *mlflowv1.StartupProbeSpec) *mlflowv1.MLflow {
		return &mlflowv1.MLflow{
			ObjectMeta: metav1.ObjectMeta{Name: ResourceName},
			Spec: mlflowv1.MLflowSpec{
				BackendStoreURI: ptr("postgresql://db.example.com/mlflow"),
				ServeArtifacts:  ptr(true), StartupProbe: probe,
			},
		}
	}

	It("defaults unspecified timing fields without replacing an override", func() {
		instance := newMLflow(&mlflowv1.StartupProbeSpec{FailureThreshold: ptr(int32(30))})
		Expect(k8sClient.Create(ctx, instance, client.DryRunAll)).To(Succeed())
		Expect(instance.Spec.StartupProbe).To(Equal(&mlflowv1.StartupProbeSpec{
			InitialDelaySeconds: ptr(int32(0)), TimeoutSeconds: ptr(int32(1)),
			PeriodSeconds: ptr(int32(10)), FailureThreshold: ptr(int32(30)),
		}))
	})

	It("defaults an empty timing configuration", func() {
		instance := newMLflow(&mlflowv1.StartupProbeSpec{})
		Expect(k8sClient.Create(ctx, instance, client.DryRunAll)).To(Succeed())
		Expect(instance.Spec.StartupProbe).To(Equal(&mlflowv1.StartupProbeSpec{
			InitialDelaySeconds: ptr(int32(0)), TimeoutSeconds: ptr(int32(1)),
			PeriodSeconds: ptr(int32(10)), FailureThreshold: ptr(int32(18)),
		}))
	})

	DescribeTable("rejects invalid timings", func(probe *mlflowv1.StartupProbeSpec, field string) {
		err := k8sClient.Create(ctx, newMLflow(probe), client.DryRunAll)
		Expect(apierrors.IsInvalid(err)).To(BeTrue())
		Expect(err.Error()).To(ContainSubstring("spec.startupProbe." + field))
	},
		Entry("negative delay", &mlflowv1.StartupProbeSpec{InitialDelaySeconds: ptr(int32(-1))}, "initialDelaySeconds"),
		Entry("zero timeout", &mlflowv1.StartupProbeSpec{TimeoutSeconds: ptr(int32(0))}, "timeoutSeconds"),
		Entry("zero period", &mlflowv1.StartupProbeSpec{PeriodSeconds: ptr(int32(0))}, "periodSeconds"),
		Entry("zero failures", &mlflowv1.StartupProbeSpec{FailureThreshold: ptr(int32(0))}, "failureThreshold"),
	)
})

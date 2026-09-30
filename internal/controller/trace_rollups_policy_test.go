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
	"path/filepath"
	"strings"
	"sync"
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	admissionv1 "k8s.io/api/admissionregistration/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/rest"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/yaml"
)

type rollupWarningCollector struct {
	sync.Mutex
	messages []string
}

func (c *rollupWarningCollector) HandleWarningHeader(_ int, _ string, message string) {
	c.Lock()
	defer c.Unlock()
	c.messages = append(c.messages, message)
}
func (c *rollupWarningCollector) reset() { c.Lock(); defer c.Unlock(); c.messages = nil }
func (c *rollupWarningCollector) text() string {
	c.Lock()
	defer c.Unlock()
	return strings.Join(c.messages, "\n")
}

var _ = Describe("SQL trace rollup admission warning", func() {
	It("warns with effective timing and permits omitted settings without defaulting them", func() {
		data, err := os.ReadFile("../../config/admission/trace-rollups-schedule-warning.yaml")
		Expect(err).NotTo(HaveOccurred())
		docs := strings.Split(string(data), "\n---\n")
		policy := &admissionv1.ValidatingAdmissionPolicy{}
		binding := &admissionv1.ValidatingAdmissionPolicyBinding{}
		Expect(yaml.Unmarshal([]byte(docs[0]), policy)).To(Succeed())
		Expect(yaml.Unmarshal([]byte(docs[1]), binding)).To(Succeed())
		cs, err := kubernetes.NewForConfig(cfg)
		Expect(err).NotTo(HaveOccurred())
		_, err = cs.AdmissionregistrationV1().ValidatingAdmissionPolicies().Create(ctx, policy, metav1.CreateOptions{})
		Expect(err).NotTo(HaveOccurred())
		DeferCleanup(func() {
			Expect(cs.AdmissionregistrationV1().ValidatingAdmissionPolicies().Delete(ctx, policy.Name, metav1.DeleteOptions{})).To(Succeed())
		})
		_, err = cs.AdmissionregistrationV1().ValidatingAdmissionPolicyBindings().Create(ctx, binding, metav1.CreateOptions{})
		Expect(err).NotTo(HaveOccurred())
		DeferCleanup(func() {
			Expect(cs.AdmissionregistrationV1().ValidatingAdmissionPolicyBindings().Delete(ctx, binding.Name, metav1.DeleteOptions{})).To(Succeed())
		})
		collector := &rollupWarningCollector{}
		warningConfig := rest.CopyConfig(cfg)
		warningConfig.WarningHandler = collector
		warningClient, err := client.New(warningConfig, client.Options{Scheme: scheme.Scheme})
		Expect(err).NotTo(HaveOccurred())
		dryRun := func(uri string, spec *mlflowv1.TraceRollupsSpec) *mlflowv1.MLflow {
			collector.reset()
			instance := &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: "mlflow"}, Spec: mlflowv1.MLflowSpec{BackendStoreURI: ptr(uri), DefaultArtifactRoot: ptr("s3://bucket/artifacts"), TraceRollups: spec}}
			if strings.HasPrefix(uri, "sqlite:") {
				instance.Spec.Storage = &corev1.PersistentVolumeClaimSpec{AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce}}
			}
			Expect(warningClient.Create(ctx, instance, client.DryRunAll)).To(Succeed())
			return instance
		}
		Eventually(func() string { dryRun("postgresql://host/db", nil); return collector.text() }, 15*time.Second, 200*time.Millisecond).Should(ContainSubstring("nightly at 02:00"))
		Expect(collector.text()).To(ContainSubstring("Etc/UTC"))
		result := dryRun("postgresql://host/db", &mlflowv1.TraceRollupsSpec{})
		Expect(result.Spec.TraceRollups.Schedule).To(BeNil())
		Expect(result.Spec.TraceRollups.TimeZone).To(BeNil())
		Expect(collector.text()).To(ContainSubstring("nightly at 02:00"))
		dryRun("postgresql://host/db", &mlflowv1.TraceRollupsSpec{Schedule: ptr("30 1 * * *")})
		Expect(collector.text()).To(ContainSubstring("30 1 * * *"))
		Expect(collector.text()).To(ContainSubstring("Etc/UTC"))
		dryRun("postgresql://host/db", &mlflowv1.TraceRollupsSpec{TimeZone: ptr("America/New_York")})
		Expect(collector.text()).To(ContainSubstring("America/New_York"))
		dryRun("postgresql://host/db", &mlflowv1.TraceRollupsSpec{Schedule: ptr("30 1 * * *"), TimeZone: ptr("America/New_York")})
		Expect(collector.text()).To(BeEmpty())
		dryRun("postgresql://host/db", &mlflowv1.TraceRollupsSpec{Enabled: ptr(false)})
		Expect(collector.text()).To(BeEmpty())
		dryRun("sqlite:////mlflow/mlflow.db", nil)
		Expect(collector.text()).To(BeEmpty())
		p, err := cs.AdmissionregistrationV1().ValidatingAdmissionPolicies().Get(ctx, policy.Name, metav1.GetOptions{})
		Expect(err).NotTo(HaveOccurred())
		if p.Status.TypeChecking != nil {
			Expect(p.Status.TypeChecking.ExpressionWarnings).To(BeEmpty())
		}
	})
})

var _ = Describe("MLflow sample admission", func() {
	It("accepts the shipped samples against the generated CRD", func() {
		paths, err := filepath.Glob("../../config/samples/*.yaml")
		Expect(err).NotTo(HaveOccurred())
		for _, path := range paths {
			data, err := os.ReadFile(path)
			Expect(err).NotTo(HaveOccurred())
			instance := &mlflowv1.MLflow{}
			Expect(yaml.Unmarshal(data, instance)).To(Succeed())
			if instance.Kind != "MLflow" {
				continue
			}
			By("validating " + path)
			Expect(k8sClient.Create(ctx, instance, client.DryRunAll)).To(Succeed(), path)
		}
	})
})

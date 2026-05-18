/*
Copyright 2025.

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

	gomega "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/util/intstr"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

func TestRenderChart_NetworkPolicy(t *testing.T) {
	g := gomega.NewWithT(t)
	renderer := NewHelmRenderer("../../charts/mlflow")

	// Default: expected egress ports are present
	objs, err := renderer.RenderChart(&mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr(testBackendStoreURI),
		},
	}, "test-ns", RenderOptions{})
	g.Expect(err).NotTo(gomega.HaveOccurred())

	np := findObject(objs, "NetworkPolicy", "mlflow")
	g.Expect(np).NotTo(gomega.BeNil(), "NetworkPolicy should be rendered")
	migrationNP := findObject(objs, "NetworkPolicy", "mlflow-migration")
	g.Expect(migrationNP).NotTo(gomega.BeNil(), "migration NetworkPolicy should be rendered for operator-managed chart rendering")
	ingress, found, err := unstructured.NestedSlice(migrationNP.Object, "spec", "ingress")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found || len(ingress) == 0).To(gomega.BeTrue())
	g.Expect(ingress).To(gomega.BeEmpty(), "migration NetworkPolicy should not allow ingress")
	migrationEgress, found, err := unstructured.NestedSlice(migrationNP.Object, "spec", "egress")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue())
	g.Expect(migrationEgress).To(gomega.HaveLen(2), "migration NetworkPolicy should have 2 default egress rules (DNS and SQL metadata stores)")
	migrationPorts := collectEgressPorts(migrationEgress)
	for _, expected := range []int64{53, 5353, 5432, 3306} {
		g.Expect(migrationPorts).To(gomega.ContainElement(expected), "migration egress should allow port %d", expected)
	}
	for _, unexpected := range []int64{443, 6443, 9000, 8333, 8334} {
		g.Expect(migrationPorts).NotTo(gomega.ContainElement(unexpected), "migration egress should not allow port %d", unexpected)
	}

	egress, found, err := unstructured.NestedSlice(np.Object, "spec", "egress")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue())
	g.Expect(egress).To(gomega.HaveLen(5), "should have 5 default egress rules (DNS, HTTPS, PostgreSQL, MySQL, S3)")

	allPorts := collectEgressPorts(egress)
	for _, expected := range []int64{53, 443, 8443, 6443, 5432, 3306, 9000, 8333, 8334} {
		g.Expect(allPorts).To(gomega.ContainElement(expected), "egress should allow port %d", expected)
	}

	httpsRules := findEgressRulesByPort(egress, 443)
	g.Expect(httpsRules).To(gomega.HaveLen(1), "default policy should have one HTTPS egress rule")
	g.Expect(httpsRules[0]).NotTo(gomega.HaveKey("to"), "HTTPS egress rule should be unrestricted by destination")
	httpsRulePorts := collectRulePorts(httpsRules[0])
	for _, p := range []int64{443, 6443, 8443} {
		g.Expect(httpsRulePorts).To(gomega.ContainElement(p), "HTTPS egress rule should include port %d", p)
	}

	// Additional egress rules are appended
	objs, err = renderer.RenderChart(&mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr(testBackendStoreURI),
			NetworkPolicyAdditionalEgressRules: []networkingv1.NetworkPolicyEgressRule{
				{
					Ports: []networkingv1.NetworkPolicyPort{
						{
							Protocol: ptr(corev1.ProtocolTCP),
							Port:     ptr(intstr.FromInt32(443)),
						},
					},
				},
			},
		},
	}, "test-ns", RenderOptions{})
	g.Expect(err).NotTo(gomega.HaveOccurred())

	np = findObject(objs, "NetworkPolicy", "mlflow")
	g.Expect(np).NotTo(gomega.BeNil())

	egress, found, err = unstructured.NestedSlice(np.Object, "spec", "egress")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue())
	g.Expect(egress).To(gomega.HaveLen(6), "should have 5 default + 1 additional egress rule")

	httpsRules = findEgressRulesByPort(egress, 443)
	g.Expect(httpsRules).To(gomega.HaveLen(2), "should have the default unrestricted HTTPS rule plus the additional rule")
	g.Expect(httpsRules[1]).NotTo(gomega.HaveKey("to"), "additional HTTPS rule should remain unrestricted unless configured otherwise")

	migrationNP = findObject(objs, "NetworkPolicy", "mlflow-migration")
	g.Expect(migrationNP).NotTo(gomega.BeNil())
	migrationEgress, found, err = unstructured.NestedSlice(migrationNP.Object, "spec", "egress")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue())
	g.Expect(migrationEgress).To(gomega.HaveLen(3), "migration NetworkPolicy should have 2 default + 1 additional egress rule")
	g.Expect(collectEgressPorts(migrationEgress)).To(gomega.ContainElement(int64(443)))
}

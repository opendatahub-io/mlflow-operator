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

	egress, found, err := unstructured.NestedSlice(np.Object, "spec", "egress")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue())
	g.Expect(egress).To(gomega.HaveLen(5), "should have 5 default egress rules (DNS, HTTPS+K8sAPI, PostgreSQL, MySQL, S3)")

	allPorts := collectEgressPorts(egress)
	for _, expected := range []int64{53, 443, 6443, 5432, 3306, 9000, 8333, 8334} {
		g.Expect(allPorts).To(gomega.ContainElement(expected), "egress should allow port %d", expected)
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
							Port:     ptr(intstr.FromInt32(15432)),
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
	g.Expect(collectEgressPorts(egress)).To(gomega.ContainElement(int64(15432)))
}

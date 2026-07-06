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

	gomega "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

func TestMlflowToHelmValues_ResourceClaims(t *testing.T) {
	t.Parallel()

	g := gomega.NewWithT(t)
	renderer := &HelmRenderer{}

	values, err := renderer.mlflowToHelmValues(&mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "test"},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr(testBackendStoreURI),
			ResourceClaims: []corev1.PodResourceClaim{{
				Name:                      "shared-gpu",
				ResourceClaimTemplateName: ptr("shared-gpu-template"),
			}},
		},
	}, "test-namespace", RenderOptions{}, nil)
	g.Expect(err).NotTo(gomega.HaveOccurred())

	rawClaims, exists := values["resourceClaims"]
	g.Expect(exists).To(gomega.BeTrue())

	claims, ok := rawClaims.([]corev1.PodResourceClaim)
	g.Expect(ok).To(gomega.BeTrue())
	g.Expect(claims).To(gomega.HaveLen(1))
	g.Expect(claims[0].Name).To(gomega.Equal("shared-gpu"))
	g.Expect(claims[0].ResourceClaimTemplateName).NotTo(gomega.BeNil())
	g.Expect(*claims[0].ResourceClaimTemplateName).To(gomega.Equal("shared-gpu-template"))
}

func TestRenderChart_ResourceClaims(t *testing.T) {
	t.Parallel()

	g := gomega.NewWithT(t)
	renderer := NewHelmRenderer("../../charts/mlflow")

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr(testBackendStoreURI),
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
	}

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{}, nil)
	g.Expect(err).NotTo(gomega.HaveOccurred())

	deployment := findObject(objs, deploymentKind, "mlflow")
	g.Expect(deployment).NotTo(gomega.BeNil(), "Deployment should be rendered")

	resourceClaims, found, err := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "resourceClaims")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue(), "Pod resourceClaims should be rendered")
	g.Expect(resourceClaims).To(gomega.HaveLen(1))

	podClaim, ok := resourceClaims[0].(map[string]interface{})
	g.Expect(ok).To(gomega.BeTrue())
	g.Expect(podClaim["name"]).To(gomega.Equal("shared-gpu"))
	g.Expect(podClaim["resourceClaimTemplateName"]).To(gomega.Equal("shared-gpu-template"))

	containers, found, err := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "containers")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue())
	g.Expect(containers).NotTo(gomega.BeEmpty())

	container, ok := containers[0].(map[string]interface{})
	g.Expect(ok).To(gomega.BeTrue())

	resources, found, err := unstructured.NestedMap(container, "resources")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue(), "Container resources should be rendered")

	containerClaims, ok := resources["claims"].([]interface{})
	g.Expect(ok).To(gomega.BeTrue())
	g.Expect(containerClaims).To(gomega.HaveLen(1))

	containerClaim, ok := containerClaims[0].(map[string]interface{})
	g.Expect(ok).To(gomega.BeTrue())
	g.Expect(containerClaim["name"]).To(gomega.Equal("shared-gpu"))
	g.Expect(containerClaim["request"]).To(gomega.Equal("gpu"))
}

func TestRenderChart_ResourceClaimName(t *testing.T) {
	t.Parallel()

	g := gomega.NewWithT(t)
	renderer := NewHelmRenderer("../../charts/mlflow")

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr(testBackendStoreURI),
			ResourceClaims: []corev1.PodResourceClaim{{
				Name:              "shared-gpu",
				ResourceClaimName: ptr("existing-shared-gpu"),
			}},
			Resources: &corev1.ResourceRequirements{
				Claims: []corev1.ResourceClaim{{
					Name: "shared-gpu",
				}},
			},
		},
	}

	objs, err := renderer.RenderChart(mlflow, "test-ns", RenderOptions{}, nil)
	g.Expect(err).NotTo(gomega.HaveOccurred())

	deployment := findObject(objs, deploymentKind, "mlflow")
	g.Expect(deployment).NotTo(gomega.BeNil(), "Deployment should be rendered")

	resourceClaims, found, err := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "resourceClaims")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue(), "Pod resourceClaims should be rendered")
	g.Expect(resourceClaims).To(gomega.HaveLen(1))

	podClaim, ok := resourceClaims[0].(map[string]interface{})
	g.Expect(ok).To(gomega.BeTrue())
	g.Expect(podClaim["name"]).To(gomega.Equal("shared-gpu"))
	g.Expect(podClaim["resourceClaimName"]).To(gomega.Equal("existing-shared-gpu"))
}

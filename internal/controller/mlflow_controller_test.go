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
	"context"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

var _ = Describe("MLflow Controller", func() {
	Context("When reconciling a resource", func() {
		const resourceName = "mlflow"

		ctx := context.Background()

		typeNamespacedName := types.NamespacedName{
			Name: resourceName,
		}
		mlflow := &mlflowv1.MLflow{}

		BeforeEach(func() {
			By("creating the opendatahub namespace")
			ns := &corev1.Namespace{
				ObjectMeta: metav1.ObjectMeta{
					Name: "opendatahub",
				},
			}
			err := k8sClient.Get(ctx, types.NamespacedName{Name: "opendatahub"}, ns)
			if err != nil && errors.IsNotFound(err) {
				Expect(k8sClient.Create(ctx, ns)).To(Succeed())
			}

			By("creating the custom resource for the Kind MLflow")
			err = k8sClient.Get(ctx, typeNamespacedName, mlflow)
			if err != nil && errors.IsNotFound(err) {
				disabled := false
				mlflowResource := &mlflowv1.MLflow{
					ObjectMeta: metav1.ObjectMeta{
						Name: resourceName,
					},
					Spec: mlflowv1.MLflowSpec{
						KubeRbacProxy: &mlflowv1.KubeRbacProxyConfig{
							Enabled: &disabled,
						},
						// Storage is required when using default sqlite backend
						Storage: &corev1.PersistentVolumeClaimSpec{
							AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
							Resources: corev1.VolumeResourceRequirements{
								Requests: corev1.ResourceList{
									corev1.ResourceStorage: resource.MustParse("1Gi"),
								},
							},
						},
					},
				}
				Expect(k8sClient.Create(ctx, mlflowResource)).To(Succeed())
			}
		})

		AfterEach(func() {
			resource := &mlflowv1.MLflow{}
			err := k8sClient.Get(ctx, typeNamespacedName, resource)
			Expect(err).NotTo(HaveOccurred())

			By("Cleanup the specific resource instance MLflow")
			Expect(k8sClient.Delete(ctx, resource)).To(Succeed())
		})
		It("should successfully reconcile the resource", func() {
			By("Reconciling the created resource")

			controllerReconciler := &MLflowReconciler{
				Client:               k8sClient,
				Scheme:               k8sClient.Scheme(),
				Namespace:            "opendatahub",
				ChartPath:            "../../charts/mlflow",
				ConsoleLinkAvailable: false,
				HTTPRouteAvailable:   false,
			}

			_, reconcileErr := controllerReconciler.Reconcile(ctx, reconcile.Request{
				NamespacedName: typeNamespacedName,
			})
			Expect(reconcileErr).NotTo(HaveOccurred())
		})
	})
})

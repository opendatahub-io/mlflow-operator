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
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/intstr"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	gatewayv1 "sigs.k8s.io/gateway-api/apis/v1"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

var _ = Describe("MLflow Controller", func() {
	pgStoreURI := "postgresql://user:pass@host:5432/db"

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
				backendStoreURI := "sqlite:////mlflow/mlflow.db"
				mlflowResource := &mlflowv1.MLflow{
					ObjectMeta: metav1.ObjectMeta{
						Name: resourceName,
					},
					Spec: mlflowv1.MLflowSpec{
						BackendStoreURI: &backendStoreURI,
						DefaultArtifactRoot: func() *string {
							val := "s3://default/artifacts"
							return &val
						}(),
						// Storage is required when using sqlite backend
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

			Expect(k8sClient.Get(ctx, typeNamespacedName, mlflow)).To(Succeed())
			Expect(mlflow.Status.URL).To(BeEmpty())
			Expect(mlflow.Status.Address).NotTo(BeNil())
			Expect(mlflow.Status.Address.URL).To(Equal("https://mlflow.opendatahub.svc:8443"))
		})

		It("should delete GC CronJob when garbageCollection is removed from spec", func() {
			By("Enabling garbage collection")
			Expect(k8sClient.Get(ctx, typeNamespacedName, mlflow)).To(Succeed())
			mlflow.Spec.GarbageCollection = &mlflowv1.GarbageCollectionSpec{
				Schedule: "0 2 * * 0",
			}
			Expect(k8sClient.Update(ctx, mlflow)).To(Succeed())

			controllerReconciler := &MLflowReconciler{
				Client:               k8sClient,
				Scheme:               k8sClient.Scheme(),
				Namespace:            "opendatahub",
				ChartPath:            "../../charts/mlflow",
				ConsoleLinkAvailable: false,
				HTTPRouteAvailable:   false,
			}

			By("Reconciling to create the CronJob")
			_, reconcileErr := controllerReconciler.Reconcile(ctx, reconcile.Request{
				NamespacedName: typeNamespacedName,
			})
			Expect(reconcileErr).NotTo(HaveOccurred())

			gcCronJob := &batchv1.CronJob{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name:      "mlflow-gc",
				Namespace: "opendatahub",
			}, gcCronJob)).To(Succeed())

			By("Disabling garbage collection")
			Expect(k8sClient.Get(ctx, typeNamespacedName, mlflow)).To(Succeed())
			mlflow.Spec.GarbageCollection = nil
			Expect(k8sClient.Update(ctx, mlflow)).To(Succeed())

			By("Reconciling to delete the CronJob")
			_, reconcileErr = controllerReconciler.Reconcile(ctx, reconcile.Request{
				NamespacedName: typeNamespacedName,
			})
			Expect(reconcileErr).NotTo(HaveOccurred())

			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      "mlflow-gc",
				Namespace: "opendatahub",
			}, gcCronJob)
			Expect(errors.IsNotFound(err)).To(BeTrue())

			gcServiceAccount := &corev1.ServiceAccount{}
			err = k8sClient.Get(ctx, types.NamespacedName{
				Name:      GCServiceAccountName,
				Namespace: "opendatahub",
			}, gcServiceAccount)
			Expect(errors.IsNotFound(err)).To(BeTrue())

			gcClusterRoleBinding := &rbacv1.ClusterRoleBinding{}
			err = k8sClient.Get(ctx, types.NamespacedName{
				Name: "mlflow-gc",
			}, gcClusterRoleBinding)
			Expect(errors.IsNotFound(err)).To(BeTrue())

			gcClusterRole := &rbacv1.ClusterRole{}
			err = k8sClient.Get(ctx, types.NamespacedName{
				Name: "mlflow-gc",
			}, gcClusterRole)
			Expect(errors.IsNotFound(err)).To(BeTrue())
		})

		It("should create an HTTPRoute with API rewrite when available", func() {
			By("Reconciling the created resource with HTTPRoute enabled")

			controllerReconciler := &MLflowReconciler{
				Client:               k8sClient,
				Scheme:               k8sClient.Scheme(),
				Namespace:            "opendatahub",
				ChartPath:            "../../charts/mlflow",
				ConsoleLinkAvailable: false,
				HTTPRouteAvailable:   true,
			}

			_, reconcileErr := controllerReconciler.Reconcile(ctx, reconcile.Request{
				NamespacedName: typeNamespacedName,
			})
			Expect(reconcileErr).NotTo(HaveOccurred())

			httpRoute := &gatewayv1.HTTPRoute{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name:      ResourceName,
				Namespace: controllerReconciler.Namespace,
			}, httpRoute)).To(Succeed())

			Expect(httpRoute.Spec.Rules).To(HaveLen(3))

			apiRule := httpRoute.Spec.Rules[0]
			Expect(apiRule.Matches).To(HaveLen(1))
			Expect(apiRule.Matches[0].Path).NotTo(BeNil())
			Expect(apiRule.Matches[0].Path.Value).NotTo(BeNil())
			Expect(*apiRule.Matches[0].Path.Value).To(Equal("/" + ResourceName + "/api"))

			Expect(apiRule.Filters).To(HaveLen(1))
			Expect(apiRule.Filters[0].Type).To(Equal(gatewayv1.HTTPRouteFilterURLRewrite))
			Expect(apiRule.Filters[0].URLRewrite).NotTo(BeNil())
			Expect(apiRule.Filters[0].URLRewrite.Path).NotTo(BeNil())
			Expect(apiRule.Filters[0].URLRewrite.Path.Type).To(Equal(gatewayv1.PrefixMatchHTTPPathModifier))
			Expect(apiRule.Filters[0].URLRewrite.Path.ReplacePrefixMatch).NotTo(BeNil())
			Expect(*apiRule.Filters[0].URLRewrite.Path.ReplacePrefixMatch).To(Equal("/api"))

			Expect(apiRule.BackendRefs).To(HaveLen(1))
			apiBackend := apiRule.BackendRefs[0]
			Expect(apiBackend.BackendRef.BackendObjectReference.Name).To(Equal(gatewayv1.ObjectName(ResourceName)))
			Expect(apiBackend.BackendRef.Port).NotTo(BeNil())
			Expect(int(*apiBackend.BackendRef.Port)).To(Equal(8443))
			Expect(apiBackend.BackendRef.Weight).NotTo(BeNil())
			Expect(*apiBackend.BackendRef.Weight).To(Equal(int32(1)))

			v1Rule := httpRoute.Spec.Rules[1]
			Expect(v1Rule.Matches).To(HaveLen(1))
			Expect(v1Rule.Matches[0].Path).NotTo(BeNil())
			Expect(v1Rule.Matches[0].Path.Value).NotTo(BeNil())
			Expect(*v1Rule.Matches[0].Path.Value).To(Equal("/" + ResourceName + "/v1"))

			Expect(v1Rule.Filters).To(HaveLen(1))
			Expect(v1Rule.Filters[0].Type).To(Equal(gatewayv1.HTTPRouteFilterURLRewrite))
			Expect(v1Rule.Filters[0].URLRewrite).NotTo(BeNil())
			Expect(v1Rule.Filters[0].URLRewrite.Path).NotTo(BeNil())
			Expect(v1Rule.Filters[0].URLRewrite.Path.Type).To(Equal(gatewayv1.PrefixMatchHTTPPathModifier))
			Expect(v1Rule.Filters[0].URLRewrite.Path.ReplacePrefixMatch).NotTo(BeNil())
			Expect(*v1Rule.Filters[0].URLRewrite.Path.ReplacePrefixMatch).To(Equal("/v1"))

			Expect(v1Rule.BackendRefs).To(HaveLen(1))
			v1Backend := v1Rule.BackendRefs[0]
			Expect(v1Backend.BackendRef.BackendObjectReference.Name).To(Equal(gatewayv1.ObjectName(ResourceName)))
			Expect(v1Backend.BackendRef.Port).NotTo(BeNil())
			Expect(int(*v1Backend.BackendRef.Port)).To(Equal(8443))
			Expect(v1Backend.BackendRef.Weight).NotTo(BeNil())
			Expect(*v1Backend.BackendRef.Weight).To(Equal(int32(1)))

			rootRule := httpRoute.Spec.Rules[2]
			Expect(rootRule.Matches).To(HaveLen(1))
			Expect(rootRule.Matches[0].Path).NotTo(BeNil())
			Expect(rootRule.Matches[0].Path.Value).NotTo(BeNil())
			Expect(*rootRule.Matches[0].Path.Value).To(Equal("/" + ResourceName))
		})
	})

	Describe("CEL validation", func() {
		const resourceName = "mlflow"
		ctx := context.Background()

		AfterEach(func() {
			resource := &mlflowv1.MLflow{}
			err := k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, resource)
			if errors.IsNotFound(err) {
				return
			}
			Expect(err).NotTo(HaveOccurred())
			Expect(k8sClient.Delete(ctx, resource)).To(Succeed())
		})

		It("rejects when serveArtifacts is false and defaultArtifactRoot is missing", func() {
			serveArtifactsFalse := false
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Name: resourceName,
				},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:   &serveArtifactsFalse,
					BackendStoreURI:  &pgStoreURI,
					RegistryStoreURI: &pgStoreURI,
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("defaultArtifactRoot must be set"))
		})

		It("allows missing defaultArtifactRoot when serveArtifacts is true", func() {
			serveArtifactsTrue := true
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Name: resourceName,
				},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:   &serveArtifactsTrue,
					BackendStoreURI:  &pgStoreURI,
					RegistryStoreURI: &pgStoreURI,
				},
			}
			Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())
		})

		It("rejects when backend store is missing", func() {
			serveArtifactsTrue := true
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Name: resourceName,
				},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts: &serveArtifactsTrue,
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("backendStoreUri or backendStoreUriFrom must be set"))
		})

		It("allows backendStoreUriFrom without backendStoreUri", func() {
			serveArtifactsTrue := true
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Name: resourceName,
				},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts: &serveArtifactsTrue,
					BackendStoreURIFrom: &corev1.SecretKeySelector{
						LocalObjectReference: corev1.LocalObjectReference{
							Name: "db-credentials",
						},
						Key: "backend-uri",
					},
				},
			}
			Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())
		})

		It("allows backendStoreUri without backendStoreUriFrom", func() {
			serveArtifactsTrue := true
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Name: resourceName,
				},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:  &serveArtifactsTrue,
					BackendStoreURI: &pgStoreURI,
				},
			}
			Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())
		})

		It("rejects when both backendStoreUri and backendStoreUriFrom are set", func() {
			serveArtifactsTrue := true
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Name: resourceName,
				},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:  &serveArtifactsTrue,
					BackendStoreURI: &pgStoreURI,
					BackendStoreURIFrom: &corev1.SecretKeySelector{
						LocalObjectReference: corev1.LocalObjectReference{
							Name: "db-credentials",
						},
						Key: "backend-uri",
					},
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("backendStoreUri and backendStoreUriFrom are mutually exclusive"))
		})

		It("rejects empty networkPolicyAdditionalEgressRules entries", func() {
			artifactRoot := "s3://bucket/artifacts"
			proto := corev1.ProtocolTCP
			port := intstr.FromInt32(15432)
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Name: resourceName,
				},
				Spec: mlflowv1.MLflowSpec{
					DefaultArtifactRoot: &artifactRoot,
					BackendStoreURI:     &pgStoreURI,
					RegistryStoreURI:    &pgStoreURI,
					NetworkPolicyAdditionalEgressRules: []networkingv1.NetworkPolicyEgressRule{
						{Ports: []networkingv1.NetworkPolicyPort{{Protocol: &proto, Port: &port}}},
						{}, // empty - should be rejected
					},
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("must specify at least one port or one destination"))
		})

		It("rejects empty olderThan in garbageCollection", func() {
			artifactRoot := "s3://bucket/artifacts"
			emptyOlderThan := ""
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Name: resourceName,
				},
				Spec: mlflowv1.MLflowSpec{
					DefaultArtifactRoot: &artifactRoot,
					BackendStoreURI:     &pgStoreURI,
					GarbageCollection: &mlflowv1.GarbageCollectionSpec{
						Schedule:  "0 2 * * 0",
						OlderThan: &emptyOlderThan,
					},
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
		})

		It("rejects MLFLOW_SERVER_DISABLE_SECURITY_MIDDLEWARE env var", func() {
			artifactRoot := "s3://bucket/artifacts"
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{
					Name: resourceName,
				},
				Spec: mlflowv1.MLflowSpec{
					DefaultArtifactRoot: &artifactRoot,
					BackendStoreURI:     &pgStoreURI,
					RegistryStoreURI:    &pgStoreURI,
					Env: []corev1.EnvVar{
						{
							Name:  "MLFLOW_SERVER_DISABLE_SECURITY_MIDDLEWARE",
							Value: "true",
						},
					},
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("MLFLOW_SERVER_DISABLE_SECURITY_MIDDLEWARE"))
		})
	})
})

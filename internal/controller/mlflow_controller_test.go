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
	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	"k8s.io/apimachinery/pkg/api/resource"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/intstr"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	gatewayv1 "sigs.k8s.io/gateway-api/apis/v1"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	"github.com/opendatahub-io/mlflow-operator/internal/config"
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
			Expect(k8sClient.Get(ctx, typeNamespacedName, mlflow)).To(Succeed())
			mlflow.Status.Version = SupportedMLflowVersion
			Expect(k8sClient.Status().Update(ctx, mlflow)).To(Succeed())
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
				GCRBACWatchCache:     mustNewGCRBACWatchCache(),
			}

			_, reconcileErr := controllerReconciler.Reconcile(ctx, reconcile.Request{
				NamespacedName: typeNamespacedName,
			})
			Expect(reconcileErr).NotTo(HaveOccurred())

			Expect(k8sClient.Get(ctx, typeNamespacedName, mlflow)).To(Succeed())
			Expect(mlflow.Status.URL).To(BeEmpty())
			Expect(mlflow.Status.Address).NotTo(BeNil())
			Expect(mlflow.Status.Address.URL).To(Equal("https://mlflow.opendatahub.svc:8443/mlflow"))
		})

		It("should reject split serving before mutating workloads when HTTPRoute is unavailable", func() {
			Expect(k8sClient.Get(ctx, typeNamespacedName, mlflow)).To(Succeed())
			mlflow.Spec.ServeArtifacts = ptr(true)
			mlflow.Spec.ArtifactsDestination = ptr("s3://bucket/artifacts")
			Expect(k8sClient.Update(ctx, mlflow)).To(Succeed())

			controllerReconciler := &MLflowReconciler{
				Client:             k8sClient,
				Scheme:             k8sClient.Scheme(),
				Namespace:          "opendatahub",
				ChartPath:          "../../charts/mlflow",
				HTTPRouteAvailable: false,
				GCRBACWatchCache:   mustNewGCRBACWatchCache(),
				baseConfig: &config.OperatorConfig{
					MLflowImage:         controllerTestMLflowImage,
					MLflowURL:           "https://gateway.example.com",
					MLflowURLConfigured: true,
				},
			}
			_, reconcileErr := controllerReconciler.Reconcile(ctx, reconcile.Request{NamespacedName: typeNamespacedName})
			Expect(reconcileErr).NotTo(HaveOccurred())

			trackingKey := types.NamespacedName{Name: ResourceName, Namespace: "opendatahub"}
			trackingDeploymentBefore := &appsv1.Deployment{}
			Expect(k8sClient.Get(ctx, trackingKey, trackingDeploymentBefore)).To(Succeed())
			Expect(trackingDeploymentBefore.Spec.Template.Spec.Containers[0].Args).To(ContainElement("--serve-artifacts"))
			trackingServiceBefore := &corev1.Service{}
			Expect(k8sClient.Get(ctx, trackingKey, trackingServiceBefore)).To(Succeed())

			Expect(k8sClient.Get(ctx, typeNamespacedName, mlflow)).To(Succeed())
			mlflow.Spec.BackendStoreURI = &pgStoreURI
			mlflow.Spec.ServeArtifacts = ptr(false)
			mlflow.Spec.DefaultArtifactRoot = nil
			mlflow.Spec.ArtifactsServer = &mlflowv1.ArtifactsServerSpec{Enabled: true}
			Expect(k8sClient.Update(ctx, mlflow)).To(Succeed())

			_, reconcileErr = controllerReconciler.Reconcile(ctx, reconcile.Request{NamespacedName: typeNamespacedName})
			Expect(reconcileErr).To(MatchError(artifactsServerHTTPRouteRequiredMessage))

			trackingDeploymentAfter := &appsv1.Deployment{}
			Expect(k8sClient.Get(ctx, trackingKey, trackingDeploymentAfter)).To(Succeed())
			Expect(trackingDeploymentAfter.UID).To(Equal(trackingDeploymentBefore.UID))
			Expect(trackingDeploymentAfter.ResourceVersion).To(Equal(trackingDeploymentBefore.ResourceVersion))
			Expect(trackingDeploymentAfter.Spec).To(Equal(trackingDeploymentBefore.Spec))
			trackingServiceAfter := &corev1.Service{}
			Expect(k8sClient.Get(ctx, trackingKey, trackingServiceAfter)).To(Succeed())
			Expect(trackingServiceAfter.UID).To(Equal(trackingServiceBefore.UID))
			Expect(trackingServiceAfter.ResourceVersion).To(Equal(trackingServiceBefore.ResourceVersion))
			Expect(trackingServiceAfter.Spec).To(Equal(trackingServiceBefore.Spec))

			artifactKey := types.NamespacedName{Name: ArtifactsResourceName, Namespace: "opendatahub"}
			Expect(errors.IsNotFound(k8sClient.Get(ctx, artifactKey, &appsv1.Deployment{}))).To(BeTrue())
			Expect(errors.IsNotFound(k8sClient.Get(ctx, artifactKey, &corev1.Service{}))).To(BeTrue())
			Expect(errors.IsNotFound(k8sClient.Get(ctx, artifactKey, &gatewayv1.HTTPRoute{}))).To(BeTrue())
			Expect(errors.IsNotFound(k8sClient.Get(ctx, types.NamespacedName{
				Name: migrationJobName(mlflow), Namespace: "opendatahub",
			}, &batchv1.Job{}))).To(BeTrue())

			Expect(k8sClient.Get(ctx, typeNamespacedName, mlflow)).To(Succeed())
			available := meta.FindStatusCondition(mlflow.Status.Conditions, "Available")
			Expect(available).NotTo(BeNil())
			Expect(available.Status).To(Equal(metav1.ConditionFalse))
			Expect(available.Reason).To(Equal("HttpRouteFailed"))
			Expect(available.Message).To(ContainSubstring(artifactsServerHTTPRouteRequiredMessage))
			Expect(mlflow.Status.URL).To(BeEmpty())
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
				GCRBACWatchCache:     mustNewGCRBACWatchCache(),
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

		It("should create an HTTPRoute with v1 rewrite when available", func() {
			By("Reconciling the created resource with HTTPRoute enabled")

			controllerReconciler := &MLflowReconciler{
				Client:               k8sClient,
				Scheme:               k8sClient.Scheme(),
				Namespace:            "opendatahub",
				ChartPath:            "../../charts/mlflow",
				ConsoleLinkAvailable: false,
				HTTPRouteAvailable:   true,
				GCRBACWatchCache:     mustNewGCRBACWatchCache(),
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

			Expect(httpRoute.Spec.Rules).To(HaveLen(2))

			v1Rule := httpRoute.Spec.Rules[0]
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

			rootRule := httpRoute.Spec.Rules[1]
			Expect(rootRule.Matches).To(HaveLen(1))
			Expect(rootRule.Matches[0].Path).NotTo(BeNil())
			Expect(rootRule.Matches[0].Path.Value).NotTo(BeNil())
			Expect(*rootRule.Matches[0].Path.Value).To(Equal("/" + ResourceName))
		})

		It("should reconcile an enabled dedicated artifact server and evaluate its readiness", func() {
			Expect(k8sClient.Get(ctx, typeNamespacedName, mlflow)).To(Succeed())
			mlflow.Spec.BackendStoreURI = &pgStoreURI
			mlflow.Spec.DefaultArtifactRoot = nil
			mlflow.Spec.ArtifactsServer = &mlflowv1.ArtifactsServerSpec{Enabled: true}
			mlflow.Spec.ArtifactsDestination = ptr("s3://bucket/artifacts")
			Expect(k8sClient.Update(ctx, mlflow)).To(Succeed())

			controllerReconciler := &MLflowReconciler{
				Client:             k8sClient,
				Scheme:             k8sClient.Scheme(),
				Namespace:          "opendatahub",
				ChartPath:          "../../charts/mlflow",
				HTTPRouteAvailable: true,
				GCRBACWatchCache:   mustNewGCRBACWatchCache(),
				baseConfig: &config.OperatorConfig{
					MLflowImage:         controllerTestMLflowImage,
					GatewayName:         "data-science-gateway",
					MLflowURL:           "https://gateway.example.com",
					MLflowURLConfigured: true,
				},
			}
			artifactKey := types.NamespacedName{Name: ArtifactsResourceName, Namespace: "opendatahub"}
			DeferCleanup(func() {
				for _, obj := range []client.Object{
					&appsv1.Deployment{ObjectMeta: metav1.ObjectMeta{Name: ArtifactsResourceName, Namespace: "opendatahub"}},
					&corev1.Service{ObjectMeta: metav1.ObjectMeta{Name: ArtifactsResourceName, Namespace: "opendatahub"}},
					&gatewayv1.HTTPRoute{ObjectMeta: metav1.ObjectMeta{Name: ArtifactsResourceName, Namespace: "opendatahub"}},
				} {
					Expect(client.IgnoreNotFound(k8sClient.Delete(ctx, obj))).To(Succeed())
				}
			})

			_, reconcileErr := controllerReconciler.Reconcile(ctx, reconcile.Request{NamespacedName: typeNamespacedName})
			Expect(reconcileErr).NotTo(HaveOccurred())

			Expect(k8sClient.Get(ctx, artifactKey, &appsv1.Deployment{})).To(Succeed())
			Expect(k8sClient.Get(ctx, artifactKey, &corev1.Service{})).To(Succeed())
			Expect(k8sClient.Get(ctx, artifactKey, &gatewayv1.HTTPRoute{})).To(Succeed())

			trackingDeployment := &appsv1.Deployment{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: ResourceName, Namespace: "opendatahub"}, trackingDeployment)).To(Succeed())
			trackingDeployment.Status.Replicas = 1
			trackingDeployment.Status.ReadyReplicas = 1
			Expect(k8sClient.Status().Update(ctx, trackingDeployment)).To(Succeed())

			_, reconcileErr = controllerReconciler.Reconcile(ctx, reconcile.Request{NamespacedName: typeNamespacedName})
			Expect(reconcileErr).NotTo(HaveOccurred())
			Expect(k8sClient.Get(ctx, typeNamespacedName, mlflow)).To(Succeed())
			available := meta.FindStatusCondition(mlflow.Status.Conditions, "Available")
			Expect(available).NotTo(BeNil())
			Expect(available.Status).To(Equal(metav1.ConditionFalse))
			Expect(available.Message).To(ContainSubstring(ArtifactsResourceName))
		})

		It("should create and clean up dedicated artifact resources", func() {
			Expect(k8sClient.Get(ctx, typeNamespacedName, mlflow)).To(Succeed())
			mlflow.Spec.BackendStoreURI = &pgStoreURI
			mlflow.Spec.DefaultArtifactRoot = nil
			mlflow.Spec.ArtifactsServer = &mlflowv1.ArtifactsServerSpec{Enabled: true}
			mlflow.Spec.ArtifactsDestination = ptr("s3://bucket/artifacts")
			Expect(k8sClient.Update(ctx, mlflow)).To(Succeed())

			controllerReconciler := &MLflowReconciler{
				Client:             k8sClient,
				Scheme:             k8sClient.Scheme(),
				Namespace:          "opendatahub",
				ChartPath:          "../../charts/mlflow",
				HTTPRouteAvailable: true,
				GCRBACWatchCache:   mustNewGCRBACWatchCache(),
			}
			Expect(controllerReconciler.reconcileArtifactsHTTPRoute(ctx, mlflow, "opendatahub", &config.OperatorConfig{
				GatewayName: "data-science-gateway",
			})).To(Succeed())

			artifactKey := types.NamespacedName{Name: ArtifactsResourceName, Namespace: "opendatahub"}
			artifactRoute := &gatewayv1.HTTPRoute{}
			Expect(k8sClient.Get(ctx, artifactKey, artifactRoute)).To(Succeed())
			Expect(artifactRoute.Spec.Rules).To(HaveLen(2))

			legacyRule := artifactRoute.Spec.Rules[0]
			Expect(legacyRule.Matches).To(HaveLen(1))
			Expect(legacyRule.Matches[0].Path).NotTo(BeNil())
			Expect(legacyRule.Matches[0].Path.Type).NotTo(BeNil())
			Expect(*legacyRule.Matches[0].Path.Type).To(Equal(gatewayv1.PathMatchPathPrefix))
			Expect(legacyRule.Matches[0].Path.Value).NotTo(BeNil())
			Expect(*legacyRule.Matches[0].Path.Value).To(Equal("/mlflow/api/2.0/mlflow-artifacts/artifacts"))
			Expect(legacyRule.Filters).To(HaveLen(1))
			Expect(legacyRule.Filters[0].Type).To(Equal(gatewayv1.HTTPRouteFilterURLRewrite))
			Expect(legacyRule.Filters[0].URLRewrite).NotTo(BeNil())
			Expect(legacyRule.Filters[0].URLRewrite.Path).NotTo(BeNil())
			Expect(legacyRule.Filters[0].URLRewrite.Path.Type).To(Equal(gatewayv1.PrefixMatchHTTPPathModifier))
			Expect(legacyRule.Filters[0].URLRewrite.Path.ReplacePrefixMatch).NotTo(BeNil())
			Expect(*legacyRule.Filters[0].URLRewrite.Path.ReplacePrefixMatch).To(Equal(
				"/mlflow-artifacts/api/2.0/mlflow-artifacts/artifacts",
			))
			Expect(legacyRule.BackendRefs).To(HaveLen(1))
			Expect(legacyRule.BackendRefs[0].Name).To(Equal(gatewayv1.ObjectName(ArtifactsResourceName)))
			Expect(legacyRule.BackendRefs[0].Port).NotTo(BeNil())
			Expect(*legacyRule.BackendRefs[0].Port).To(Equal(gatewayv1.PortNumber(8443)))
			Expect(legacyRule.BackendRefs[0].Weight).NotTo(BeNil())
			Expect(*legacyRule.BackendRefs[0].Weight).To(Equal(int32(1)))

			artifactRule := artifactRoute.Spec.Rules[1]
			Expect(artifactRule.Matches).To(HaveLen(1))
			Expect(artifactRule.Matches[0].Path).NotTo(BeNil())
			Expect(artifactRule.Matches[0].Path.Value).NotTo(BeNil())
			Expect(*artifactRule.Matches[0].Path.Value).To(Equal("/" + ArtifactsResourceName))
			Expect(artifactRule.Filters).To(BeEmpty())
			Expect(artifactRule.BackendRefs).To(HaveLen(1))
			Expect(artifactRule.BackendRefs[0].Name).To(Equal(gatewayv1.ObjectName(ArtifactsResourceName)))
			Expect(artifactRoute.Labels).To(HaveKeyWithValue("app", ResourceName))

			artifactLabels := map[string]string{"app": ArtifactsResourceName}
			ownerReference := *metav1.NewControllerRef(mlflow, mlflowv1.GroupVersion.WithKind("MLflow"))
			Expect(k8sClient.Create(ctx, &appsv1.Deployment{
				ObjectMeta: metav1.ObjectMeta{
					Name:            ArtifactsResourceName,
					Namespace:       "opendatahub",
					OwnerReferences: []metav1.OwnerReference{ownerReference},
				},
				Spec: appsv1.DeploymentSpec{
					Selector: &metav1.LabelSelector{MatchLabels: artifactLabels},
					Template: corev1.PodTemplateSpec{
						ObjectMeta: metav1.ObjectMeta{Labels: artifactLabels},
						Spec: corev1.PodSpec{Containers: []corev1.Container{{
							Name: "mlflow-artifacts", Image: "example.com/mlflow:test",
						}}},
					},
				},
			})).To(Succeed())
			DeferCleanup(func() {
				for _, obj := range []client.Object{
					&appsv1.Deployment{ObjectMeta: metav1.ObjectMeta{Name: ArtifactsResourceName, Namespace: "opendatahub"}},
					&corev1.Service{ObjectMeta: metav1.ObjectMeta{Name: ArtifactsResourceName, Namespace: "opendatahub"}},
				} {
					Expect(client.IgnoreNotFound(k8sClient.Delete(ctx, obj))).To(Succeed())
				}
			})
			Expect(k8sClient.Create(ctx, &corev1.Service{
				ObjectMeta: metav1.ObjectMeta{
					Name:            ArtifactsResourceName,
					Namespace:       "opendatahub",
					OwnerReferences: []metav1.OwnerReference{ownerReference},
				},
				Spec: corev1.ServiceSpec{Ports: []corev1.ServicePort{{
					Name: "https", Port: 8443,
				}}},
			})).To(Succeed())

			Expect(k8sClient.Get(ctx, typeNamespacedName, mlflow)).To(Succeed())
			mlflow.Spec.ArtifactsServer = nil
			mlflow.Spec.DefaultArtifactRoot = ptr("s3://default/artifacts")
			Expect(k8sClient.Update(ctx, mlflow)).To(Succeed())
			_, reconcileErr := controllerReconciler.Reconcile(ctx, reconcile.Request{NamespacedName: typeNamespacedName})
			Expect(reconcileErr).NotTo(HaveOccurred())

			Expect(errors.IsNotFound(k8sClient.Get(ctx, artifactKey, &appsv1.Deployment{}))).To(BeTrue())
			Expect(errors.IsNotFound(k8sClient.Get(ctx, artifactKey, &corev1.Service{}))).To(BeTrue())
			Expect(errors.IsNotFound(k8sClient.Get(ctx, artifactKey, &gatewayv1.HTTPRoute{}))).To(BeTrue())
		})

		It("should include the resource suffix in dedicated artifact routes", func() {
			customMLflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "custom", UID: types.UID("custom-uid")},
				Spec: mlflowv1.MLflowSpec{
					ArtifactsServer: &mlflowv1.ArtifactsServerSpec{Enabled: true},
				},
			}
			controllerReconciler := &MLflowReconciler{
				Client:             k8sClient,
				Scheme:             k8sClient.Scheme(),
				HTTPRouteAvailable: true,
			}
			Expect(controllerReconciler.reconcileArtifactsHTTPRoute(
				ctx,
				customMLflow,
				"opendatahub",
				&config.OperatorConfig{GatewayName: "data-science-gateway"},
			)).To(Succeed())

			artifactKey := types.NamespacedName{Name: "mlflow-artifacts-custom", Namespace: "opendatahub"}
			DeferCleanup(func() {
				Expect(client.IgnoreNotFound(k8sClient.Delete(ctx, &gatewayv1.HTTPRoute{
					ObjectMeta: metav1.ObjectMeta{Name: artifactKey.Name, Namespace: artifactKey.Namespace},
				}))).To(Succeed())
			})

			artifactRoute := &gatewayv1.HTTPRoute{}
			Expect(k8sClient.Get(ctx, artifactKey, artifactRoute)).To(Succeed())
			Expect(artifactRoute.Spec.Rules).To(HaveLen(2))
			Expect(*artifactRoute.Spec.Rules[0].Matches[0].Path.Value).To(Equal(
				"/mlflow-custom/api/2.0/mlflow-artifacts/artifacts",
			))
			Expect(*artifactRoute.Spec.Rules[0].Filters[0].URLRewrite.Path.ReplacePrefixMatch).To(Equal(
				"/mlflow-artifacts-custom/api/2.0/mlflow-artifacts/artifacts",
			))
			Expect(*artifactRoute.Spec.Rules[1].Matches[0].Path.Value).To(Equal("/mlflow-artifacts-custom"))
			Expect(artifactRoute.Spec.Rules[0].BackendRefs[0].Name).To(Equal(
				gatewayv1.ObjectName("mlflow-artifacts-custom"),
			))
		})

		It("should preserve unowned artifact resources when split serving is disabled", func() {
			foreignService := &corev1.Service{
				ObjectMeta: metav1.ObjectMeta{Name: ArtifactsResourceName, Namespace: "opendatahub"},
				Spec:       corev1.ServiceSpec{Ports: []corev1.ServicePort{{Name: "https", Port: 8443}}},
			}
			Expect(k8sClient.Create(ctx, foreignService)).To(Succeed())
			DeferCleanup(func() {
				service := &corev1.Service{}
				key := types.NamespacedName{Name: ArtifactsResourceName, Namespace: "opendatahub"}
				if err := k8sClient.Get(ctx, key, service); err == nil {
					Expect(k8sClient.Delete(ctx, service)).To(Succeed())
				}
			})

			controllerReconciler := &MLflowReconciler{
				Client:             k8sClient,
				Scheme:             k8sClient.Scheme(),
				Namespace:          "opendatahub",
				ChartPath:          "../../charts/mlflow",
				HTTPRouteAvailable: false,
				GCRBACWatchCache:   mustNewGCRBACWatchCache(),
			}
			_, reconcileErr := controllerReconciler.Reconcile(ctx, reconcile.Request{NamespacedName: typeNamespacedName})
			Expect(reconcileErr).NotTo(HaveOccurred())
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name: ArtifactsResourceName, Namespace: "opendatahub",
			}, &corev1.Service{})).To(Succeed())
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

		It("rejects enabled file-based trace archival without ReadWriteMany storage", func() {
			serveArtifactsTrue := true
			location := "file:///mlflow/traces"
			schedule := "0 */6 * * *"
			retention := "30d"
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:  &serveArtifactsTrue,
					BackendStoreURI: &pgStoreURI,
					Storage: &corev1.PersistentVolumeClaimSpec{
						AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
					},
					TraceArchival: &mlflowv1.TraceArchivalSpec{
						Enabled:   true,
						Schedule:  &schedule,
						Location:  &location,
						Retention: &retention,
					},
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("enabled file-based traceArchival.location requires storage with ReadWriteMany"))
		})

		It("allows enabled file-based trace archival with ReadWriteMany storage", func() {
			serveArtifactsTrue := true
			location := "file:///mlflow/traces"
			schedule := "0 */6 * * *"
			retention := "30d"
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:  &serveArtifactsTrue,
					BackendStoreURI: &pgStoreURI,
					Storage: &corev1.PersistentVolumeClaimSpec{
						AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteMany},
					},
					TraceArchival: &mlflowv1.TraceArchivalSpec{
						Enabled:   true,
						Schedule:  &schedule,
						Location:  &location,
						Retention: &retention,
					},
				},
			}
			Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())
		})

		It("allows a legacy omitted storage access mode to be set to ReadWriteOnce", func() {
			serveArtifactsTrue := true
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:  &serveArtifactsTrue,
					BackendStoreURI: &pgStoreURI,
					Storage:         &corev1.PersistentVolumeClaimSpec{},
				},
			}
			Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())

			mlflow.Spec.Storage.AccessModes = []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce}
			Expect(k8sClient.Update(ctx, mlflow)).To(Succeed())
		})

		It("rejects setting a legacy omitted storage access mode to ReadWriteMany", func() {
			serveArtifactsTrue := true
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:  &serveArtifactsTrue,
					BackendStoreURI: &pgStoreURI,
					Storage:         &corev1.PersistentVolumeClaimSpec{},
				},
			}
			Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())

			mlflow.Spec.Storage.AccessModes = []corev1.PersistentVolumeAccessMode{corev1.ReadWriteMany}
			err := k8sClient.Update(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("an omitted legacy access mode may only be set to ReadWriteOnce"))
		})

		It("rejects changing an established storage access mode", func() {
			serveArtifactsTrue := true
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:  &serveArtifactsTrue,
					BackendStoreURI: &pgStoreURI,
					Storage: &corev1.PersistentVolumeClaimSpec{
						AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
					},
				},
			}
			Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())

			mlflow.Spec.Storage.AccessModes = []corev1.PersistentVolumeAccessMode{corev1.ReadWriteMany}
			err := k8sClient.Update(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("storage and its accessModes are immutable once configured"))
		})

		It("rejects removing an established storage access mode", func() {
			serveArtifactsTrue := true
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:  &serveArtifactsTrue,
					BackendStoreURI: &pgStoreURI,
					Storage: &corev1.PersistentVolumeClaimSpec{
						AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
					},
				},
			}
			Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())

			mlflow.Spec.Storage.AccessModes = nil
			err := k8sClient.Update(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("storage and its accessModes are immutable once configured"))
		})

		It("rejects removing configured storage", func() {
			serveArtifactsTrue := true
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:  &serveArtifactsTrue,
					BackendStoreURI: &pgStoreURI,
					Storage: &corev1.PersistentVolumeClaimSpec{
						AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
					},
				},
			}
			Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())

			mlflow.Spec.Storage = nil
			err := k8sClient.Update(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("storage and its accessModes are immutable once configured"))
		})

		It("allows missing defaultArtifactRoot when artifactsServer is enabled", func() {
			artifactsDestination := "s3://bucket/artifacts"
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      &pgStoreURI,
					ArtifactsDestination: &artifactsDestination,
					ArtifactsServer:      &mlflowv1.ArtifactsServerSpec{Enabled: true},
				},
			}
			Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())
		})

		It("rejects defaultArtifactRoot when artifactsServer is enabled", func() {
			artifactsDestination := "s3://bucket/artifacts"
			defaultArtifactRoot := "s3://bucket/custom-root"
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      &pgStoreURI,
					ArtifactsDestination: &artifactsDestination,
					DefaultArtifactRoot:  &defaultArtifactRoot,
					ArtifactsServer:      &mlflowv1.ArtifactsServerSpec{Enabled: true},
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("defaultArtifactRoot and artifactsServer.enabled are mutually exclusive"))
		})

		It("rejects enabling artifact serving on both deployments", func() {
			serveArtifactsTrue := true
			artifactsDestination := "s3://bucket/artifacts"
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      &pgStoreURI,
					ArtifactsDestination: &artifactsDestination,
					ServeArtifacts:       &serveArtifactsTrue,
					ArtifactsServer:      &mlflowv1.ArtifactsServerSpec{Enabled: true},
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("serveArtifacts and artifactsServer.enabled are mutually exclusive"))
		})

		It("rejects an artifact server replica count below one", func() {
			zero := int32(0)
			artifactsDestination := "s3://bucket/artifacts"
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      &pgStoreURI,
					ArtifactsDestination: &artifactsDestination,
					ArtifactsServer:      &mlflowv1.ArtifactsServerSpec{Enabled: true, Replicas: &zero},
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("spec.artifactsServer.replicas"))
		})

		DescribeTable("validates artifact server resource claim sources",
			func(claim corev1.PodResourceClaim, wantValid bool) {
				artifactsDestination := "s3://bucket/artifacts"
				mlflow := &mlflowv1.MLflow{
					ObjectMeta: metav1.ObjectMeta{Name: resourceName},
					Spec: mlflowv1.MLflowSpec{
						BackendStoreURI:      &pgStoreURI,
						ArtifactsDestination: &artifactsDestination,
						ArtifactsServer: &mlflowv1.ArtifactsServerSpec{
							Enabled:        true,
							ResourceClaims: []corev1.PodResourceClaim{claim},
						},
					},
				}
				err := k8sClient.Create(ctx, mlflow)
				if wantValid {
					Expect(err).NotTo(HaveOccurred())
					return
				}
				Expect(errors.IsInvalid(err)).To(BeTrue())
				Expect(err.Error()).To(ContainSubstring(
					"each artifactsServer.resourceClaims entry must set exactly one non-empty value",
				))
			},
			Entry("accepts a direct claim", corev1.PodResourceClaim{
				Name: "artifact-gpu", ResourceClaimName: ptr("existing-artifact-gpu"),
			}, true),
			Entry("accepts a claim template", corev1.PodResourceClaim{
				Name: "artifact-gpu", ResourceClaimTemplateName: ptr("artifact-gpu-template"),
			}, true),
			Entry("rejects both sources", corev1.PodResourceClaim{
				Name:                      "artifact-gpu",
				ResourceClaimName:         ptr("existing-artifact-gpu"),
				ResourceClaimTemplateName: ptr("artifact-gpu-template"),
			}, false),
			Entry("rejects neither source", corev1.PodResourceClaim{Name: "artifact-gpu"}, false),
			Entry("rejects an empty source", corev1.PodResourceClaim{
				Name: "artifact-gpu", ResourceClaimName: ptr(""),
			}, false),
		)

		It("rejects an artifact server without an explicit destination", func() {
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: &pgStoreURI,
					ArtifactsServer: &mlflowv1.ArtifactsServerSpec{Enabled: true},
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("artifactsDestination must be set when artifactsServer is enabled"))
		})

		It("allows one file-backed artifact server replica with ReadWriteOnce storage", func() {
			artifactsDestination := "file:///mlflow/artifacts"
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      &pgStoreURI,
					ArtifactsDestination: &artifactsDestination,
					ArtifactsServer:      &mlflowv1.ArtifactsServerSpec{Enabled: true},
					Storage: &corev1.PersistentVolumeClaimSpec{
						AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
						Resources: corev1.VolumeResourceRequirements{Requests: corev1.ResourceList{
							corev1.ResourceStorage: resource.MustParse("1Gi"),
						}},
					},
				},
			}
			Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())
		})

		It("rejects multiple file-backed artifact server replicas with ReadWriteOnce storage", func() {
			artifactsDestination := "file:///mlflow/artifacts"
			replicas := int32(2)
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      &pgStoreURI,
					ArtifactsDestination: &artifactsDestination,
					ArtifactsServer:      &mlflowv1.ArtifactsServerSpec{Enabled: true, Replicas: &replicas},
					Storage: &corev1.PersistentVolumeClaimSpec{
						AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
					},
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("multiple artifact server replicas"))
		})

		It("allows multiple file-backed artifact server replicas with ReadWriteMany storage", func() {
			artifactsDestination := "file:///mlflow/artifacts"
			replicas := int32(2)
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI:      &pgStoreURI,
					ArtifactsDestination: &artifactsDestination,
					ArtifactsServer:      &mlflowv1.ArtifactsServerSpec{Enabled: true, Replicas: &replicas},
					Storage: &corev1.PersistentVolumeClaimSpec{
						AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteMany},
						Resources: corev1.VolumeResourceRequirements{Requests: corev1.ResourceList{
							corev1.ResourceStorage: resource.MustParse("1Gi"),
						}},
					},
				},
			}
			Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())
		})

		DescribeTable("rejects an artifact server with inline SQLite metadata",
			func(storeField string) {
				artifactsDestination := "s3://bucket/artifacts"
				sqliteURI := "sqlite:////mlflow/mlflow.db"
				spec := mlflowv1.MLflowSpec{
					BackendStoreURI:      &pgStoreURI,
					ArtifactsDestination: &artifactsDestination,
					ArtifactsServer:      &mlflowv1.ArtifactsServerSpec{Enabled: true},
					Storage:              &corev1.PersistentVolumeClaimSpec{},
				}
				switch storeField {
				case "backendStoreUri":
					spec.BackendStoreURI = &sqliteURI
				case "registryStoreUri":
					spec.RegistryStoreURI = &sqliteURI
				case "readReplicaBackendStoreUri":
					spec.ReadReplicaBackendStoreURI = &sqliteURI
				}
				err := k8sClient.Create(ctx, &mlflowv1.MLflow{
					ObjectMeta: metav1.ObjectMeta{Name: resourceName},
					Spec:       spec,
				})
				Expect(errors.IsInvalid(err)).To(BeTrue())
				Expect(err.Error()).To(ContainSubstring("artifactsServer cannot be enabled with an inline SQLite"))
			},
			Entry("backend store", "backendStoreUri"),
			Entry("registry store", "registryStoreUri"),
			Entry("read replica", "readReplicaBackendStoreUri"),
		)

		It("rejects multiple tracking replicas sharing ReadWriteOnce storage", func() {
			serveArtifactsTrue := true
			replicas := int32(2)
			sqliteURI := "sqlite:////mlflow/mlflow.db"
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					Replicas:         &replicas,
					ServeArtifacts:   &serveArtifactsTrue,
					BackendStoreURI:  &sqliteURI,
					RegistryStoreURI: &sqliteURI,
					Storage: &corev1.PersistentVolumeClaimSpec{
						AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
					},
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("multiple tracking replicas that use persistent storage"))
		})

		It("allows multiple tracking replicas with unused ReadWriteOnce storage", func() {
			serveArtifactsTrue := true
			replicas := int32(2)
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					Replicas:         &replicas,
					ServeArtifacts:   &serveArtifactsTrue,
					BackendStoreURI:  &pgStoreURI,
					RegistryStoreURI: &pgStoreURI,
					Storage: &corev1.PersistentVolumeClaimSpec{
						AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
					},
				},
			}
			Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())
		})

		It("allows split serving with secret-backed metadata and ReadWriteOnce artifact storage", func() {
			replicas := int32(3)
			artifactsDestination := "file:///mlflow/artifacts"
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					Replicas: &replicas,
					BackendStoreURIFrom: &corev1.SecretKeySelector{
						LocalObjectReference: corev1.LocalObjectReference{Name: "remote-db-credentials"},
						Key:                  "backend-uri",
					},
					ArtifactsDestination: &artifactsDestination,
					ArtifactsServer:      &mlflowv1.ArtifactsServerSpec{Enabled: true},
					Storage: &corev1.PersistentVolumeClaimSpec{
						AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
					},
				},
			}
			Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())
		})

		It("rejects trace archival sharing ReadWriteOnce SQLite metadata storage", func() {
			serveArtifactsTrue := true
			sqliteURI := "sqlite:////mlflow/mlflow.db"
			location := "s3://bucket/traces"
			schedule := "0 */6 * * *"
			retention := "30d"
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:  &serveArtifactsTrue,
					BackendStoreURI: &sqliteURI,
					Storage: &corev1.PersistentVolumeClaimSpec{
						AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
					},
					TraceArchival: &mlflowv1.TraceArchivalSpec{
						Enabled:   true,
						Schedule:  &schedule,
						Location:  &location,
						Retention: &retention,
					},
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("trace archival with persistent metadata storage"))
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

		It("allows readReplicaBackendStoreUri", func() {
			serveArtifactsTrue := true
			readReplicaURI := "postgresql://reader:5432/db"
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:             &serveArtifactsTrue,
					BackendStoreURI:            &pgStoreURI,
					ReadReplicaBackendStoreURI: &readReplicaURI,
				},
			}
			Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())
		})

		It("allows readReplicaBackendStoreUriFrom", func() {
			serveArtifactsTrue := true
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:  &serveArtifactsTrue,
					BackendStoreURI: &pgStoreURI,
					ReadReplicaBackendStoreURIFrom: &corev1.SecretKeySelector{
						LocalObjectReference: corev1.LocalObjectReference{Name: "db-credentials"},
						Key:                  "read-replica-uri",
					},
				},
			}
			Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())
		})

		It("rejects both read-replica backend store forms", func() {
			serveArtifactsTrue := true
			readReplicaURI := "postgresql://reader:5432/db"
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:             &serveArtifactsTrue,
					BackendStoreURI:            &pgStoreURI,
					ReadReplicaBackendStoreURI: &readReplicaURI,
					ReadReplicaBackendStoreURIFrom: &corev1.SecretKeySelector{
						LocalObjectReference: corev1.LocalObjectReference{Name: "db-credentials"},
						Key:                  "read-replica-uri",
					},
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("readReplicaBackendStoreUri and readReplicaBackendStoreUriFrom are mutually exclusive"))
		})

		It("rejects an incomplete read-replica secret selector", func() {
			serveArtifactsTrue := true
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:  &serveArtifactsTrue,
					BackendStoreURI: &pgStoreURI,
					ReadReplicaBackendStoreURIFrom: &corev1.SecretKeySelector{
						LocalObjectReference: corev1.LocalObjectReference{Name: "db-credentials"},
					},
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("readReplicaBackendStoreUriFrom.name and readReplicaBackendStoreUriFrom.key must be non-empty"))
		})

		It("rejects an unsupported read-replica URI scheme", func() {
			serveArtifactsTrue := true
			readReplicaURI := "file:///mlflow/replica"
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:             &serveArtifactsTrue,
					BackendStoreURI:            &pgStoreURI,
					ReadReplicaBackendStoreURI: &readReplicaURI,
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("readReplicaBackendStoreUri must use a supported SQL metadata store URI scheme"))
		})

		It("rejects a SQLite read replica without storage", func() {
			serveArtifactsTrue := true
			readReplicaURI := "sqlite:////mlflow/replica.db"
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: resourceName},
				Spec: mlflowv1.MLflowSpec{
					ServeArtifacts:             &serveArtifactsTrue,
					BackendStoreURI:            &pgStoreURI,
					ReadReplicaBackendStoreURI: &readReplicaURI,
				},
			}
			err := k8sClient.Create(ctx, mlflow)
			Expect(errors.IsInvalid(err)).To(BeTrue())
			Expect(err.Error()).To(ContainSubstring("storage must be configured when using a file-based read-replica backend store"))
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

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
	"context"
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"

	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	apimeta "k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

var _ = Describe("Migration reconcile", func() {
	const resourceName = "mlflow"

	newReconciler := func(namespace string) *MLflowReconciler {
		return &MLflowReconciler{
			Client:               k8sClient,
			Scheme:               k8sClient.Scheme(),
			Namespace:            namespace,
			ChartPath:            "../../charts/mlflow",
			ConsoleLinkAvailable: false,
			HTTPRouteAvailable:   false,
		}
	}

	newMLflow := func() *mlflowv1.MLflow {
		backendStoreURI := "sqlite:////mlflow/mlflow.db"
		replicas := int32(1)
		serveArtifacts := true
		return &mlflowv1.MLflow{
			ObjectMeta: metav1.ObjectMeta{Name: resourceName},
			Spec: mlflowv1.MLflowSpec{
				Replicas:        &replicas,
				ServeArtifacts:  &serveArtifacts,
				BackendStoreURI: &backendStoreURI,
				Storage: &corev1.PersistentVolumeClaimSpec{
					AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
				},
			},
		}
	}

	It("creates a migration Job, records status.version on success, and restores replicas", func() {
		ctx := context.Background()
		namespace := "migration-success"
		Expect(k8sClient.Create(ctx, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: namespace}})).To(Succeed())
		DeferCleanup(func() {
			_ = k8sClient.Delete(ctx, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: namespace}})
			_ = k8sClient.Delete(ctx, &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: resourceName}})
		})

		mlflow := newMLflow()
		Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, mlflow)).To(Succeed())

		reconciler := newReconciler(namespace)
		_, err := reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())

		job := &batchv1.Job{}
		jobKey := types.NamespacedName{Name: migrationJobName(mlflow), Namespace: namespace}
		Expect(k8sClient.Get(ctx, jobKey, job)).To(Succeed())
		Expect(job.OwnerReferences).NotTo(BeEmpty())
		Expect(job.OwnerReferences[0].Kind).To(Equal("MLflow"))

		deployment := &appsv1.Deployment{}
		deploymentKey := types.NamespacedName{Name: ResourceName, Namespace: namespace}
		Expect(k8sClient.Get(ctx, deploymentKey, deployment)).To(Succeed())
		Expect(deployment.Spec.Replicas).NotTo(BeNil())
		Expect(*deployment.Spec.Replicas).To(Equal(int32(0)))

		now := metav1.Now()
		job.Status.Succeeded = 1
		job.Status.StartTime = &now
		job.Status.CompletionTime = &now
		job.Status.Conditions = []batchv1.JobCondition{
			{Type: batchv1.JobSuccessCriteriaMet, Status: corev1.ConditionTrue},
			{Type: batchv1.JobComplete, Status: corev1.ConditionTrue},
		}
		Expect(k8sClient.Status().Update(ctx, job)).To(Succeed())

		_, err = reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())

		updatedMLflow := &mlflowv1.MLflow{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, updatedMLflow)).To(Succeed())
		Expect(updatedMLflow.Status.Version).To(Equal(SupportedMLflowVersion))

		Expect(k8sClient.Get(ctx, deploymentKey, deployment)).To(Succeed())
		Expect(deployment.Spec.Replicas).NotTo(BeNil())
		Expect(*deployment.Spec.Replicas).To(Equal(int32(1)))
	})

	It("clears the force-migrate annotation after a successful forced migration", func() {
		ctx := context.Background()
		namespace := "migration-force"
		Expect(k8sClient.Create(ctx, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: namespace}})).To(Succeed())
		DeferCleanup(func() {
			_ = k8sClient.Delete(ctx, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: namespace}})
			_ = k8sClient.Delete(ctx, &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: resourceName}})
		})

		mlflow := newMLflow()
		mlflow.Annotations = map[string]string{forceMigrateAnnotation: ""}
		Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, mlflow)).To(Succeed())

		reconciler := newReconciler(namespace)
		_, err := reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())

		job := &batchv1.Job{}
		jobKey := types.NamespacedName{Name: migrationJobName(mlflow), Namespace: namespace}
		Expect(k8sClient.Get(ctx, jobKey, job)).To(Succeed())

		now := metav1.Now()
		job.Status.Succeeded = 1
		job.Status.StartTime = &now
		job.Status.CompletionTime = &now
		job.Status.Conditions = []batchv1.JobCondition{
			{Type: batchv1.JobSuccessCriteriaMet, Status: corev1.ConditionTrue},
			{Type: batchv1.JobComplete, Status: corev1.ConditionTrue},
		}
		Expect(k8sClient.Status().Update(ctx, job)).To(Succeed())

		_, err = reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())

		updatedMLflow := &mlflowv1.MLflow{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, updatedMLflow)).To(Succeed())
		Expect(updatedMLflow.Annotations).NotTo(HaveKey(forceMigrateAnnotation))
		Expect(updatedMLflow.Status.Version).To(Equal(SupportedMLflowVersion))
	})

	It("keeps replicas at zero and reports failure when the migration Job fails", func() {
		ctx := context.Background()
		namespace := "migration-failure"
		Expect(k8sClient.Create(ctx, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: namespace}})).To(Succeed())
		DeferCleanup(func() {
			_ = k8sClient.Delete(ctx, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: namespace}})
			_ = k8sClient.Delete(ctx, &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: resourceName}})
		})

		mlflow := newMLflow()
		Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, mlflow)).To(Succeed())

		reconciler := newReconciler(namespace)
		_, err := reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())

		job := &batchv1.Job{}
		jobKey := types.NamespacedName{Name: migrationJobName(mlflow), Namespace: namespace}
		Expect(k8sClient.Get(ctx, jobKey, job)).To(Succeed())

		now := metav1.Now()
		job.Status.Failed = 1
		job.Status.StartTime = &now
		job.Status.Conditions = []batchv1.JobCondition{
			{Type: batchv1.JobFailureTarget, Status: corev1.ConditionTrue},
			{Type: batchv1.JobFailed, Status: corev1.ConditionTrue},
		}
		Expect(k8sClient.Status().Update(ctx, job)).To(Succeed())

		_, err = reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())

		deployment := &appsv1.Deployment{}
		deploymentKey := types.NamespacedName{Name: ResourceName, Namespace: namespace}
		Expect(k8sClient.Get(ctx, deploymentKey, deployment)).To(Succeed())
		Expect(deployment.Spec.Replicas).NotTo(BeNil())
		Expect(*deployment.Spec.Replicas).To(Equal(int32(0)))

		updatedMLflow := &mlflowv1.MLflow{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, updatedMLflow)).To(Succeed())
		Expect(updatedMLflow.Status.Version).To(BeEmpty())
		condition := apimeta.FindStatusCondition(updatedMLflow.Status.Conditions, "Available")
		Expect(condition).NotTo(BeNil())
		Expect(condition.Reason).To(Equal("MigrationFailed"))
	})

	It("restarts a failed bootstrap migration when force-migrate is added", func() {
		ctx := context.Background()
		namespace := "migration-force-retry"
		Expect(k8sClient.Create(ctx, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: namespace}})).To(Succeed())
		DeferCleanup(func() {
			_ = k8sClient.Delete(ctx, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: namespace}})
			_ = k8sClient.Delete(ctx, &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: resourceName}})
		})

		mlflow := newMLflow()
		Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, mlflow)).To(Succeed())

		reconciler := newReconciler(namespace)
		_, err := reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())

		job := &batchv1.Job{}
		jobKey := types.NamespacedName{Name: migrationJobName(mlflow), Namespace: namespace}
		Expect(k8sClient.Get(ctx, jobKey, job)).To(Succeed())

		now := metav1.Now()
		job.Status.Failed = 1
		job.Status.StartTime = &now
		job.Status.Conditions = []batchv1.JobCondition{
			{Type: batchv1.JobFailureTarget, Status: corev1.ConditionTrue},
			{Type: batchv1.JobFailed, Status: corev1.ConditionTrue},
		}
		Expect(k8sClient.Status().Update(ctx, job)).To(Succeed())

		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, mlflow)).To(Succeed())
		before := mlflow.DeepCopy()
		mlflow.Annotations = map[string]string{forceMigrateAnnotation: ""}
		Expect(k8sClient.Patch(ctx, mlflow, client.MergeFrom(before))).To(Succeed())

		result, err := reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())
		Expect(result.RequeueAfter).To(Equal(migrationJobRequeueAfter))

		Eventually(func() bool {
			job := &batchv1.Job{}
			err := k8sClient.Get(ctx, jobKey, job)
			return errors.IsNotFound(err) || job.GetDeletionTimestamp() != nil
		}, 5*time.Second, 100*time.Millisecond).Should(BeTrue())

		_, err = reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())
		Expect(k8sClient.Get(ctx, jobKey, &batchv1.Job{})).To(Succeed())
	})
})

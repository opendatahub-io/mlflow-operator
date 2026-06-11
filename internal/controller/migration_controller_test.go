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
			GCRBACWatchCache:     mustNewGCRBACWatchCache(),
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

	It("creates a migration Job, restores replicas, and records status.version after rollout readiness", func() {
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
		Expect(updatedMLflow.Status.Version).To(BeEmpty())
		migrationCondition := apimeta.FindStatusCondition(updatedMLflow.Status.Conditions, migrationConditionType)
		Expect(migrationCondition).NotTo(BeNil())
		Expect(migrationCondition.Status).To(Equal(metav1.ConditionUnknown))

		Expect(k8sClient.Get(ctx, deploymentKey, deployment)).To(Succeed())
		Expect(deployment.Spec.Replicas).NotTo(BeNil())
		Expect(*deployment.Spec.Replicas).To(Equal(int32(1)))
		deployment.Status.Replicas = 1
		deployment.Status.ReadyReplicas = 1
		Expect(k8sClient.Status().Update(ctx, deployment)).To(Succeed())

		_, err = reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())

		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, updatedMLflow)).To(Succeed())
		Expect(updatedMLflow.Status.Version).To(Equal(SupportedMLflowVersion))
		migrationCondition = apimeta.FindStatusCondition(updatedMLflow.Status.Conditions, migrationConditionType)
		Expect(migrationCondition).NotTo(BeNil())
		Expect(migrationCondition.Status).To(Equal(metav1.ConditionTrue))
		Expect(migrationCondition.ObservedGeneration).To(Equal(updatedMLflow.Generation))
		Expect(migrationCondition.Reason).To(Equal("MigrationSucceeded"))
		progressing := apimeta.FindStatusCondition(updatedMLflow.Status.Conditions, "Progressing")
		Expect(progressing).NotTo(BeNil())
		Expect(progressing.Reason).To(Equal("ReconcileComplete"))
	})

	It("clears the force-migrate annotation after the forced migration rollout is ready", func() {
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
		Expect(updatedMLflow.Annotations).To(HaveKey(forceMigrateAnnotation))
		Expect(updatedMLflow.Status.Version).To(BeEmpty())

		deployment := &appsv1.Deployment{}
		deploymentKey := types.NamespacedName{Name: ResourceName, Namespace: namespace}
		Expect(k8sClient.Get(ctx, deploymentKey, deployment)).To(Succeed())
		deployment.Status.Replicas = 1
		deployment.Status.ReadyReplicas = 1
		Expect(k8sClient.Status().Update(ctx, deployment)).To(Succeed())

		_, err = reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())

		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, updatedMLflow)).To(Succeed())
		Expect(updatedMLflow.Annotations).NotTo(HaveKey(forceMigrateAnnotation))
		Expect(updatedMLflow.Status.Version).To(Equal(SupportedMLflowVersion))
		migrationCondition := apimeta.FindStatusCondition(updatedMLflow.Status.Conditions, migrationConditionType)
		Expect(migrationCondition).NotTo(BeNil())
		Expect(migrationCondition.Status).To(Equal(metav1.ConditionTrue))
		Expect(migrationCondition.ObservedGeneration).To(Equal(updatedMLflow.Generation))
	})

	It("restarts a successful migration Job when force-migrate is added before status is updated", func() {
		ctx := context.Background()
		namespace := "migration-force-stale-success"
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
		job.Status.Succeeded = 1
		job.Status.StartTime = &now
		job.Status.CompletionTime = &now
		job.Status.Conditions = []batchv1.JobCondition{
			{Type: batchv1.JobSuccessCriteriaMet, Status: corev1.ConditionTrue},
			{Type: batchv1.JobComplete, Status: corev1.ConditionTrue},
		}
		Expect(k8sClient.Status().Update(ctx, job)).To(Succeed())

		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, mlflow)).To(Succeed())
		before := mlflow.DeepCopy()
		mlflow.Annotations = map[string]string{forceMigrateAnnotation: ""}
		Expect(k8sClient.Patch(ctx, mlflow, client.MergeFrom(before))).To(Succeed())

		result, err := reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())
		Expect(result.RequeueAfter).To(Equal(migrationJobRequeueAfter))

		updatedMLflow := &mlflowv1.MLflow{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, updatedMLflow)).To(Succeed())
		Expect(updatedMLflow.Annotations).To(HaveKey(forceMigrateAnnotation))
		Expect(updatedMLflow.Status.Version).To(BeEmpty())
		migrationCondition := apimeta.FindStatusCondition(updatedMLflow.Status.Conditions, migrationConditionType)
		Expect(migrationCondition).NotTo(BeNil())
		Expect(migrationCondition.Reason).To(Equal(migrationReasonForceRestartRequested))

		Eventually(func() bool {
			job := &batchv1.Job{}
			err := k8sClient.Get(ctx, jobKey, job)
			return errors.IsNotFound(err) || job.GetDeletionTimestamp() != nil
		}, 5*time.Second, 100*time.Millisecond).Should(BeTrue())

		_, err = reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())
		Expect(k8sClient.Get(ctx, jobKey, &batchv1.Job{})).To(Succeed())
	})

	It("does not treat the old successful Job as the forced rerun while restart is still pending", func() {
		ctx := context.Background()
		namespace := "migration-force-pending-delete"
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
		job.Status.Succeeded = 1
		job.Status.StartTime = &now
		job.Status.CompletionTime = &now
		job.Status.Conditions = []batchv1.JobCondition{
			{Type: batchv1.JobSuccessCriteriaMet, Status: corev1.ConditionTrue},
			{Type: batchv1.JobComplete, Status: corev1.ConditionTrue},
		}
		Expect(k8sClient.Status().Update(ctx, job)).To(Succeed())

		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, mlflow)).To(Succeed())
		mlflow.SetMigrationProgress(migrationReasonForceRestartRequested, "waiting for previous successful Job to be deleted")
		Expect(k8sClient.Status().Update(ctx, mlflow)).To(Succeed())

		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, mlflow)).To(Succeed())
		before := mlflow.DeepCopy()
		mlflow.Annotations = map[string]string{forceMigrateAnnotation: ""}
		Expect(k8sClient.Patch(ctx, mlflow, client.MergeFrom(before))).NotTo(HaveOccurred())

		result, err := reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())
		Expect(result.RequeueAfter).To(Equal(migrationJobRequeueAfter))

		updatedMLflow := &mlflowv1.MLflow{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, updatedMLflow)).To(Succeed())
		Expect(updatedMLflow.Annotations).To(HaveKey(forceMigrateAnnotation))
		Expect(updatedMLflow.Status.Version).To(BeEmpty())
		migrationCondition := apimeta.FindStatusCondition(updatedMLflow.Status.Conditions, migrationConditionType)
		Expect(migrationCondition).NotTo(BeNil())
		Expect(migrationCondition.Reason).To(Equal(migrationReasonForceRestartRequested))
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

		pod := &corev1.Pod{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "migration-failure-pod",
				Namespace: namespace,
				Labels: map[string]string{
					"job-name": job.Name,
				},
			},
			Spec: corev1.PodSpec{
				Containers: []corev1.Container{{
					Name:  migrationJobContainerName,
					Image: "test",
				}},
			},
		}
		Expect(k8sClient.Create(ctx, pod)).To(Succeed())
		pod.Status.ContainerStatuses = []corev1.ContainerStatus{{
			Name: migrationJobContainerName,
			State: corev1.ContainerState{
				Terminated: &corev1.ContainerStateTerminated{
					ExitCode: migrationScriptExitCodeUnsupportedBackend,
				},
			},
		}}
		Expect(k8sClient.Status().Update(ctx, pod)).To(Succeed())

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
		Expect(condition.Message).To(Equal("operator-managed migration only supports SQL backend store URIs. After fixing the issue, add the mlflow.opendatahub.io/force-migrate annotation to the MLflow resource to force a rerun."))
		migrationCondition := apimeta.FindStatusCondition(updatedMLflow.Status.Conditions, migrationConditionType)
		Expect(migrationCondition).NotTo(BeNil())
		Expect(migrationCondition.Status).To(Equal(metav1.ConditionFalse))
		Expect(migrationCondition.ObservedGeneration).To(Equal(updatedMLflow.Generation))
		Expect(migrationCondition.Reason).To(Equal("MigrationFailed"))
	})

	It("falls back to the Job condition message when no migration pod status is available", func() {
		ctx := context.Background()
		namespace := "migration-failure-fallback"
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
			{
				Type:   batchv1.JobFailureTarget,
				Status: corev1.ConditionTrue,
			},
			{
				Type:    batchv1.JobFailed,
				Status:  corev1.ConditionTrue,
				Message: "job condition failure message",
			},
		}
		Expect(k8sClient.Status().Update(ctx, job)).To(Succeed())

		_, err = reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())

		updatedMLflow := &mlflowv1.MLflow{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, updatedMLflow)).To(Succeed())
		condition := apimeta.FindStatusCondition(updatedMLflow.Status.Conditions, "Available")
		Expect(condition).NotTo(BeNil())
		Expect(condition.Reason).To(Equal("MigrationRetrying"))
		Expect(condition.Message).To(ContainSubstring("job condition failure message"))
		Expect(condition.Message).To(ContainSubstring(forceMigrateAnnotation))
		migrationCondition := apimeta.FindStatusCondition(updatedMLflow.Status.Conditions, migrationConditionType)
		Expect(migrationCondition).NotTo(BeNil())
		Expect(migrationCondition.Status).To(Equal(metav1.ConditionUnknown))
		Expect(migrationCondition.ObservedGeneration).To(Equal(updatedMLflow.Generation))
		Expect(migrationCondition.Reason).To(Equal("MigrationRetrying"))
	})

	It("retries a generic failed migration Job after backoff and recreates it", func() {
		ctx := context.Background()
		namespace := "migration-retryable"
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

		past := metav1.NewTime(time.Now().Add(-2 * time.Minute))
		job.Status.Failed = 1
		job.Status.StartTime = &past
		job.Status.Conditions = []batchv1.JobCondition{
			{Type: batchv1.JobFailureTarget, Status: corev1.ConditionTrue, LastTransitionTime: past},
			{Type: batchv1.JobFailed, Status: corev1.ConditionTrue, LastTransitionTime: past, Message: "connection refused"},
		}
		Expect(k8sClient.Status().Update(ctx, job)).To(Succeed())

		pod := &corev1.Pod{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "migration-retryable-pod",
				Namespace: namespace,
				Labels: map[string]string{
					"job-name": job.Name,
				},
			},
			Spec: corev1.PodSpec{
				Containers: []corev1.Container{{
					Name:  migrationJobContainerName,
					Image: "test",
				}},
			},
		}
		Expect(k8sClient.Create(ctx, pod)).To(Succeed())
		pod.Status.ContainerStatuses = []corev1.ContainerStatus{{
			Name: migrationJobContainerName,
			State: corev1.ContainerState{
				Terminated: &corev1.ContainerStateTerminated{
					ExitCode: migrationScriptExitCodeRetryableFailure,
					Message:  "connection refused",
				},
			},
		}}
		Expect(k8sClient.Status().Update(ctx, pod)).To(Succeed())

		result, err := reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())
		Expect(result.RequeueAfter).To(Equal(migrationRetryDeleteDelay))

		updatedMLflow := &mlflowv1.MLflow{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, updatedMLflow)).To(Succeed())
		migrationCondition := apimeta.FindStatusCondition(updatedMLflow.Status.Conditions, migrationConditionType)
		Expect(migrationCondition).NotTo(BeNil())
		Expect(migrationCondition.Status).To(Equal(metav1.ConditionUnknown))
		Expect(migrationCondition.Reason).To(Equal("MigrationRetrying"))
		Expect(migrationCondition.Message).To(ContainSubstring("migration failed due to a retryable database or migration error"))
		Expect(migrationCondition.Message).To(ContainSubstring(forceMigrateAnnotation))

		Eventually(func() bool {
			job := &batchv1.Job{}
			err := k8sClient.Get(ctx, jobKey, job)
			return errors.IsNotFound(err) || job.GetDeletionTimestamp() != nil
		}, 5*time.Second, 100*time.Millisecond).Should(BeTrue())
		backgroundDelete := metav1.DeletePropagationBackground
		Expect(k8sClient.Delete(ctx, pod, client.PropagationPolicy(backgroundDelete))).To(Succeed())
		_ = k8sClient.Delete(ctx, &batchv1.Job{ObjectMeta: metav1.ObjectMeta{Name: jobKey.Name, Namespace: jobKey.Namespace}}, client.PropagationPolicy(backgroundDelete))
		Eventually(func() bool {
			return errors.IsNotFound(k8sClient.Get(ctx, jobKey, &batchv1.Job{}))
		}, 5*time.Second, 100*time.Millisecond).Should(BeTrue())

		_, err = reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())
		Expect(k8sClient.Get(ctx, jobKey, &batchv1.Job{})).To(Succeed())
	})

	It("does not recreate a terminally failed migration Job after the Job is removed, even when the desired generation changes, until force-migrate is requested", func() {
		ctx := context.Background()
		namespace := "migration-failure-terminal"
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

		pod := &corev1.Pod{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "migration-failure-terminal-pod",
				Namespace: namespace,
				Labels: map[string]string{
					"job-name": job.Name,
				},
			},
			Spec: corev1.PodSpec{
				Containers: []corev1.Container{{
					Name:  migrationJobContainerName,
					Image: "test",
				}},
			},
		}
		Expect(k8sClient.Create(ctx, pod)).To(Succeed())
		pod.Status.ContainerStatuses = []corev1.ContainerStatus{{
			Name: migrationJobContainerName,
			State: corev1.ContainerState{
				Terminated: &corev1.ContainerStateTerminated{
					ExitCode: migrationScriptExitCodeUnsupportedBackend,
				},
			},
		}}
		Expect(k8sClient.Status().Update(ctx, pod)).To(Succeed())

		_, err = reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())

		backgroundDelete := metav1.DeletePropagationBackground
		Expect(k8sClient.Delete(ctx, job, client.PropagationPolicy(backgroundDelete))).To(Succeed())
		Eventually(func() bool {
			return errors.IsNotFound(k8sClient.Get(ctx, jobKey, &batchv1.Job{}))
		}, 5*time.Second, 100*time.Millisecond).Should(BeTrue())

		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, mlflow)).To(Succeed())
		before := mlflow.DeepCopy()
		mlflow.Spec.Env = append(mlflow.Spec.Env, corev1.EnvVar{Name: "EXAMPLE_GENERATION_BUMP", Value: "true"})
		Expect(k8sClient.Patch(ctx, mlflow, client.MergeFrom(before))).To(Succeed())

		_, err = reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())
		Expect(errors.IsNotFound(k8sClient.Get(ctx, jobKey, &batchv1.Job{}))).To(BeTrue())

		updatedMLflow := &mlflowv1.MLflow{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, updatedMLflow)).To(Succeed())
		migrationCondition := apimeta.FindStatusCondition(updatedMLflow.Status.Conditions, migrationConditionType)
		Expect(migrationCondition).NotTo(BeNil())
		Expect(migrationCondition.Status).To(Equal(metav1.ConditionFalse))
		Expect(migrationCondition.ObservedGeneration).To(Equal(updatedMLflow.Generation))
		Expect(migrationCondition.Reason).To(Equal("MigrationFailed"))
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

	It("waits for all MLflow replicas to disappear before creating the migration Job", func() {
		ctx := context.Background()
		namespace := "migration-quiesce"
		Expect(k8sClient.Create(ctx, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: namespace}})).To(Succeed())
		DeferCleanup(func() {
			_ = k8sClient.Delete(ctx, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: namespace}})
			_ = k8sClient.Delete(ctx, &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: resourceName}})
		})

		mlflow := newMLflow()
		Expect(k8sClient.Create(ctx, mlflow)).To(Succeed())
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, mlflow)).To(Succeed())

		reconciler := newReconciler(namespace)
		renderer := NewHelmRenderer("../../charts/mlflow")
		objects, err := renderer.RenderChart(mlflow, namespace, RenderOptions{})
		Expect(err).NotTo(HaveOccurred())
		Expect(reconciler.applyRenderedObjects(ctx, mlflow, objects)).To(Succeed())

		deployment := &appsv1.Deployment{}
		deploymentKey := types.NamespacedName{Name: ResourceName, Namespace: namespace}
		Expect(k8sClient.Get(ctx, deploymentKey, deployment)).To(Succeed())
		deployment.Status.Replicas = 1
		deployment.Status.ReadyReplicas = 0
		Expect(k8sClient.Status().Update(ctx, deployment)).To(Succeed())

		result, err := reconciler.Reconcile(ctx, reconcile.Request{NamespacedName: types.NamespacedName{Name: resourceName}})
		Expect(err).NotTo(HaveOccurred())
		Expect(result.RequeueAfter).To(Equal(migrationJobRequeueAfter))

		jobKey := types.NamespacedName{Name: migrationJobName(mlflow), Namespace: namespace}
		err = k8sClient.Get(ctx, jobKey, &batchv1.Job{})
		Expect(errors.IsNotFound(err)).To(BeTrue())

		updatedMLflow := &mlflowv1.MLflow{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: resourceName}, updatedMLflow)).To(Succeed())
		condition := apimeta.FindStatusCondition(updatedMLflow.Status.Conditions, "Available")
		Expect(condition).NotTo(BeNil())
		Expect(condition.Reason).To(Equal("MigrationScalingDown"))
		Expect(condition.Message).To(ContainSubstring("1 replicas remain"))
	})
})

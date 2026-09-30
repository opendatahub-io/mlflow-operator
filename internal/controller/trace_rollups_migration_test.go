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
	"testing"
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
)

func TestTraceRollupsMigrationRendering(t *testing.T) {
	for _, name := range []string{"mlflow", "scale"} {
		t.Run(name, func(t *testing.T) {
			instance := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: name},
				Spec:       mlflowv1.MLflowSpec{BackendStoreURI: ptr("postgresql://host/db")},
			}
			objects, err := NewHelmRenderer("../../charts/mlflow").RenderChart(instance, "apps", RenderOptions{}, nil)
			if err != nil {
				t.Fatal(err)
			}
			suffix := getResourceSuffix(name)
			original := findObject(objects, "CronJob", "mlflow-trace-rollups"+suffix)
			if original == nil {
				t.Fatal("SQL backend must render the rollup CronJob")
			}
			label, _, err := unstructured.NestedString(original.Object, "spec", "jobTemplate", "metadata", "labels", traceRollupsInstanceLabel)
			if err != nil || label != ResourceName+suffix {
				t.Fatalf("Job instance label = %q, %v; want %q", label, err, ResourceName+suffix)
			}
			objects = append(objects, &unstructured.Unstructured{Object: map[string]interface{}{
				"apiVersion": "batch/v1", "kind": "CronJob",
				"metadata": map[string]interface{}{"name": "mlflow-gc" + suffix, "namespace": "apps"},
				"spec":     map[string]interface{}{"suspend": false},
			}})
			scaled := scaledDownObjects(objects, ResourceName+suffix)
			rollups := findObject(scaled, "CronJob", original.GetName())
			suspended, _, err := unstructured.NestedBool(rollups.Object, "spec", "suspend")
			if err != nil || !suspended {
				t.Fatalf("migration must suspend rollups: %v, %v", suspended, err)
			}
			suspended, found, err := unstructured.NestedBool(original.Object, "spec", "suspend")
			if err != nil || !found || suspended {
				t.Fatalf("normal rendering must explicitly resume rollups and remain unmodified: %v, %v, %v", suspended, found, err)
			}
			gc := findObject(scaled, "CronJob", "mlflow-gc"+suffix)
			suspended, _, err = unstructured.NestedBool(gc.Object, "spec", "suspend")
			if err != nil || suspended {
				t.Fatalf("rollup suspension must not change another maintenance CronJob: %v, %v", suspended, err)
			}
		})
	}
}

func TestTraceRollupsMigrationAndCleanup(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := batchv1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	instance := &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: "mlflow", UID: types.UID("instance")}}
	cronJob := &batchv1.CronJob{ObjectMeta: metav1.ObjectMeta{
		Name: "mlflow-trace-rollups", Namespace: "apps", UID: types.UID("cronjob"),
		OwnerReferences: []metav1.OwnerReference{*metav1.NewControllerRef(instance, mlflowv1.GroupVersion.WithKind("MLflow"))},
	}}
	job := &batchv1.Job{ObjectMeta: metav1.ObjectMeta{
		Name: "rollup-manual", Namespace: "apps", Labels: map[string]string{traceRollupsInstanceLabel: "mlflow"},
	}}
	kubeClient := fake.NewClientBuilder().WithScheme(scheme).WithStatusSubresource(job).WithObjects(cronJob, job).Build()
	reconciler := &MLflowReconciler{Client: kubeClient, APIReader: kubeClient}
	ctx := context.Background()
	ready, err := reconciler.quiesceTraceRollups(ctx, instance, "apps")
	if err != nil || ready {
		t.Fatalf("first quiesce = %v, %v; want suspended and waiting", ready, err)
	}
	if err := kubeClient.Get(ctx, client.ObjectKeyFromObject(cronJob), cronJob); err != nil {
		t.Fatal(err)
	}
	if cronJob.Spec.Suspend == nil || !*cronJob.Spec.Suspend {
		t.Fatal("CronJob not suspended")
	}
	ready, err = reconciler.quiesceTraceRollups(ctx, instance, "apps")
	if err != nil || ready {
		t.Fatalf("pending Job must block migration: %v, %v", ready, err)
	}
	if err := kubeClient.Get(ctx, client.ObjectKeyFromObject(job), job); err != nil {
		t.Fatal(err)
	}
	job.Status.Succeeded = 1
	job.Status.Conditions = []batchv1.JobCondition{{Type: batchv1.JobComplete, Status: corev1.ConditionTrue}}
	if err := kubeClient.Status().Update(ctx, job); err != nil {
		t.Fatal(err)
	}
	ready, err = reconciler.quiesceTraceRollups(ctx, instance, "apps")
	if err != nil || !ready {
		t.Fatalf("finished Job must allow migration: %v, %v", ready, err)
	}
	if err := reconciler.cleanupTraceRollups(ctx, instance, "apps"); err != nil {
		t.Fatal(err)
	}
	if err := kubeClient.Get(ctx, client.ObjectKeyFromObject(cronJob), cronJob); !errors.IsNotFound(err) {
		t.Fatalf("disabled CronJob must be deleted: %v", err)
	}
}

func TestTraceRollupsMigrationJobIdentification(t *testing.T) {
	for _, tc := range []struct {
		name           string
		label          string
		ownerUID       types.UID
		status         batchv1.JobStatus
		missingCronJob bool
		unownedCronJob bool
		disabled       bool
		otherNamespace bool
		wantReady      bool
	}{
		{name: "pending labeled manual Job", label: "mlflow-scale"},
		{name: "active labeled manual Job", label: "mlflow-scale", status: batchv1.JobStatus{Active: 1}},
		{name: "succeeded counter before termination", label: "mlflow-scale", status: batchv1.JobStatus{Succeeded: 1}},
		{name: "complete Job", label: "mlflow-scale", status: batchv1.JobStatus{Conditions: []batchv1.JobCondition{{Type: batchv1.JobComplete, Status: corev1.ConditionTrue}}}, wantReady: true},
		{name: "failed Job", label: "mlflow-scale", status: batchv1.JobStatus{Conditions: []batchv1.JobCondition{{Type: batchv1.JobFailed, Status: corev1.ConditionTrue}}}, wantReady: true},
		{name: "failure condition not terminal", label: "mlflow-scale", status: batchv1.JobStatus{Conditions: []batchv1.JobCondition{{Type: batchv1.JobFailed, Status: corev1.ConditionFalse}}}},
		{name: "legacy unlabeled scheduled Job", ownerUID: types.UID("cronjob")},
		{name: "legacy Job during opt out", ownerUID: types.UID("cronjob"), disabled: true},
		{name: "Job from another CronJob", ownerUID: types.UID("another-cronjob"), wantReady: true},
		{name: "Job from unowned CronJob", ownerUID: types.UID("cronjob"), unownedCronJob: true, wantReady: true},
		{name: "Job from another instance", label: "mlflow-other", wantReady: true},
		{name: "Job in another namespace", label: "mlflow-scale", otherNamespace: true, wantReady: true},
		{name: "labeled Job without CronJob", label: "mlflow-scale", missingCronJob: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			scheme := runtime.NewScheme()
			if err := batchv1.AddToScheme(scheme); err != nil {
				t.Fatal(err)
			}
			instance := &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: "scale", UID: types.UID("instance")}}
			if tc.disabled {
				instance.Spec.TraceRollups = &mlflowv1.TraceRollupsSpec{Enabled: ptr(false)}
			}
			cronJob := &batchv1.CronJob{
				ObjectMeta: metav1.ObjectMeta{
					Name: "mlflow-trace-rollups-scale", Namespace: "apps", UID: types.UID("cronjob"),
					OwnerReferences: []metav1.OwnerReference{*metav1.NewControllerRef(instance, mlflowv1.GroupVersion.WithKind("MLflow"))},
				},
				Spec: batchv1.CronJobSpec{Suspend: ptr(true)},
			}
			if tc.unownedCronJob {
				cronJob.OwnerReferences = nil
				cronJob.Spec.Suspend = ptr(false)
			}
			job := &batchv1.Job{ObjectMeta: metav1.ObjectMeta{Name: "rollup-pass", Namespace: "apps"}, Status: tc.status}
			if tc.label != "" {
				job.Labels = map[string]string{traceRollupsInstanceLabel: tc.label}
			}
			if tc.ownerUID != "" {
				job.OwnerReferences = []metav1.OwnerReference{{APIVersion: "batch/v1", Kind: "CronJob", Name: cronJob.Name, UID: tc.ownerUID, Controller: ptr(true)}}
			}
			if tc.otherNamespace {
				job.Namespace = "other"
			}
			objects := []client.Object{job}
			if !tc.missingCronJob {
				objects = append(objects, cronJob)
			}
			kubeClient := fake.NewClientBuilder().WithScheme(scheme).WithObjects(objects...).Build()
			reconciler := &MLflowReconciler{Client: kubeClient, APIReader: kubeClient}
			ready, err := reconciler.quiesceTraceRollups(context.Background(), instance, "apps")
			if err != nil || ready != tc.wantReady {
				t.Fatalf("quiesce = %v, %v; want ready %v", ready, err, tc.wantReady)
			}
			if tc.unownedCronJob {
				if err := kubeClient.Get(context.Background(), client.ObjectKeyFromObject(cronJob), cronJob); err != nil {
					t.Fatal(err)
				}
				if *cronJob.Spec.Suspend {
					t.Fatal("must not suspend an unowned CronJob")
				}
			}
		})
	}
}

func TestTraceRollupsWaitPreservesMigrationFailure(t *testing.T) {
	scheme := runtime.NewScheme()
	for _, add := range []func(*runtime.Scheme) error{batchv1.AddToScheme, mlflowv1.AddToScheme} {
		if err := add(scheme); err != nil {
			t.Fatal(err)
		}
	}
	instance := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow", UID: types.UID("instance"), Generation: 2},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr("postgresql://host/db"),
			Migration:       &mlflowv1.MLflowMigrationConfig{Mode: mlflowv1.MLflowMigrateAlways},
		},
		Status: mlflowv1.MLflowStatus{
			Version: SupportedMLflowVersion,
			Conditions: []metav1.Condition{{
				Type: migrationConditionType, Status: metav1.ConditionFalse, ObservedGeneration: 1,
				Reason: migrationReasonFailed, Message: "terminal migration failure requires force-migrate",
			}},
		},
	}
	job := &batchv1.Job{ObjectMeta: metav1.ObjectMeta{
		Name: "rollup-manual", Namespace: "apps", Labels: map[string]string{traceRollupsInstanceLabel: "mlflow"},
	}}
	kubeClient := fake.NewClientBuilder().WithScheme(scheme).WithStatusSubresource(instance).WithObjects(instance, job).Build()
	reconciler := &MLflowReconciler{Client: kubeClient, APIReader: kubeClient, Scheme: scheme}
	objects, err := NewHelmRenderer("../../charts/mlflow").RenderChart(instance, "apps", RenderOptions{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	result, handled, err := reconciler.handleMigration(context.Background(), instance, "apps", objects)
	if err != nil || !handled || result.RequeueAfter != 5*time.Second {
		t.Fatalf("migration wait = %+v, %v, %v", result, handled, err)
	}
	if failure := latestTerminalMigrationFailureCondition(instance); failure == nil || failure.ObservedGeneration != 1 {
		t.Fatal("waiting for rollups must preserve the previous generation's terminal failure")
	}
}

var _ = Describe("Trace rollup migration coordination", func() {
	DescribeTable("waits for legacy Jobs before migration and restores scheduling state", func(enabled bool, namespace string) {
		ctx := context.Background()
		Expect(k8sClient.Create(ctx, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: namespace}})).To(Succeed())
		DeferCleanup(func() {
			_ = k8sClient.Delete(ctx, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: namespace}})
			_ = k8sClient.Delete(ctx, &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: "mlflow"}})
		})
		instance := &mlflowv1.MLflow{
			ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
			Spec: mlflowv1.MLflowSpec{
				BackendStoreURI: ptr("postgresql://host/db"),
				ServeArtifacts:  ptr(true),
				TraceRollups:    &mlflowv1.TraceRollupsSpec{Enabled: ptr(enabled)},
				Migration:       &mlflowv1.MLflowMigrationConfig{Mode: mlflowv1.MLflowMigrateAlways},
			},
		}
		Expect(k8sClient.Create(ctx, instance)).To(Succeed())
		instance.Status.Version = SupportedMLflowVersion
		Expect(k8sClient.Status().Update(ctx, instance)).To(Succeed())
		cronJob := &batchv1.CronJob{
			ObjectMeta: metav1.ObjectMeta{
				Name: "mlflow-trace-rollups", Namespace: namespace,
				OwnerReferences: []metav1.OwnerReference{*metav1.NewControllerRef(instance, mlflowv1.GroupVersion.WithKind("MLflow"))},
			},
			Spec: batchv1.CronJobSpec{
				Schedule: "0 0 29 2 *",
				JobTemplate: batchv1.JobTemplateSpec{Spec: batchv1.JobSpec{Template: corev1.PodTemplateSpec{Spec: corev1.PodSpec{
					RestartPolicy: corev1.RestartPolicyNever,
					Containers:    []corev1.Container{{Name: "rollups", Image: controllerTestMLflowImage}},
				}}}},
			},
		}
		Expect(k8sClient.Create(ctx, cronJob)).To(Succeed())
		rollupJob := &batchv1.Job{
			ObjectMeta: metav1.ObjectMeta{
				Name: "legacy-rollup-pass", Namespace: namespace,
				OwnerReferences: []metav1.OwnerReference{*metav1.NewControllerRef(cronJob, batchv1.SchemeGroupVersion.WithKind("CronJob"))},
			},
			Spec: *cronJob.Spec.JobTemplate.Spec.DeepCopy(),
		}
		Expect(k8sClient.Create(ctx, rollupJob)).To(Succeed())
		reconciler := &MLflowReconciler{
			Client: k8sClient, APIReader: k8sClient, Scheme: k8sClient.Scheme(), Namespace: namespace,
			ChartPath: "../../charts/mlflow", GCRBACWatchCache: mustNewGCRBACWatchCache(),
		}
		request := reconcile.Request{NamespacedName: types.NamespacedName{Name: instance.Name}}
		migrationJob := &batchv1.Job{}
		migrationKey := types.NamespacedName{Name: migrationJobName(instance), Namespace: namespace}
		for range 2 {
			result, err := reconciler.Reconcile(ctx, request)
			Expect(err).NotTo(HaveOccurred())
			Expect(result.RequeueAfter).To(Equal(5 * time.Second))
			Expect(k8sClient.Get(ctx, client.ObjectKeyFromObject(cronJob), cronJob)).To(Succeed())
			Expect(cronJob.Spec.Suspend).To(Equal(ptr(true)))
			Expect(errors.IsNotFound(k8sClient.Get(ctx, migrationKey, migrationJob))).To(BeTrue())
		}

		markFinished := func(job *batchv1.Job) {
			now := metav1.Now()
			job.Status.Succeeded = 1
			job.Status.StartTime = &now
			job.Status.CompletionTime = &now
			job.Status.Conditions = []batchv1.JobCondition{
				{Type: batchv1.JobSuccessCriteriaMet, Status: corev1.ConditionTrue},
				{Type: batchv1.JobComplete, Status: corev1.ConditionTrue},
			}
			Expect(k8sClient.Status().Update(ctx, job)).To(Succeed())
		}
		markFinished(rollupJob)
		_, err := reconciler.Reconcile(ctx, request)
		Expect(err).NotTo(HaveOccurred())
		Expect(k8sClient.Get(ctx, migrationKey, migrationJob)).To(Succeed())
		err = k8sClient.Get(ctx, client.ObjectKeyFromObject(cronJob), cronJob)
		if enabled {
			Expect(err).NotTo(HaveOccurred())
			Expect(cronJob.Spec.Suspend).To(Equal(ptr(true)))
		} else {
			Expect(errors.IsNotFound(err)).To(BeTrue())
		}
		markFinished(migrationJob)

		_, err = reconciler.Reconcile(ctx, request)
		Expect(err).NotTo(HaveOccurred())
		deployment := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: ResourceName, Namespace: namespace}, deployment)).To(Succeed())
		Expect(deployment.Spec.Replicas).To(Equal(ptr(int32(1))))
		deployment.Status.Replicas = 1
		deployment.Status.ReadyReplicas = 1
		Expect(k8sClient.Status().Update(ctx, deployment)).To(Succeed())
		for range 2 {
			_, err = reconciler.Reconcile(ctx, request)
			Expect(err).NotTo(HaveOccurred())
		}
		err = k8sClient.Get(ctx, client.ObjectKeyFromObject(cronJob), cronJob)
		if enabled {
			Expect(err).NotTo(HaveOccurred())
			Expect(cronJob.Spec.Suspend).To(Equal(ptr(false)))
			Expect(cronJob.Spec.JobTemplate.Labels).To(HaveKeyWithValue(traceRollupsInstanceLabel, "mlflow"))
		} else {
			Expect(errors.IsNotFound(err)).To(BeTrue())
		}
	},
		Entry("enabled scheduling resumes after migration", true, "rollups-migration-enabled"),
		Entry("opt out waits before deleting the legacy CronJob", false, "rollups-migration-disabled"),
	)
})

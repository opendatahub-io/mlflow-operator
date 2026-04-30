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
	_ "embed"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

const (
	// SupportedMLflowVersion is the MLflow version this operator expects to migrate to.
	SupportedMLflowVersion    = "3.10.1"
	forceMigrateAnnotation    = "mlflow.opendatahub.io/force-migrate"
	migrationJobContainerName = "db-migrate"
	migrationJobLabelKey      = "mlflow.opendatahub.io/migration-job"
	migrationJobInstanceLabel = "mlflow.opendatahub.io/migration-instance"
	migrationJobRequeueAfter  = 5 * time.Second
)

const migrationJobCommand = `exec python3.12 -c "$MIGRATION_PYTHON_SCRIPT"`

var versionKeyPattern = regexp.MustCompile(`[^a-zA-Z0-9]+`)

//go:embed assets/mlflow_db_migrate.py
var migrationPythonScript string

func hasForceMigrateAnnotation(mlflow *mlflowv1.MLflow) bool {
	if mlflow.Annotations == nil {
		return false
	}
	_, ok := mlflow.Annotations[forceMigrateAnnotation]
	return ok
}

func migrationRequested(mlflow *mlflowv1.MLflow) bool {
	if hasForceMigrateAnnotation(mlflow) {
		return true
	}

	if mlflow.Spec.Migrate == mlflowv1.MLflowMigrateAlways {
		return true
	}

	return mlflow.Status.Version == "" || mlflow.Status.Version != SupportedMLflowVersion
}

func migrationJobName(mlflow *mlflowv1.MLflow) string {
	versionKey := strings.TrimPrefix(strings.ToLower(versionKeyPattern.ReplaceAllString(SupportedMLflowVersion, "")), "v")
	if versionKey == "" {
		versionKey = "unknown"
	}

	suffix := fmt.Sprintf("-mg-%s-g%d", versionKey, mlflow.Generation)
	base := ResourceName + getResourceSuffix(mlflow.Name)
	if len(base) > 63-len(suffix) {
		base = base[:63-len(suffix)]
	}
	return base + suffix
}

func renderedDeployment(objects []*unstructured.Unstructured, name, namespace string) (*appsv1.Deployment, error) {
	for _, obj := range objects {
		if obj.GetKind() != "Deployment" || obj.GetName() != name || obj.GetNamespace() != namespace {
			continue
		}
		deployment := &appsv1.Deployment{}
		if err := runtime.DefaultUnstructuredConverter.FromUnstructured(obj.Object, deployment); err != nil {
			return nil, fmt.Errorf("convert rendered Deployment %s/%s: %w", namespace, name, err)
		}
		return deployment, nil
	}
	return nil, fmt.Errorf("rendered Deployment %s/%s not found", namespace, name)
}

func scaledDownObjects(objects []*unstructured.Unstructured, deploymentName string) []*unstructured.Unstructured {
	scaled := make([]*unstructured.Unstructured, 0, len(objects))
	for _, obj := range objects {
		copyObj := obj.DeepCopy()
		if copyObj.GetKind() == "Deployment" && copyObj.GetName() == deploymentName {
			if err := unstructured.SetNestedField(copyObj.Object, int64(0), "spec", "replicas"); err != nil {
				logf.Log.Error(err, "Failed to set Deployment replicas to zero in rendered object", "name", copyObj.GetName(), "namespace", copyObj.GetNamespace())
			}
		}
		scaled = append(scaled, copyObj)
	}
	return scaled
}

func isJobSuccessful(job *batchv1.Job) bool {
	return job.Status.Succeeded > 0
}

func isJobFailed(job *batchv1.Job) bool {
	if job.Status.Failed > 0 {
		return true
	}
	for _, condition := range job.Status.Conditions {
		if condition.Type == batchv1.JobFailed && condition.Status == corev1.ConditionTrue {
			return true
		}
	}
	return false
}

func isJobFinished(job *batchv1.Job) bool {
	return isJobSuccessful(job) || isJobFailed(job)
}

func jobFailureMessage(job *batchv1.Job) string {
	for _, condition := range job.Status.Conditions {
		if condition.Type == batchv1.JobFailed && condition.Status == corev1.ConditionTrue {
			if condition.Message != "" {
				return condition.Message
			}
			if condition.Reason != "" {
				return condition.Reason
			}
		}
	}
	if job.Status.Failed > 0 {
		return fmt.Sprintf("migration Job failed after %d attempt(s)", job.Status.Failed)
	}
	return "migration Job failed"
}

func setMigrationProgress(mlflow *mlflowv1.MLflow, reason, message string) {
	meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
		Type:    "Available",
		Status:  metav1.ConditionFalse,
		Reason:  reason,
		Message: message,
	})
	meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
		Type:    "Progressing",
		Status:  metav1.ConditionTrue,
		Reason:  reason,
		Message: message,
	})
}

func setMigrationFailure(mlflow *mlflowv1.MLflow, reason, message string) {
	meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
		Type:    "Available",
		Status:  metav1.ConditionFalse,
		Reason:  reason,
		Message: message,
	})
	meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
		Type:    "Progressing",
		Status:  metav1.ConditionFalse,
		Reason:  reason,
		Message: message,
	})
}

func (r *MLflowReconciler) clearForceMigrateAnnotation(ctx context.Context, mlflow *mlflowv1.MLflow) error {
	if !hasForceMigrateAnnotation(mlflow) {
		return nil
	}

	patchBytes, err := json.Marshal(map[string]any{
		"metadata": map[string]any{
			"annotations": map[string]any{
				forceMigrateAnnotation: nil,
			},
		},
	})
	if err != nil {
		return fmt.Errorf("marshal force-migrate annotation clear patch: %w", err)
	}

	return r.Patch(
		ctx,
		&mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: mlflow.Name, Namespace: mlflow.Namespace}},
		client.RawPatch(types.MergePatchType, patchBytes),
	)
}

func (r *MLflowReconciler) markMigrationSuccessful(ctx context.Context, mlflow *mlflowv1.MLflow) error {
	if err := r.clearForceMigrateAnnotation(ctx, mlflow); err != nil {
		return err
	}

	if mlflow.Annotations != nil {
		delete(mlflow.Annotations, forceMigrateAnnotation)
		if len(mlflow.Annotations) == 0 {
			mlflow.Annotations = nil
		}
	}

	if mlflow.Status.Version == SupportedMLflowVersion {
		return nil
	}

	mlflow.Status.Version = SupportedMLflowVersion
	return r.updateStatus(ctx, mlflow)
}

func buildMigrationJobFromDeployment(mlflow *mlflowv1.MLflow, deployment *appsv1.Deployment, namespace string) (*batchv1.Job, error) {
	mainContainer := findContainer(deployment.Spec.Template.Spec.Containers, "mlflow")
	if mainContainer == nil {
		return nil, fmt.Errorf("rendered Deployment %s/%s does not have an mlflow container", namespace, deployment.Name)
	}

	podSpec := deployment.Spec.Template.Spec.DeepCopy()
	jobContainer := mainContainer.DeepCopy()
	jobContainer.Name = migrationJobContainerName
	jobContainer.Command = []string{"/bin/sh", "-ec"}
	jobContainer.Args = []string{migrationJobCommand}
	jobContainer.Ports = nil
	jobContainer.LivenessProbe = nil
	jobContainer.ReadinessProbe = nil
	jobContainer.StartupProbe = nil
	jobContainer.Lifecycle = nil
	jobContainer.VolumeMounts = filterVolumeMounts(jobContainer.VolumeMounts)
	jobContainer.Env = append(jobContainer.Env, corev1.EnvVar{
		Name:  "MIGRATION_PYTHON_SCRIPT",
		Value: migrationPythonScript,
	})

	podSpec.Containers = []corev1.Container{*jobContainer}
	podSpec.InitContainers = filterMigrationInitContainers(podSpec.InitContainers)
	podSpec.Volumes = filterVolumes(podSpec.Volumes, usedVolumeNames(*podSpec))
	podSpec.RestartPolicy = corev1.RestartPolicyNever
	podSpec.TerminationGracePeriodSeconds = nil

	backoffLimit := int32(3)
	job := &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{
			Name:      migrationJobName(mlflow),
			Namespace: namespace,
			Labels: map[string]string{
				"component":               "mlflow-migration",
				migrationJobLabelKey:      "true",
				migrationJobInstanceLabel: mlflow.Name,
			},
		},
		Spec: batchv1.JobSpec{
			BackoffLimit: &backoffLimit,
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels: map[string]string{
						"component":               "mlflow-migration",
						migrationJobLabelKey:      "true",
						migrationJobInstanceLabel: mlflow.Name,
					},
				},
				Spec: *podSpec,
			},
		},
	}
	return job, nil
}

func findContainer(containers []corev1.Container, name string) *corev1.Container {
	for i := range containers {
		if containers[i].Name == name {
			return &containers[i]
		}
	}
	if len(containers) == 0 {
		return nil
	}
	return &containers[0]
}

func filterMigrationInitContainers(initContainers []corev1.Container) []corev1.Container {
	filtered := make([]corev1.Container, 0, len(initContainers))
	for _, initContainer := range initContainers {
		if initContainer.Name == "combine-ca-bundles" {
			filtered = append(filtered, initContainer)
		}
	}
	return filtered
}

func filterVolumeMounts(volumeMounts []corev1.VolumeMount) []corev1.VolumeMount {
	filtered := make([]corev1.VolumeMount, 0, len(volumeMounts))
	for _, volumeMount := range volumeMounts {
		switch volumeMount.Name {
		case "tmp", "mlflow-storage", "combined-ca-bundle":
			filtered = append(filtered, volumeMount)
		}
	}
	return filtered
}

func usedVolumeNames(podSpec corev1.PodSpec) map[string]struct{} {
	used := map[string]struct{}{}
	for _, container := range append(append([]corev1.Container{}, podSpec.InitContainers...), podSpec.Containers...) {
		for _, volumeMount := range container.VolumeMounts {
			used[volumeMount.Name] = struct{}{}
		}
	}
	return used
}

func filterVolumes(volumes []corev1.Volume, used map[string]struct{}) []corev1.Volume {
	filtered := make([]corev1.Volume, 0, len(volumes))
	for _, volume := range volumes {
		if _, ok := used[volume.Name]; ok {
			filtered = append(filtered, volume)
		}
	}
	return filtered
}

func (r *MLflowReconciler) listMigrationJobs(ctx context.Context, mlflow *mlflowv1.MLflow, namespace string) ([]batchv1.Job, error) {
	jobList := &batchv1.JobList{}
	if err := r.List(
		ctx,
		jobList,
		client.InNamespace(namespace),
		client.MatchingLabels{
			migrationJobLabelKey:      "true",
			migrationJobInstanceLabel: mlflow.Name,
		},
	); err != nil {
		return nil, err
	}
	return jobList.Items, nil
}

func (r *MLflowReconciler) handleMigration(ctx context.Context, mlflow *mlflowv1.MLflow, namespace string, objects []*unstructured.Unstructured) (ctrl.Result, bool, error) {
	log := logf.FromContext(ctx)
	if !migrationRequested(mlflow) {
		return ctrl.Result{}, false, nil
	}

	deploymentName := ResourceName + getResourceSuffix(mlflow.Name)
	deployment, err := renderedDeployment(objects, deploymentName, namespace)
	if err != nil {
		return ctrl.Result{}, true, err
	}

	jobKey := types.NamespacedName{Name: migrationJobName(mlflow), Namespace: namespace}
	existingJob := &batchv1.Job{}
	jobErr := r.Get(ctx, jobKey, existingJob)
	if jobErr != nil && !errors.IsNotFound(jobErr) {
		return ctrl.Result{}, true, jobErr
	}

	if hasForceMigrateAnnotation(mlflow) && jobErr == nil && isJobFailed(existingJob) {
		if err := r.Delete(ctx, existingJob); err != nil && !errors.IsNotFound(err) {
			return ctrl.Result{}, true, err
		}
		setMigrationProgress(mlflow, "MigrationRestartRequested", "Deleted failed migration Job to honor the force-migrate annotation")
		if err := r.updateStatus(ctx, mlflow); err != nil {
			return ctrl.Result{}, true, err
		}
		return ctrl.Result{RequeueAfter: migrationJobRequeueAfter}, true, nil
	}

	if hasForceMigrateAnnotation(mlflow) && mlflow.Status.Version == SupportedMLflowVersion && jobErr == nil && isJobSuccessful(existingJob) {
		if err := r.Delete(ctx, existingJob); err != nil && !errors.IsNotFound(err) {
			return ctrl.Result{}, true, err
		}
		setMigrationProgress(mlflow, "MigrationRestartRequested", "Deleted completed migration Job to honor the force-migrate annotation")
		if err := r.updateStatus(ctx, mlflow); err != nil {
			return ctrl.Result{}, true, err
		}
		return ctrl.Result{RequeueAfter: migrationJobRequeueAfter}, true, nil
	}

	if jobErr == nil && isJobSuccessful(existingJob) {
		if err := r.markMigrationSuccessful(ctx, mlflow); err != nil {
			return ctrl.Result{}, true, err
		}
		log.Info("Migration Job already completed successfully", "job", jobKey.Name)
		return ctrl.Result{}, false, nil
	}

	if err := r.applyRenderedObjects(ctx, mlflow, scaledDownObjects(objects, deploymentName)); err != nil {
		return ctrl.Result{}, true, err
	}

	currentDeployment := &appsv1.Deployment{}
	deploymentErr := r.Get(ctx, types.NamespacedName{Name: deploymentName, Namespace: namespace}, currentDeployment)
	if deploymentErr != nil && !errors.IsNotFound(deploymentErr) {
		return ctrl.Result{}, true, deploymentErr
	}
	if deploymentErr == nil && currentDeployment.Status.ReadyReplicas > 0 {
		setMigrationProgress(
			mlflow,
			"MigrationScalingDown",
			fmt.Sprintf("Waiting for MLflow pods to quiesce before migration: %d ready replicas remain", currentDeployment.Status.ReadyReplicas),
		)
		if err := r.updateStatus(ctx, mlflow); err != nil {
			return ctrl.Result{}, true, err
		}
		return ctrl.Result{RequeueAfter: migrationJobRequeueAfter}, true, nil
	}

	if errors.IsNotFound(jobErr) {
		jobs, err := r.listMigrationJobs(ctx, mlflow, namespace)
		if err != nil {
			return ctrl.Result{}, true, err
		}
		for _, job := range jobs {
			if job.Name == jobKey.Name || isJobFinished(&job) {
				continue
			}
			setMigrationProgress(
				mlflow,
				"MigrationRunning",
				fmt.Sprintf("Waiting for migration Job %s from a previous desired generation to finish", job.Name),
			)
			if err := r.updateStatus(ctx, mlflow); err != nil {
				return ctrl.Result{}, true, err
			}
			return ctrl.Result{RequeueAfter: migrationJobRequeueAfter}, true, nil
		}
	}

	if jobErr == nil && isJobFailed(existingJob) {
		setMigrationFailure(mlflow, "MigrationFailed", jobFailureMessage(existingJob))
		if err := r.updateStatus(ctx, mlflow); err != nil {
			return ctrl.Result{}, true, err
		}
		return ctrl.Result{}, true, nil
	}

	if errors.IsNotFound(jobErr) {
		job, err := buildMigrationJobFromDeployment(mlflow, deployment, namespace)
		if err != nil {
			return ctrl.Result{}, true, err
		}
		if err := controllerutil.SetControllerReference(mlflow, job, r.Scheme); err != nil {
			return ctrl.Result{}, true, err
		}
		if err := r.Create(ctx, job); err != nil && !errors.IsAlreadyExists(err) {
			return ctrl.Result{}, true, err
		}
		setMigrationProgress(mlflow, "MigrationRunning", fmt.Sprintf("Created migration Job %s", job.Name))
		if err := r.updateStatus(ctx, mlflow); err != nil {
			return ctrl.Result{}, true, err
		}
		return ctrl.Result{RequeueAfter: migrationJobRequeueAfter}, true, nil
	}

	setMigrationProgress(mlflow, "MigrationRunning", fmt.Sprintf("Waiting for migration Job %s to finish", existingJob.Name))
	if err := r.updateStatus(ctx, mlflow); err != nil {
		return ctrl.Result{}, true, err
	}
	return ctrl.Result{RequeueAfter: migrationJobRequeueAfter}, true, nil
}

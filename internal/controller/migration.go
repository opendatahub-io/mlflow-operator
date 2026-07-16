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

	"github.com/Masterminds/semver/v3"
	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/equality"
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
	forceMigrateAnnotation            = "mlflow.opendatahub.io/force-migrate"
	migrationConditionType            = "Migration"
	migrationJobContainerName         = "db-migrate"
	MigrationJobLabelKey              = "mlflow.opendatahub.io/migration-job"
	migrationJobInstanceLabel         = "mlflow.opendatahub.io/migration-instance"
	supportedVersionEnvName           = "SUPPORTED_MLFLOW_VERSION"
	readReplicaBackendStoreURIEnvName = "MLFLOW_READ_REPLICA_BACKEND_STORE_URI"
	migrationJobRequeueAfter          = 5 * time.Minute
	migrationRetryDelay               = 2 * time.Minute
	migrationRetryDeleteDelay         = 2 * time.Second
	migrationJobCommand               = `exec python3.12 -c "$MIGRATION_PYTHON_SCRIPT"`
	migrationJobBackoffLimit          = int32(3)
	migrationJobTTLSeconds            = int32(24 * 60 * 60) // 24 hours

	migrationScriptExitCodeVersionMismatch     = 10
	migrationScriptExitCodeUnsupportedBackend  = 11
	migrationScriptExitCodeUnsupportedRegistry = 12
	migrationScriptExitCodeRevisionMismatch    = 13
	migrationScriptExitCodeRevisionResolution  = 14
	migrationScriptExitCodeRetryableFailure    = 15

	migrationReasonSucceeded             = "MigrationSucceeded"
	migrationReasonFailed                = "MigrationFailed"
	migrationReasonRunning               = "MigrationRunning"
	migrationReasonRetrying              = "MigrationRetrying"
	migrationReasonScalingDown           = "MigrationScalingDown"
	migrationReasonRestartRequested      = "MigrationRestartRequested"
	migrationReasonForceRunning          = "ForceMigrationRunning"
	migrationReasonForceScalingDown      = "ForceMigrationScalingDown"
	migrationReasonForceRestartRequested = "ForceMigrationRestartRequested"
)

// versionKeyPattern strips non-alphanumerics so the supported MLflow version can
// be embedded safely in a DNS label for migration Job names.
var versionKeyPattern = regexp.MustCompile(`[^a-zA-Z0-9]+`)

// SupportedMLflowVersion is injected via -ldflags from config/component_metadata.yaml.
var SupportedMLflowVersion string

//go:embed assets/mlflow_db_migrate.py
var migrationPythonScript string

func hasForceMigrateAnnotation(mlflow *mlflowv1.MLflow) bool {
	if mlflow.Annotations == nil {
		return false
	}
	_, ok := mlflow.Annotations[forceMigrateAnnotation]
	return ok
}

func currentGenerationMigrationCondition(mlflow *mlflowv1.MLflow) *metav1.Condition {
	condition := meta.FindStatusCondition(mlflow.Status.Conditions, migrationConditionType)
	if condition == nil || condition.ObservedGeneration != mlflow.Generation {
		return nil
	}
	return condition
}

func latestMigrationCondition(mlflow *mlflowv1.MLflow) *metav1.Condition {
	return meta.FindStatusCondition(mlflow.Status.Conditions, migrationConditionType)
}

func latestTerminalMigrationFailureCondition(mlflow *mlflowv1.MLflow) *metav1.Condition {
	condition := latestMigrationCondition(mlflow)
	if condition == nil || condition.Status != metav1.ConditionFalse || condition.Reason != migrationReasonFailed {
		return nil
	}
	return condition
}

func migrationConditionWasForceTriggered(condition *metav1.Condition) bool {
	if condition == nil {
		return false
	}
	switch condition.Reason {
	case migrationReasonForceRunning, migrationReasonForceScalingDown, migrationReasonForceRestartRequested:
		return true
	default:
		return false
	}
}

func migrationRestartRequested(condition *metav1.Condition) bool {
	return condition != nil && condition.Reason == migrationReasonForceRestartRequested
}

func migrationProgressReason(trigger migrationTrigger, defaultReason string) string {
	if trigger.kind != "force-rerun" {
		return defaultReason
	}
	switch defaultReason {
	case migrationReasonRunning:
		return migrationReasonForceRunning
	case migrationReasonScalingDown:
		return migrationReasonForceScalingDown
	case migrationReasonRestartRequested:
		return migrationReasonForceRestartRequested
	default:
		return defaultReason
	}
}

func migrationRequested(mlflow *mlflowv1.MLflow) bool {
	if hasForceMigrateAnnotation(mlflow) {
		return true
	}

	if mlflow.Status.Version != SupportedMLflowVersion {
		return true
	}

	if condition := currentGenerationMigrationCondition(mlflow); condition != nil {
		return condition.Status != metav1.ConditionTrue
	}

	if migrationMode(mlflow) == mlflowv1.MLflowMigrateAlways {
		return true
	}

	return false
}

func migrationMode(mlflow *mlflowv1.MLflow) mlflowv1.MLflowMigrateMode {
	if mlflow.Spec.Migration == nil || mlflow.Spec.Migration.Mode == "" {
		return mlflowv1.MLflowMigrateAutomatic
	}
	return mlflow.Spec.Migration.Mode
}

func supportedVersionEarlierThanStatusVersion(mlflow *mlflowv1.MLflow) bool {
	if mlflow.Status.Version == "" || SupportedMLflowVersion == "" {
		return false
	}

	supportedVersion, err := semver.NewVersion(strings.TrimPrefix(SupportedMLflowVersion, "v"))
	if err != nil {
		return false
	}
	statusVersion, err := semver.NewVersion(strings.TrimPrefix(mlflow.Status.Version, "v"))
	if err != nil {
		return false
	}
	return supportedVersion.LessThan(statusVersion)
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

func migrationFailureMessageForExitCode(exitCode int32) (string, bool) {
	switch exitCode {
	case migrationScriptExitCodeVersionMismatch:
		return fmt.Sprintf("migration image reports an unexpected MLflow version; expected %s", SupportedMLflowVersion), true
	case migrationScriptExitCodeUnsupportedBackend:
		return "operator-managed migration only supports SQL backend store URIs", true
	case migrationScriptExitCodeUnsupportedRegistry:
		return "operator-managed migration only supports SQL registry store URIs", true
	case migrationScriptExitCodeRevisionMismatch:
		return "migration completed but the resulting schema revision does not match the image's Alembic head", true
	case migrationScriptExitCodeRevisionResolution:
		return "migration failed because Alembic could not resolve the schema revision graph", true
	case migrationScriptExitCodeRetryableFailure:
		return "migration failed due to a retryable database or migration error", true
	default:
		return "", false
	}
}

type migrationFailureDetails struct {
	exitCode    int32
	hasExitCode bool
	message     string
}

func isTerminalMigrationExitCode(exitCode int32) bool {
	switch exitCode {
	case migrationScriptExitCodeVersionMismatch,
		migrationScriptExitCodeUnsupportedBackend,
		migrationScriptExitCodeUnsupportedRegistry,
		migrationScriptExitCodeRevisionMismatch,
		migrationScriptExitCodeRevisionResolution:
		return true
	default:
		return false
	}
}

// jobFailedCondition returns the terminal Failed condition for a Job, if one is
// present, so the controller can anchor retry timing to the Job's transition to
// a failed state.
func jobFailedCondition(job *batchv1.Job) *batchv1.JobCondition {
	for i := range job.Status.Conditions {
		condition := &job.Status.Conditions[i]
		if condition.Type == batchv1.JobFailed && condition.Status == corev1.ConditionTrue {
			return condition
		}
	}
	return nil
}

// retryableMigrationMessage appends the next retry time and the manual
// force-rerun hint to a retryable migration failure message.
func retryableMigrationMessage(message string, retryAt time.Time) string {
	return fmt.Sprintf(
		"%s Retrying automatically after %s. If you want to rerun immediately after fixing the issue, add the %s annotation.",
		message,
		retryAt.Format(time.RFC3339),
		forceMigrateAnnotation,
	)
}

// terminalMigrationMessage appends the manual force-rerun instruction to a
// terminal migration failure message.
func terminalMigrationMessage(message string) string {
	return fmt.Sprintf(
		"%s. After fixing the issue, add the %s annotation to the MLflow resource to force a rerun.",
		strings.TrimSuffix(message, "."),
		forceMigrateAnnotation,
	)
}

type migrationTrigger struct {
	kind   string
	detail string
}

func describeMigrationTrigger(mlflow *mlflowv1.MLflow) migrationTrigger {
	switch {
	case hasForceMigrateAnnotation(mlflow):
		return migrationTrigger{
			kind:   "force-rerun",
			detail: fmt.Sprintf("the %s annotation is present", forceMigrateAnnotation),
		}
	case migrationMode(mlflow) == mlflowv1.MLflowMigrateAlways:
		return migrationTrigger{
			kind:   "desired-generation",
			detail: fmt.Sprintf("spec.migration.mode=Always and desired generation %d has not completed migration yet", mlflow.Generation),
		}
	case mlflow.Status.Version == "":
		return migrationTrigger{
			kind:   "bootstrap",
			detail: "status.version is empty during bootstrap",
		}
	default:
		return migrationTrigger{
			kind:   "version-upgrade",
			detail: fmt.Sprintf("status.version=%q does not match supported version %s", mlflow.Status.Version, SupportedMLflowVersion),
		}
	}
}

func (t migrationTrigger) message(action string) string {
	return fmt.Sprintf("%s because %s", action, t.detail)
}

func latestMigrationJobPod(pods []corev1.Pod) *corev1.Pod {
	var latest *corev1.Pod
	for i := range pods {
		pod := &pods[i]
		if latest == nil {
			latest = pod
			continue
		}
		if pod.CreationTimestamp.After(latest.CreationTimestamp.Time) {
			latest = pod
		}
	}
	return latest
}

func terminatedMigrationJobContainerStatus(pod *corev1.Pod) *corev1.ContainerStatus {
	for i := range pod.Status.ContainerStatuses {
		status := &pod.Status.ContainerStatuses[i]
		if status.Name == migrationJobContainerName && status.State.Terminated != nil {
			return status
		}
	}
	for i := range pod.Status.ContainerStatuses {
		status := &pod.Status.ContainerStatuses[i]
		if status.State.Terminated != nil {
			return status
		}
	}
	return nil
}

// jobFailureDetails inspects the failed migration Job's pod termination state
// first, then falls back to Job conditions/status. For script-owned failures,
// the controller relies on explicit exit codes instead of parsing termination
// log text.
func (r *MLflowReconciler) jobFailureDetails(ctx context.Context, job *batchv1.Job) (migrationFailureDetails, error) {
	podList := &corev1.PodList{}
	if err := r.List(
		ctx,
		podList,
		client.InNamespace(job.Namespace),
		client.MatchingLabels{"job-name": job.Name},
	); err != nil {
		return migrationFailureDetails{}, fmt.Errorf("list pods for migration Job %s/%s: %w", job.Namespace, job.Name, err)
	}
	if pod := latestMigrationJobPod(podList.Items); pod != nil {
		if status := terminatedMigrationJobContainerStatus(pod); status != nil {
			terminated := status.State.Terminated
			if message, ok := migrationFailureMessageForExitCode(terminated.ExitCode); ok {
				return migrationFailureDetails{exitCode: terminated.ExitCode, hasExitCode: true, message: message}, nil
			}
			if terminated.Reason != "" {
				return migrationFailureDetails{exitCode: terminated.ExitCode, hasExitCode: true, message: terminated.Reason}, nil
			}
			return migrationFailureDetails{
				exitCode:    terminated.ExitCode,
				hasExitCode: true,
				message:     fmt.Sprintf("migration Job container exited with code %d", terminated.ExitCode),
			}, nil
		}
	}

	for _, condition := range job.Status.Conditions {
		if condition.Type == batchv1.JobFailed && condition.Status == corev1.ConditionTrue {
			if condition.Message != "" {
				return migrationFailureDetails{message: condition.Message}, nil
			}
			if condition.Reason != "" {
				return migrationFailureDetails{message: condition.Reason}, nil
			}
		}
	}
	if job.Status.Failed > 0 {
		return migrationFailureDetails{message: fmt.Sprintf("migration Job failed after %d attempt(s)", job.Status.Failed)}, nil
	}
	return migrationFailureDetails{message: "migration Job failed"}, nil
}

func (r *MLflowReconciler) recordMigrationProgress(ctx context.Context, mlflow *mlflowv1.MLflow, reason, message string) error {
	logf.FromContext(ctx).Info("Migration progress", "reason", reason, "message", message, "mlflow", mlflow.Name, "generation", mlflow.Generation)
	before := mlflow.DeepCopy()
	mlflow.SetMigrationProgress(reason, message)
	if equality.Semantic.DeepEqual(before.Status, mlflow.Status) {
		return nil
	}
	return r.updateStatus(ctx, mlflow)
}

func (r *MLflowReconciler) recordMigrationFailure(ctx context.Context, mlflow *mlflowv1.MLflow, reason, message string) error {
	logf.FromContext(ctx).Info("Migration failure", "reason", reason, "message", message, "mlflow", mlflow.Name, "generation", mlflow.Generation)
	before := mlflow.DeepCopy()
	mlflow.SetMigrationFailure(reason, message)
	if equality.Semantic.DeepEqual(before.Status, mlflow.Status) {
		return nil
	}
	return r.updateStatus(ctx, mlflow)
}

func (r *MLflowReconciler) recordMigrationError(ctx context.Context, mlflow *mlflowv1.MLflow, reason, message string) error {
	logf.FromContext(ctx).Info("Migration error", "reason", reason, "message", message, "mlflow", mlflow.Name, "generation", mlflow.Generation)
	before := mlflow.DeepCopy()
	mlflow.SetMigrationError(reason, message)
	if equality.Semantic.DeepEqual(before.Status, mlflow.Status) {
		return nil
	}
	return r.updateStatus(ctx, mlflow)
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

	currentCondition := currentGenerationMigrationCondition(mlflow)
	if mlflow.Status.Version == SupportedMLflowVersion &&
		currentCondition != nil &&
		currentCondition.Status == metav1.ConditionTrue {
		return nil
	}

	mlflow.Status.Version = SupportedMLflowVersion
	meta.SetStatusCondition(&mlflow.Status.Conditions, metav1.Condition{
		Type:               migrationConditionType,
		Status:             metav1.ConditionTrue,
		ObservedGeneration: mlflow.Generation,
		Reason:             migrationReasonSucceeded,
		Message:            fmt.Sprintf("Migration for generation %d completed successfully", mlflow.Generation),
	})
	return r.updateStatus(ctx, mlflow)
}

func buildMigrationLabels(templateLabels map[string]string, mlflowName string) map[string]string {
	labels := make(map[string]string, len(templateLabels)+3)
	for key, value := range templateLabels {
		if key == "app" {
			continue
		}
		labels[key] = value
	}
	labels["component"] = "mlflow-migration"
	labels[MigrationJobLabelKey] = "true"
	labels[migrationJobInstanceLabel] = mlflowName
	return labels
}

// classifyMigrationFailure returns true when a failed migration should be
// treated as terminal rather than automatically retried by the operator.
func classifyMigrationFailure(details migrationFailureDetails) bool {
	return details.hasExitCode && isTerminalMigrationExitCode(details.exitCode)
}

func migrationJobTTLSecondsAfterFinished(mlflow *mlflowv1.MLflow) int32 {
	if mlflow.Spec.Migration != nil && mlflow.Spec.Migration.TTLSecondsAfterFinished != nil {
		return *mlflow.Spec.Migration.TTLSecondsAfterFinished
	}
	return migrationJobTTLSeconds
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
	jobContainer.Resources.Claims = nil
	jobContainer.Env = filterEnvVar(jobContainer.Env, readReplicaBackendStoreURIEnvName)
	jobContainer.Env = append(jobContainer.Env, corev1.EnvVar{
		Name:  readReplicaBackendStoreURIEnvName,
		Value: "",
	})
	jobContainer.Env = append(jobContainer.Env, corev1.EnvVar{
		Name:  "MIGRATION_PYTHON_SCRIPT",
		Value: migrationPythonScript,
	})
	jobContainer.Env = append(jobContainer.Env, corev1.EnvVar{
		Name:  supportedVersionEnvName,
		Value: SupportedMLflowVersion,
	})

	podSpec.Containers = []corev1.Container{*jobContainer}
	podSpec.InitContainers = filterMigrationInitContainers(podSpec.InitContainers)
	podSpec.ResourceClaims = nil
	podSpec.Volumes = filterVolumes(podSpec.Volumes, usedVolumeNames(*podSpec))
	podSpec.RestartPolicy = corev1.RestartPolicyNever
	podSpec.TerminationGracePeriodSeconds = nil

	backoffLimit := migrationJobBackoffLimit
	ttlSecondsAfterFinished := migrationJobTTLSecondsAfterFinished(mlflow)
	job := &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{
			Name:      migrationJobName(mlflow),
			Namespace: namespace,
			Labels:    buildMigrationLabels(deployment.Spec.Template.Labels, mlflow.Name),
		},
		Spec: batchv1.JobSpec{
			BackoffLimit:            &backoffLimit,
			TTLSecondsAfterFinished: &ttlSecondsAfterFinished,
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels: buildMigrationLabels(deployment.Spec.Template.Labels, mlflow.Name),
				},
				Spec: *podSpec,
			},
		},
	}
	return job, nil
}

func deploymentHasActiveReplicas(deployment *appsv1.Deployment) bool {
	return deployment.Status.Replicas > 0
}

func findContainer(containers []corev1.Container, name string) *corev1.Container {
	for i := range containers {
		if containers[i].Name == name {
			return &containers[i]
		}
	}
	return nil
}

func filterEnvVar(env []corev1.EnvVar, excludedName string) []corev1.EnvVar {
	filtered := make([]corev1.EnvVar, 0, len(env))
	for _, envVar := range env {
		if envVar.Name != excludedName {
			filtered = append(filtered, envVar)
		}
	}
	return filtered
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
			MigrationJobLabelKey:      "true",
			migrationJobInstanceLabel: mlflow.Name,
		},
	); err != nil {
		return nil, err
	}
	return jobList.Items, nil
}

func (r *MLflowReconciler) handleMigration(ctx context.Context, mlflow *mlflowv1.MLflow, namespace string, objects []*unstructured.Unstructured) (ctrl.Result, bool, error) {
	log := logf.FromContext(ctx)
	if supportedVersionEarlierThanStatusVersion(mlflow) {
		log.Info(
			"Recorded MLflow version is newer than this operator's supported version",
			"statusVersion", mlflow.Status.Version,
			"supportedVersion", SupportedMLflowVersion,
			"generation", mlflow.Generation,
		)
	}
	if !migrationRequested(mlflow) {
		return ctrl.Result{}, false, nil
	}
	trigger := describeMigrationTrigger(mlflow)
	currentMigrationCondition := currentGenerationMigrationCondition(mlflow)
	log.Info(
		"Migration requested",
		"trigger", trigger.kind,
		"detail", trigger.detail,
		"generation", mlflow.Generation,
		"statusVersion", mlflow.Status.Version,
	)

	deploymentName := ResourceName + getResourceSuffix(mlflow.Name)
	deployment, err := renderedDeployment(objects, deploymentName, namespace)
	if err != nil {
		return ctrl.Result{}, true, err
	}

	jobKey := types.NamespacedName{Name: migrationJobName(mlflow), Namespace: namespace}
	existingJob := &batchv1.Job{}
	jobErr := r.Get(ctx, jobKey, existingJob)
	jobNotFound := errors.IsNotFound(jobErr)
	jobExists := jobErr == nil
	if jobErr != nil && !jobNotFound {
		return ctrl.Result{}, true, jobErr
	}

	if jobExists && isJobFailed(existingJob) && !hasForceMigrateAnnotation(mlflow) {
		if err := r.applyRenderedObjects(ctx, mlflow, scaledDownObjects(objects, deploymentName)); err != nil {
			return ctrl.Result{}, true, err
		}

		failureDetails, err := r.jobFailureDetails(ctx, existingJob)
		if err != nil {
			return ctrl.Result{}, true, err
		}
		if classifyMigrationFailure(failureDetails) {
			if err := r.recordMigrationFailure(ctx, mlflow, migrationReasonFailed, terminalMigrationMessage(failureDetails.message)); err != nil {
				return ctrl.Result{}, true, err
			}
			return ctrl.Result{}, true, nil
		}

		retryAfter := migrationRetryDelay
		retryAt := time.Now().Add(retryAfter)
		// Prefer the terminal JobFailed transition time so the operator-level retry
		// delay is anchored to when Kubernetes marked the Job failed.
		if condition := jobFailedCondition(existingJob); condition != nil && !condition.LastTransitionTime.IsZero() {
			retryAt = condition.LastTransitionTime.Add(retryAfter)
		}
		if time.Now().Before(retryAt) {
			if err := r.recordMigrationProgress(ctx, mlflow, migrationProgressReason(trigger, migrationReasonRetrying), retryableMigrationMessage(failureDetails.message, retryAt)); err != nil {
				return ctrl.Result{}, true, err
			}
			return ctrl.Result{RequeueAfter: time.Until(retryAt)}, true, nil
		}

		// The Kubernetes Job backoffLimit has already been exhausted for this Job.
		// Delete it so the operator can start a fresh retryable attempt with the
		// same deterministic name after the operator-level delay above.
		if err := r.Delete(ctx, existingJob); err != nil && !errors.IsNotFound(err) {
			return ctrl.Result{}, true, err
		}
		if err := r.recordMigrationProgress(
			ctx,
			mlflow,
			migrationProgressReason(trigger, migrationReasonRetrying),
			retryableMigrationMessage(failureDetails.message, time.Now().Add(migrationRetryDeleteDelay)),
		); err != nil {
			return ctrl.Result{}, true, err
		}
		return ctrl.Result{RequeueAfter: migrationRetryDeleteDelay}, true, nil
	}

	if hasForceMigrateAnnotation(mlflow) && jobExists && isJobFailed(existingJob) {
		// Delete the job to keep job names deterministic and reuse the name
		if err := r.Delete(ctx, existingJob); err != nil && !errors.IsNotFound(err) {
			return ctrl.Result{}, true, err
		}
		if err := r.recordMigrationProgress(
			ctx,
			mlflow,
			migrationProgressReason(trigger, migrationReasonRestartRequested),
			trigger.message(
				fmt.Sprintf("Deleted failed migration Job %s so the force-migrate annotation can create a fresh Job with the same generated name", existingJob.Name),
			),
		); err != nil {
			return ctrl.Result{}, true, err
		}

		// Requeue after 5 minutes incase the watches on the job don't trigger a reconcile
		return ctrl.Result{RequeueAfter: migrationJobRequeueAfter}, true, nil
	}

	// Force-migrate should rerun even when a successful Job already exists for the
	// current generation, but only once per force request. This covers both an
	// admin-triggered rerun after a prior success and the stale-status window
	// where the Job succeeded before the controller recorded force-specific progress.
	if hasForceMigrateAnnotation(mlflow) &&
		jobExists &&
		isJobSuccessful(existingJob) &&
		!migrationConditionWasForceTriggered(currentMigrationCondition) {
		if err := r.Delete(ctx, existingJob); err != nil && !errors.IsNotFound(err) {
			return ctrl.Result{}, true, err
		}
		if err := r.recordMigrationProgress(
			ctx,
			mlflow,
			migrationProgressReason(trigger, migrationReasonRestartRequested),
			trigger.message(
				fmt.Sprintf("Deleted completed migration Job %s so the force-migrate annotation can create a fresh Job with the same generated name", existingJob.Name),
			),
		); err != nil {
			return ctrl.Result{}, true, err
		}
		return ctrl.Result{RequeueAfter: migrationJobRequeueAfter}, true, nil
	}

	// Once force-specific restart progress has been recorded, keep waiting for the
	// previously successful Job to actually disappear before allowing the generic
	// successful-Job path to mark the migration complete.
	if hasForceMigrateAnnotation(mlflow) &&
		jobExists &&
		isJobSuccessful(existingJob) &&
		migrationRestartRequested(currentMigrationCondition) {
		if err := r.Delete(ctx, existingJob); err != nil && !errors.IsNotFound(err) {
			return ctrl.Result{}, true, err
		}
		return ctrl.Result{RequeueAfter: migrationJobRequeueAfter}, true, nil
	}

	if jobExists && isJobSuccessful(existingJob) {
		// Let the caller re-apply the full rendered objects so the Deployment is
		// restored from the migration-time scaled-down state. The controller only
		// records migration success after the post-migration rollout is ready.
		log.Info("Migration Job already completed successfully", "job", jobKey.Name, "trigger", trigger.kind)
		return ctrl.Result{}, false, nil
	}

	// Any path that reaches here either has no finished migration Job yet or is
	// intentionally holding the Deployment at zero replicas while migration is
	// pending or failed.
	if err := r.applyRenderedObjects(ctx, mlflow, scaledDownObjects(objects, deploymentName)); err != nil {
		return ctrl.Result{}, true, err
	}

	// Keep terminal migration failures sticky across later desired generations
	// until an admin explicitly requests a rerun. When a new generation arrives,
	// re-record the failure so the status reflects the current generation.
	if latestFailure := latestTerminalMigrationFailureCondition(mlflow); latestFailure != nil && !hasForceMigrateAnnotation(mlflow) {
		if latestFailure.ObservedGeneration != mlflow.Generation {
			if err := r.recordMigrationFailure(ctx, mlflow, latestFailure.Reason, latestFailure.Message); err != nil {
				return ctrl.Result{}, true, err
			}
		}
		return ctrl.Result{}, true, nil
	}

	currentDeployment := &appsv1.Deployment{}
	deploymentErr := r.Get(ctx, types.NamespacedName{Name: deploymentName, Namespace: namespace}, currentDeployment)
	deploymentNotFound := errors.IsNotFound(deploymentErr)
	deploymentExists := deploymentErr == nil
	if deploymentErr != nil && !deploymentNotFound {
		return ctrl.Result{}, true, deploymentErr
	}
	if deploymentExists && deploymentHasActiveReplicas(currentDeployment) {
		if err := r.recordMigrationProgress(
			ctx,
			mlflow,
			migrationProgressReason(trigger, migrationReasonScalingDown),
			trigger.message(
				fmt.Sprintf("Waiting for MLflow pods to quiesce before migration: %d replicas remain", currentDeployment.Status.Replicas),
			),
		); err != nil {
			return ctrl.Result{}, true, err
		}
		return ctrl.Result{RequeueAfter: migrationJobRequeueAfter}, true, nil
	}

	if jobNotFound {
		jobs, err := r.listMigrationJobs(ctx, mlflow, namespace)
		if err != nil {
			return ctrl.Result{}, true, err
		}
		for _, job := range jobs {
			if job.Name == jobKey.Name || isJobFinished(&job) {
				continue
			}
			if err := r.recordMigrationProgress(
				ctx,
				mlflow,
				migrationProgressReason(trigger, migrationReasonRunning),
				trigger.message(
					fmt.Sprintf("Waiting for migration Job %s from a previous desired generation to finish", job.Name),
				),
			); err != nil {
				return ctrl.Result{}, true, err
			}
			return ctrl.Result{RequeueAfter: migrationJobRequeueAfter}, true, nil
		}
	}

	if jobNotFound {
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
		if err := r.recordMigrationProgress(
			ctx,
			mlflow,
			migrationProgressReason(trigger, migrationReasonRunning),
			trigger.message(fmt.Sprintf("Created migration Job %s", job.Name)),
		); err != nil {
			return ctrl.Result{}, true, err
		}
		return ctrl.Result{RequeueAfter: migrationJobRequeueAfter}, true, nil
	}

	if err := r.recordMigrationProgress(
		ctx,
		mlflow,
		migrationProgressReason(trigger, migrationReasonRunning),
		trigger.message(fmt.Sprintf("Waiting for migration Job %s to finish", existingJob.Name)),
	); err != nil {
		return ctrl.Result{}, true, err
	}
	return ctrl.Result{RequeueAfter: migrationJobRequeueAfter}, true, nil
}

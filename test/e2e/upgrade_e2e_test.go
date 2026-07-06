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

package e2e

import (
	"context"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"

	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/fields"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/watch"
	"k8s.io/client-go/kubernetes"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	controllerpkg "github.com/opendatahub-io/mlflow-operator/internal/controller"
)

const (
	// The pinned ODH release 1.1 seed image reports MLflow 3.10.1+rhaiv.3,
	// while the upgrade flow normalizes status.version to upstream 3.10.1.
	upgradeSeedImage = "quay.io/opendatahub/mlflow@" +
		"sha256:ad51bbd7f770491da88dc1db3b3c84f7471d25c48026ecb385180b63b18f4c64"
	upgradeSeedVersion       = "3.10.1"
	upgradeVerifyJob         = "mlflow-upgrade-verify"
	upgradePVCName           = "mlflow-pvc"
	controllerDeploymentName = "mlflow-operator-controller-manager"
	controllerContainerName  = "manager"
	mlflowPythonPath         = "/usr/bin/python3.12"
)

var (
	upgradeSeedImageFlag = flag.String(
		"upgrade-seed-image",
		upgradeSeedImage,
		"MLflow image used to seed the upgrade test database",
	)
)

var _ = Describe("Upgrade", Ordered, Label("upgrade"), func() {
	var (
		ctx               context.Context
		k8sClient         client.Client
		clientset         kubernetes.Interface
		controllerPodName string
	)

	BeforeAll(func() {
		ctx = context.Background()
		k8sClient, clientset = newUpgradeClients()

		ns := &corev1.Namespace{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: namespace}, ns)).To(Succeed(),
			"Upgrade tests expect the workflow to pre-provision the namespace and operator")

		By("finding the controller-manager pod")
		controllerPodName = waitForControllerPodReady(ctx, k8sClient)
	})

	AfterEach(func() {
		if !CurrentSpecReport().Failed() || controllerPodName == "" {
			return
		}

		By("Fetching controller manager pod logs")
		controllerLogs, err := getPodLogs(ctx, clientset, controllerPodName)
		if err == nil {
			_, _ = fmt.Fprintf(GinkgoWriter, "Controller logs:\n%s", controllerLogs)
		}
	})

	It("should upgrade a running MLflow 3.10.1 deployment to the current supported version", func() {
		seedImage := *upgradeSeedImageFlag
		if seedImage == "" {
			seedImage = upgradeSeedImage
		}
		runtimeImage := os.Getenv("MLFLOW_RUNTIME_IMAGE")
		Expect(runtimeImage).NotTo(BeEmpty(), "upgrade tests require MLFLOW_RUNTIME_IMAGE")
		By("verifying the seeded 3.10.1 deployment is running before the upgrade starts")
		mlflow := &mlflowv1.MLflow{}
		Eventually(func(g Gomega) {
			err := k8sClient.Get(ctx, types.NamespacedName{Name: "mlflow"}, mlflow)
			g.Expect(err).NotTo(HaveOccurred())
			g.Expect(mlflow.Status.Version).To(Equal(upgradeSeedVersion))
			g.Expect(mlflow.Spec.Image).NotTo(BeNil())
			g.Expect(mlflow.Spec.Image.Image).NotTo(BeNil())
			g.Expect(*mlflow.Spec.Image.Image).To(Equal(seedImage))
		}, 2*time.Minute, time.Second).Should(Succeed())

		deployment := &appsv1.Deployment{}
		Eventually(func(g Gomega) {
			err := k8sClient.Get(ctx, types.NamespacedName{Name: "mlflow", Namespace: namespace}, deployment)
			g.Expect(err).NotTo(HaveOccurred())
			g.Expect(deployment.Spec.Replicas).NotTo(BeNil())
			g.Expect(*deployment.Spec.Replicas).To(Equal(int32(1)))
			g.Expect(deployment.Status.AvailableReplicas).To(BeNumerically(">", 0))
			g.Expect(currentMLflowImage(deployment)).To(Equal(seedImage))
		}, 2*time.Minute, time.Second).Should(Succeed())

		By("scaling the seeded operator down before changing the MLflow CR or operator image")
		updateControllerDeployment(ctx, k8sClient, "", 0)
		waitForControllerPodsGone(ctx, k8sClient)

		watchCtx, cancelWatch := context.WithCancel(ctx)
		defer cancelWatch()
		scaledToZeroCh, scaledToZeroErrCh := watchDeploymentScaledToZero(watchCtx, clientset, "mlflow")
		upgradeStartedAt := time.Now()

		By("switching the MLflow CR pin from the seeded image to the current runtime image while the operator is stopped")
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "mlflow"}, mlflow)).To(Succeed())
		before := mlflow.DeepCopy()
		if mlflow.Spec.Image == nil {
			mlflow.Spec.Image = &mlflowv1.ImageConfig{}
		}
		mlflow.Spec.Image.Image = ptrTo(runtimeImage)
		Expect(k8sClient.Patch(ctx, mlflow, client.MergeFrom(before))).To(Succeed())

		By("upgrading the operator deployment to the current image and scaling it back up")
		updateControllerDeployment(ctx, k8sClient, projectImage, 1)
		controllerPodName = waitForControllerPodReady(ctx, k8sClient)

		var migrationJobName string
		Eventually(func(g Gomega) string {
			jobList := &batchv1.JobList{}
			err := k8sClient.List(
				ctx,
				jobList,
				client.InNamespace(namespace),
				client.MatchingLabels{"mlflow.opendatahub.io/migration-job": "true"},
			)
			g.Expect(err).NotTo(HaveOccurred())

			for _, job := range jobList.Items {
				if strings.Contains(job.Name, "-mg-") && job.CreationTimestamp.Time.After(upgradeStartedAt.Add(-time.Second)) {
					migrationJobName = job.Name
					return job.Name
				}
			}
			return ""
		}, 2*time.Minute, time.Second).ShouldNot(BeEmpty())

		By("observing the deployment scaled to zero while migration is in progress")
		Eventually(func() bool {
			select {
			case err := <-scaledToZeroErrCh:
				if err != nil {
					Fail(err.Error())
				}
				return false
			default:
			}

			select {
			case <-scaledToZeroCh:
				return true
			default:
				return false
			}
		}, 2*time.Minute, 250*time.Millisecond).Should(BeTrue())

		By("waiting for the migration Job to succeed")
		waitForJobSuccess(ctx, k8sClient, migrationJobName)

		By("verifying status.version is updated only after successful migration")
		Eventually(func(g Gomega) string {
			mlflow := &mlflowv1.MLflow{}
			err := k8sClient.Get(ctx, types.NamespacedName{Name: "mlflow"}, mlflow)
			g.Expect(err).NotTo(HaveOccurred())
			return mlflow.Status.Version
		}, 2*time.Minute, time.Second).Should(Equal(controllerpkg.SupportedMLflowVersion))

		By("waiting for the final MLflow deployment to become available again")
		waitForDeploymentAvailable(ctx, k8sClient, "mlflow")

		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "mlflow", Namespace: namespace}, deployment)).To(Succeed())
		Expect(deployment.Spec.Replicas).NotTo(BeNil())
		Expect(*deployment.Spec.Replicas).To(Equal(int32(1)))
		Expect(currentMLflowImage(deployment)).To(Equal(runtimeImage))

		By("verifying the metadata store reached the current Alembic head")
		schemaCheckLogs := runSchemaVerificationJob(ctx, k8sClient, clientset, currentMLflowImage(deployment), mlflow)
		Expect(schemaCheckLogs).To(ContainSubstring("latest="))
		Expect(schemaCheckLogs).To(ContainSubstring("backend-revision="))
	})
})

func newUpgradeClients() (client.Client, kubernetes.Interface) {
	cfg := ctrl.GetConfigOrDie()
	scheme := runtime.NewScheme()
	Expect(appsv1.AddToScheme(scheme)).To(Succeed())
	Expect(batchv1.AddToScheme(scheme)).To(Succeed())
	Expect(corev1.AddToScheme(scheme)).To(Succeed())
	Expect(mlflowv1.AddToScheme(scheme)).To(Succeed())

	k8sClient, err := client.New(cfg, client.Options{Scheme: scheme})
	Expect(err).NotTo(HaveOccurred())

	clientset, err := kubernetes.NewForConfig(cfg)
	Expect(err).NotTo(HaveOccurred())
	return k8sClient, clientset
}

func createOrReplace(ctx context.Context, k8sClient client.Client, obj client.Object) {
	err := k8sClient.Create(ctx, obj)
	if apierrors.IsAlreadyExists(err) {
		Expect(k8sClient.Delete(ctx, obj)).To(Succeed())
		Eventually(func() bool {
			current := obj.DeepCopyObject().(client.Object)
			err := k8sClient.Get(ctx, client.ObjectKeyFromObject(obj), current)
			return apierrors.IsNotFound(err)
		}, 30*time.Second, 250*time.Millisecond).Should(BeTrue())
		err = k8sClient.Create(ctx, obj)
	}
	Expect(err).NotTo(HaveOccurred())
}

func updateControllerDeployment(ctx context.Context, k8sClient client.Client, image string, replicas int32) {
	deployment := &appsv1.Deployment{}
	Expect(
		k8sClient.Get(
			ctx,
			types.NamespacedName{Name: controllerDeploymentName, Namespace: namespace},
			deployment,
		),
	).To(Succeed())

	before := deployment.DeepCopy()
	deployment.Spec.Replicas = ptrTo(replicas)
	if image != "" {
		Expect(setContainerImage(deployment, controllerContainerName, image)).To(Succeed())
	}
	Expect(k8sClient.Patch(ctx, deployment, client.MergeFrom(before))).To(Succeed())
}

func setContainerImage(deployment *appsv1.Deployment, containerName, image string) error {
	for i := range deployment.Spec.Template.Spec.Containers {
		if deployment.Spec.Template.Spec.Containers[i].Name == containerName {
			deployment.Spec.Template.Spec.Containers[i].Image = image
			return nil
		}
	}
	return fmt.Errorf(
		"deployment %s/%s does not contain container %q",
		deployment.Namespace,
		deployment.Name,
		containerName,
	)
}

func waitForControllerPodsGone(ctx context.Context, k8sClient client.Client) {
	Eventually(func(g Gomega) []string {
		podList := &corev1.PodList{}
		err := k8sClient.List(
			ctx,
			podList,
			client.InNamespace(namespace),
			client.MatchingLabels{"control-plane": "controller-manager"},
		)
		g.Expect(err).NotTo(HaveOccurred())
		return nonDeletingPodNames(podList.Items)
	}, 2*time.Minute, time.Second).Should(BeEmpty())
}

func waitForControllerPodReady(ctx context.Context, k8sClient client.Client) string {
	var podName string
	Eventually(func(g Gomega) {
		deployment := &appsv1.Deployment{}
		err := k8sClient.Get(ctx, types.NamespacedName{Name: controllerDeploymentName, Namespace: namespace}, deployment)
		g.Expect(err).NotTo(HaveOccurred())
		g.Expect(deployment.Status.AvailableReplicas).To(BeNumerically(">", 0))

		podList := &corev1.PodList{}
		err = k8sClient.List(
			ctx,
			podList,
			client.InNamespace(namespace),
			client.MatchingLabels{"control-plane": "controller-manager"},
		)
		g.Expect(err).NotTo(HaveOccurred())

		readyPods := make([]string, 0, len(podList.Items))
		for _, pod := range podList.Items {
			if pod.DeletionTimestamp != nil || !podReady(&pod) {
				continue
			}
			readyPods = append(readyPods, pod.Name)
		}
		g.Expect(readyPods).To(HaveLen(1))
		podName = readyPods[0]
	}, 2*time.Minute, time.Second).Should(Succeed())
	return podName
}

func nonDeletingPodNames(pods []corev1.Pod) []string {
	names := make([]string, 0, len(pods))
	for _, pod := range pods {
		if pod.DeletionTimestamp != nil {
			continue
		}
		names = append(names, pod.Name)
	}
	return names
}

func podReady(pod *corev1.Pod) bool {
	for _, condition := range pod.Status.Conditions {
		if condition.Type == corev1.PodReady && condition.Status == corev1.ConditionTrue {
			return true
		}
	}
	return false
}

func waitForDeploymentAvailable(ctx context.Context, k8sClient client.Client, name string) {
	Eventually(func(g Gomega) {
		deployment := &appsv1.Deployment{}
		err := k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: namespace}, deployment)
		g.Expect(err).NotTo(HaveOccurred())
		g.Expect(deployment.Status.AvailableReplicas).To(BeNumerically(">", 0))
	}, 5*time.Minute, time.Second).Should(Succeed())
}

func waitForJobSuccess(ctx context.Context, k8sClient client.Client, name string) {
	Eventually(func(g Gomega) {
		job := &batchv1.Job{}
		err := k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: namespace}, job)
		g.Expect(err).NotTo(HaveOccurred())

		details := fmt.Sprintf(
			"job %s status: succeeded=%d failed=%d active=%d conditions=%v",
			name,
			job.Status.Succeeded,
			job.Status.Failed,
			job.Status.Active,
			job.Status.Conditions,
		)
		if jobFailed(job) {
			clientset, clientsetErr := kubernetes.NewForConfig(ctrl.GetConfigOrDie())
			g.Expect(clientsetErr).NotTo(HaveOccurred())
			if logs, logErr := getJobLogs(ctx, clientset, name); logErr == nil && logs != "" {
				details = fmt.Sprintf("%s\njob logs:\n%s", details, logs)
			}
		}
		g.Expect(jobFailed(job)).To(BeFalse(), details)
		g.Expect(job.Status.Succeeded).To(BeNumerically(">", 0))
	}, 5*time.Minute, time.Second).Should(Succeed())
}

func jobFailed(job *batchv1.Job) bool {
	for _, condition := range job.Status.Conditions {
		if condition.Type == batchv1.JobFailed && condition.Status == corev1.ConditionTrue {
			return true
		}
	}
	return false
}

func watchDeploymentScaledToZero(
	ctx context.Context,
	clientset kubernetes.Interface,
	name string,
) (<-chan struct{}, <-chan error) {
	observed := make(chan struct{})
	errCh := make(chan error, 1)

	watcher, err := clientset.AppsV1().Deployments(namespace).Watch(ctx, metav1.ListOptions{
		FieldSelector: fields.OneTermEqualSelector("metadata.name", name).String(),
	})
	if err != nil {
		errCh <- fmt.Errorf("watch deployment %s: %w", name, err)
		return observed, errCh
	}

	go func() {
		defer watcher.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case event, ok := <-watcher.ResultChan():
				if !ok {
					errCh <- fmt.Errorf("deployment watch channel for %s closed unexpectedly", name)
					return
				}
				if event.Type == watch.Error {
					errCh <- fmt.Errorf("deployment watch for %s returned an error event", name)
					return
				}

				deployment, ok := event.Object.(*appsv1.Deployment)
				if !ok {
					continue
				}
				if deployment.Spec.Replicas != nil && *deployment.Spec.Replicas == 0 {
					close(observed)
					return
				}
			}
		}
	}()

	return observed, errCh
}

func getJobLogs(ctx context.Context, clientset kubernetes.Interface, jobName string) (string, error) {
	pods, err := clientset.CoreV1().Pods(namespace).List(ctx, metav1.ListOptions{
		LabelSelector: labels.SelectorFromSet(labels.Set{"job-name": jobName}).String(),
	})
	if err != nil {
		return "", err
	}
	if len(pods.Items) == 0 {
		return "", fmt.Errorf("no pods found for Job %s", jobName)
	}

	return getPodLogs(ctx, clientset, pods.Items[0].Name)
}

func getPodLogs(ctx context.Context, clientset kubernetes.Interface, podName string) (string, error) {
	stream, err := clientset.CoreV1().Pods(namespace).GetLogs(podName, &corev1.PodLogOptions{}).Stream(ctx)
	if err != nil {
		return "", err
	}
	defer func() {
		_ = stream.Close()
	}()

	data, err := io.ReadAll(stream)
	if err != nil {
		return "", err
	}
	return string(data), nil
}

func runSchemaVerificationJob(
	ctx context.Context,
	k8sClient client.Client,
	clientset kubernetes.Interface,
	image string,
	mlflow *mlflowv1.MLflow,
) string {
	job := schemaVerificationJob(image, mlflow)
	createOrReplace(ctx, k8sClient, job)
	waitForJobSuccess(ctx, k8sClient, job.Name)
	logs, err := getJobLogs(ctx, clientset, job.Name)
	Expect(err).NotTo(HaveOccurred())
	return logs
}

func schemaVerificationJob(image string, mlflow *mlflowv1.MLflow) *batchv1.Job {
	backoffLimit := int32(0)
	job := &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{Name: upgradeVerifyJob, Namespace: namespace},
		Spec: batchv1.JobSpec{
			BackoffLimit: &backoffLimit,
			Template: corev1.PodTemplateSpec{
				Spec: corev1.PodSpec{
					RestartPolicy: corev1.RestartPolicyNever,
					SecurityContext: &corev1.PodSecurityContext{
						RunAsNonRoot: ptrTo(true),
						RunAsUser:    ptrTo(int64(1001)),
						RunAsGroup:   ptrTo(int64(1001)),
						FSGroup:      ptrTo(int64(1001)),
						SeccompProfile: &corev1.SeccompProfile{
							Type: corev1.SeccompProfileTypeRuntimeDefault,
						},
					},
					Containers: []corev1.Container{{
						Name:    "verify-schema",
						Image:   image,
						Command: []string{mlflowPythonPath, "-c"},
						Args:    []string{schemaVerificationScript()},
						SecurityContext: &corev1.SecurityContext{
							AllowPrivilegeEscalation: ptrTo(false),
							ReadOnlyRootFilesystem:   ptrTo(false),
							Capabilities:             &corev1.Capabilities{Drop: []corev1.Capability{"ALL"}},
						},
					}},
				},
			},
		},
	}

	container := &job.Spec.Template.Spec.Containers[0]
	if env := uriEnvVar(
		"MLFLOW_BACKEND_STORE_URI",
		mlflow.Spec.BackendStoreURI,
		mlflow.Spec.BackendStoreURIFrom,
	); env != nil {
		container.Env = append(container.Env, *env)
	}
	if mlflow.Spec.Storage != nil {
		container.VolumeMounts = append(container.VolumeMounts, corev1.VolumeMount{
			Name:      "mlflow-storage",
			MountPath: "/mlflow",
		})
		job.Spec.Template.Spec.Volumes = append(job.Spec.Template.Spec.Volumes, corev1.Volume{
			Name: "mlflow-storage",
			VolumeSource: corev1.VolumeSource{
				PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{ClaimName: upgradePVCName},
			},
		})
	}

	return job
}

func schemaVerificationScript() string {
	return strings.TrimSpace(`
import os
import mlflow.store.db.utils as db_utils

backend_uri = os.environ["MLFLOW_BACKEND_STORE_URI"]
latest = db_utils._get_latest_schema_revision()
print(f"latest={latest}")
engine = db_utils.create_sqlalchemy_engine_with_retry(backend_uri)
revision = db_utils._get_schema_version(engine)
print(f"backend-revision={revision}")
if revision != latest:
    raise SystemExit(f"backend revision {revision!r} does not match latest {latest!r}")
`) + "\n"
}

func uriEnvVar(name string, value *string, valueFrom *corev1.SecretKeySelector) *corev1.EnvVar {
	if value != nil {
		return &corev1.EnvVar{Name: name, Value: *value}
	}
	if valueFrom != nil {
		return &corev1.EnvVar{
			Name: name,
			ValueFrom: &corev1.EnvVarSource{
				SecretKeyRef: valueFrom.DeepCopy(),
			},
		}
	}
	return nil
}

func currentMLflowImage(deployment *appsv1.Deployment) string {
	for _, container := range deployment.Spec.Template.Spec.Containers {
		if container.Name == "mlflow" {
			return container.Image
		}
	}
	if len(deployment.Spec.Template.Spec.Containers) == 0 {
		return ""
	}
	return deployment.Spec.Template.Spec.Containers[0].Image
}

func ptrTo[T any](value T) *T {
	return &value
}

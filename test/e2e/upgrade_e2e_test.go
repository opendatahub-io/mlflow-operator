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
	"k8s.io/apimachinery/pkg/api/resource"
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
	upgradeSeedJobName  = "mlflow-upgrade-seed"
	upgradePVCName      = "mlflow-pvc"
	postgresServiceName = "postgres-service"
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
		Eventually(func(g Gomega) {
			podList := &corev1.PodList{}
			err := k8sClient.List(
				ctx,
				podList,
				client.InNamespace(namespace),
				client.MatchingLabels{"control-plane": "controller-manager"},
			)
			g.Expect(err).NotTo(HaveOccurred())
			readyPods := make([]string, 0, len(podList.Items))
			for _, pod := range podList.Items {
				if pod.DeletionTimestamp != nil {
					continue
				}
				readyPods = append(readyPods, pod.Name)
			}
			g.Expect(readyPods).To(HaveLen(1))
			controllerPodName = readyPods[0]
		}, 2*time.Minute, time.Second).Should(Succeed())
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

	AfterAll(func() {
		By("cleaning up upgrade resources")
		cleanupUpgradeResources(ctx, k8sClient)
	})

	It("should upgrade a database seeded by MLflow 3.9.0", func() {
		seedImage := os.Getenv("MLFLOW_SEED_IMAGE")
		if seedImage == "" {
			seedImage = "localhost/mlflow-seed:3.9.0"
		}
		backendStore := os.Getenv("UPGRADE_BACKEND_STORE")
		if backendStore == "" {
			backendStore = "sqlite"
		}

		By("cleaning up any stale upgrade resources")
		cleanupUpgradeResources(ctx, k8sClient)

		By("creating the PVC shared by the seed Job and upgraded MLflow deployment")
		createOrReplace(ctx, k8sClient, &corev1.PersistentVolumeClaim{
			ObjectMeta: metav1.ObjectMeta{Name: upgradePVCName, Namespace: namespace},
			Spec: corev1.PersistentVolumeClaimSpec{
				AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
				Resources: corev1.VolumeResourceRequirements{
					Requests: corev1.ResourceList{"storage": resourceMustParse("2Gi")},
				},
			},
		})

		By("seeding the tracking and registry databases with MLflow 3.9.0")
		createOrReplace(ctx, k8sClient, seedJob(seedImage, backendStore))
		waitForJobSuccess(ctx, k8sClient, upgradeSeedJobName)

		seedLogs, err := getJobLogs(ctx, clientset, upgradeSeedJobName)
		Expect(err).NotTo(HaveOccurred())
		Expect(seedLogs).To(ContainSubstring("seed-version=3.9.0"))

		watchCtx, cancelWatch := context.WithCancel(ctx)
		defer cancelWatch()
		scaledToZeroCh, scaledToZeroErrCh := watchDeploymentScaledToZero(watchCtx, clientset, "mlflow")

		By("creating the MLflow resource that should trigger operator-managed migration")
		Expect(k8sClient.Create(ctx, mlflowForUpgrade(backendStore))).To(Succeed())

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
				if strings.Contains(job.Name, "-mg-") {
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
		}, 30*time.Second, 250*time.Millisecond).Should(BeTrue())

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

		deployment := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "mlflow", Namespace: namespace}, deployment)).To(Succeed())
		Expect(deployment.Spec.Replicas).NotTo(BeNil())
		Expect(*deployment.Spec.Replicas).To(Equal(int32(1)))
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

func cleanupUpgradeResources(ctx context.Context, k8sClient client.Client) {
	deleteIfExists(ctx, k8sClient, &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: "mlflow"}})

	jobList := &batchv1.JobList{}
	if err := k8sClient.List(
		ctx,
		jobList,
		client.InNamespace(namespace),
		client.MatchingLabels{"mlflow.opendatahub.io/migration-job": "true"},
	); err == nil {
		for i := range jobList.Items {
			deleteIfExists(ctx, k8sClient, &jobList.Items[i])
		}
	}

	deleteIfExists(ctx, k8sClient, &batchv1.Job{ObjectMeta: metav1.ObjectMeta{Name: upgradeSeedJobName, Namespace: namespace}})
	deleteIfExists(ctx, k8sClient, &appsv1.Deployment{ObjectMeta: metav1.ObjectMeta{Name: "mlflow", Namespace: namespace}})
	deleteIfExists(ctx, k8sClient, &corev1.Service{ObjectMeta: metav1.ObjectMeta{Name: "mlflow", Namespace: namespace}})
	deleteIfExists(ctx, k8sClient, &corev1.PersistentVolumeClaim{ObjectMeta: metav1.ObjectMeta{Name: upgradePVCName, Namespace: namespace}})
}

func deleteIfExists(ctx context.Context, k8sClient client.Client, obj client.Object) {
	err := k8sClient.Delete(ctx, obj)
	if apierrors.IsNotFound(err) {
		return
	}
	if err != nil {
		Expect(err).NotTo(HaveOccurred())
	}

	Eventually(func() bool {
		current := obj.DeepCopyObject().(client.Object)
		err := k8sClient.Get(ctx, client.ObjectKeyFromObject(obj), current)
		return apierrors.IsNotFound(err)
	}, 30*time.Second, 250*time.Millisecond).Should(BeTrue())
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
		g.Expect(jobFailed(job)).To(BeFalse())
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

func watchDeploymentScaledToZero(ctx context.Context, clientset kubernetes.Interface, name string) (<-chan struct{}, <-chan error) {
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
	defer stream.Close()

	data, err := io.ReadAll(stream)
	if err != nil {
		return "", err
	}
	return string(data), nil
}

func seedJob(seedImage, backendStore string) *batchv1.Job {
	backoffLimit := int32(0)
	job := &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{Name: upgradeSeedJobName, Namespace: namespace},
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
						Name:    "seed",
						Image:   seedImage,
						Command: []string{"python", "-c"},
						Args:    []string{seedScript(backendStore)},
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

	if backendStore == "postgres" {
		job.Spec.Template.Spec.Containers[0].Env = []corev1.EnvVar{
			{Name: "PG_ADMIN_URI", Value: postgresAdminURI()},
			{
				Name: "MLFLOW_BACKEND_STORE_URI",
				ValueFrom: &corev1.EnvVarSource{
					SecretKeyRef: &corev1.SecretKeySelector{
						LocalObjectReference: corev1.LocalObjectReference{Name: "mlflow-db-credentials"},
						Key:                  "backend-store-uri",
					},
				},
			},
			{
				Name: "MLFLOW_REGISTRY_STORE_URI",
				ValueFrom: &corev1.EnvVarSource{
					SecretKeyRef: &corev1.SecretKeySelector{
						LocalObjectReference: corev1.LocalObjectReference{Name: "mlflow-db-credentials"},
						Key:                  "registry-store-uri",
					},
				},
			},
		}
		return job
	}

	job.Spec.Template.Spec.Containers[0].VolumeMounts = []corev1.VolumeMount{{
		Name:      "mlflow-storage",
		MountPath: "/mlflow",
	}}
	job.Spec.Template.Spec.Volumes = []corev1.Volume{{
		Name: "mlflow-storage",
		VolumeSource: corev1.VolumeSource{
			PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{ClaimName: upgradePVCName},
		},
	}}
	return job
}

func seedScript(backendStore string) string {
	if backendStore == "postgres" {
		return strings.TrimSpace(`
import os
import mlflow.store.db.utils as db_utils
import psycopg2
from psycopg2 import sql
from mlflow.version import VERSION
from urllib.parse import urlparse

stores = [
    ("backend", os.environ["MLFLOW_BACKEND_STORE_URI"]),
]
if os.environ["MLFLOW_REGISTRY_STORE_URI"] != os.environ["MLFLOW_BACKEND_STORE_URI"]:
    stores.append(("registry", os.environ["MLFLOW_REGISTRY_STORE_URI"]))

with psycopg2.connect(os.environ["PG_ADMIN_URI"]) as conn:
    conn.autocommit = True
    with conn.cursor() as cur:
        created = set()
        for _, uri in stores:
            database = urlparse(uri).path.lstrip("/")
            if database in created:
                continue
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            created.add(database)

print(f"seed-version={VERSION}")
for name, uri in stores:
    engine = db_utils.create_sqlalchemy_engine_with_retry(uri)
    db_utils._initialize_tables(engine)
    print(f"{name}-revision={db_utils._get_schema_version(engine)}")
`) + "\n"
	}

	return strings.TrimSpace(`
import mlflow.store.db.utils as db_utils
from mlflow.version import VERSION

stores = [
    ("backend", "sqlite:////mlflow/tracking.db"),
    ("registry", "sqlite:////mlflow/registry.db"),
]
print(f"seed-version={VERSION}")
for name, uri in stores:
    engine = db_utils.create_sqlalchemy_engine_with_retry(uri)
    db_utils._initialize_tables(engine)
    print(f"{name}-revision={db_utils._get_schema_version(engine)}")
`) + "\n"
}

func mlflowForUpgrade(backendStore string) *mlflowv1.MLflow {
	replicas := int32(1)
	serveArtifacts := true
	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec: mlflowv1.MLflowSpec{
			Migrate:        mlflowv1.MLflowMigrateAutomatic,
			Replicas:       &replicas,
			ServeArtifacts: &serveArtifacts,
			Storage: &corev1.PersistentVolumeClaimSpec{
				AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
				Resources: corev1.VolumeResourceRequirements{
					Requests: corev1.ResourceList{"storage": resourceMustParse("2Gi")},
				},
			},
			ArtifactsDestination: ptrTo("file:///mlflow/artifacts"),
		},
	}

	if backendStore == "postgres" {
		mlflow.Spec.BackendStoreURIFrom = &corev1.SecretKeySelector{
			LocalObjectReference: corev1.LocalObjectReference{Name: "mlflow-db-credentials"},
			Key:                  "backend-store-uri",
		}
		mlflow.Spec.RegistryStoreURIFrom = &corev1.SecretKeySelector{
			LocalObjectReference: corev1.LocalObjectReference{Name: "mlflow-db-credentials"},
			Key:                  "registry-store-uri",
		}
		return mlflow
	}

	mlflow.Spec.BackendStoreURI = ptrTo("sqlite:////mlflow/tracking.db")
	mlflow.Spec.RegistryStoreURI = ptrTo("sqlite:////mlflow/registry.db")
	return mlflow
}

func postgresAdminURI() string {
	if uri := os.Getenv("UPGRADE_POSTGRES_ADMIN_URI"); uri != "" {
		return uri
	}
	return fmt.Sprintf(
		"postgresql://postgres:mysecretpassword@%s.%s.svc.cluster.local:5432/postgres",
		postgresServiceName,
		namespace,
	)
}

func resourceMustParse(value string) resource.Quantity {
	quantity, err := resource.ParseQuantity(value)
	Expect(err).NotTo(HaveOccurred())
	return quantity
}

func ptrTo[T any](value T) *T {
	return &value
}

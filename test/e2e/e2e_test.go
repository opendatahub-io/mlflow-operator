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

package e2e

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"

	"github.com/opendatahub-io/mlflow-operator/test/utils"
)

// namespace where the project is deployed in
const namespace = "opendatahub"

// serviceAccountName created for the project
const serviceAccountName = "mlflow-operator-controller-manager"

// metricsServiceName is the name of the metrics service of the project
const metricsServiceName = "mlflow-operator-controller-manager-metrics-service"

// metricsRoleBindingName is the name of the RBAC that will be created to allow get the metrics data
const metricsRoleBindingName = "mlflow-operator-metrics-binding"

var _ = Describe("Manager", Ordered, func() {
	var controllerPodName string

	// Before running the tests, set up the environment by creating the namespace,
	// enforce the restricted security policy to the namespace, installing CRDs,
	// and deploying the controller.
	BeforeAll(func() {
		By("creating manager namespace")
		cmd := exec.Command("kubectl", "create", "ns", namespace)
		_, err := utils.Run(cmd)
		Expect(err).NotTo(HaveOccurred(), "Failed to create namespace")

		By("labeling the namespace to enforce the restricted security policy")
		cmd = exec.Command("kubectl", "label", "--overwrite", "ns", namespace,
			"pod-security.kubernetes.io/enforce=restricted")
		_, err = utils.Run(cmd)
		Expect(err).NotTo(HaveOccurred(), "Failed to label namespace with restricted policy")

		By("installing CRDs")
		cmd = exec.Command("make", "install")
		_, err = utils.Run(cmd)
		Expect(err).NotTo(HaveOccurred(), "Failed to install CRDs")

		By("deploying the controller-manager")
		cmd = exec.Command("make", "deploy", fmt.Sprintf("IMG=%s", projectImage))
		_, err = utils.Run(cmd)
		Expect(err).NotTo(HaveOccurred(), "Failed to deploy the controller-manager")
	})

	// After all tests have been executed, clean up by undeploying the controller, uninstalling CRDs,
	// and deleting the namespace.
	AfterAll(func() {
		By("cleaning up the curl pod for metrics")
		cmd := exec.Command("kubectl", "delete", "pod", "curl-metrics", "-n", namespace, "--ignore-not-found=true")
		_, _ = utils.Run(cmd)

		By("cleaning up the ClusterRoleBinding for metrics")
		cmd = exec.Command("kubectl", "delete", "clusterrolebinding", metricsRoleBindingName, "--ignore-not-found=true")
		_, _ = utils.Run(cmd)

		By("cleaning up any MLflow resources")
		cmd = exec.Command("kubectl", "delete", "mlflow", "--all", "--ignore-not-found=true")
		_, _ = utils.Run(cmd)

		By("undeploying the controller-manager")
		cmd = exec.Command("make", "undeploy")
		_, _ = utils.Run(cmd)

		By("uninstalling CRDs")
		cmd = exec.Command("make", "uninstall")
		_, _ = utils.Run(cmd)

		By("removing manager namespace")
		cmd = exec.Command("kubectl", "delete", "ns", namespace)
		_, _ = utils.Run(cmd)
	})

	// After each test, check for failures and collect logs, events,
	// and pod descriptions for debugging.
	AfterEach(func() {
		specReport := CurrentSpecReport()
		if specReport.Failed() {
			By("Fetching controller manager pod logs")
			cmd := exec.Command("kubectl", "logs", controllerPodName, "-n", namespace)
			controllerLogs, err := utils.Run(cmd)
			if err == nil {
				_, _ = fmt.Fprintf(GinkgoWriter, "Controller logs:\n %s", controllerLogs)
			} else {
				_, _ = fmt.Fprintf(GinkgoWriter, "Failed to get Controller logs: %s", err)
			}

			By("Fetching Kubernetes events")
			cmd = exec.Command("kubectl", "get", "events", "-n", namespace, "--sort-by=.lastTimestamp")
			eventsOutput, err := utils.Run(cmd)
			if err == nil {
				_, _ = fmt.Fprintf(GinkgoWriter, "Kubernetes events:\n%s", eventsOutput)
			} else {
				_, _ = fmt.Fprintf(GinkgoWriter, "Failed to get Kubernetes events: %s", err)
			}

			By("Fetching curl-metrics logs")
			cmd = exec.Command("kubectl", "logs", "curl-metrics", "-n", namespace)
			metricsOutput, err := utils.Run(cmd)
			if err == nil {
				_, _ = fmt.Fprintf(GinkgoWriter, "Metrics logs:\n %s", metricsOutput)
			} else {
				_, _ = fmt.Fprintf(GinkgoWriter, "Failed to get curl-metrics logs: %s", err)
			}

			By("Fetching controller manager pod description")
			cmd = exec.Command("kubectl", "describe", "pod", controllerPodName, "-n", namespace)
			podDescription, err := utils.Run(cmd)
			if err == nil {
				fmt.Println("Pod description:\n", podDescription)
			} else {
				fmt.Println("Failed to describe controller pod")
			}
		}
	})

	SetDefaultEventuallyTimeout(2 * time.Minute)
	SetDefaultEventuallyPollingInterval(time.Second)

	Context("Manager", func() {
		It("should run successfully", func() {
			By("validating that the controller-manager pod is running as expected")
			verifyControllerUp := func(g Gomega) {
				// Get the name of the controller-manager pod
				cmd := exec.Command("kubectl", "get",
					"pods", "-l", "control-plane=controller-manager",
					"-o", "go-template={{ range .items }}"+
						"{{ if not .metadata.deletionTimestamp }}"+
						"{{ .metadata.name }}"+
						"{{ \"\\n\" }}{{ end }}{{ end }}",
					"-n", namespace,
				)

				podOutput, err := utils.Run(cmd)
				g.Expect(err).NotTo(HaveOccurred(), "Failed to retrieve controller-manager pod information")
				podNames := utils.GetNonEmptyLines(podOutput)
				g.Expect(podNames).To(HaveLen(1), "expected 1 controller pod running")
				controllerPodName = podNames[0]
				g.Expect(controllerPodName).To(ContainSubstring("controller-manager"))

				// Validate the pod's status
				cmd = exec.Command("kubectl", "get",
					"pods", controllerPodName, "-o", "jsonpath={.status.phase}",
					"-n", namespace,
				)
				output, err := utils.Run(cmd)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(output).To(Equal("Running"), "Incorrect controller-manager pod status")
			}
			Eventually(verifyControllerUp).Should(Succeed())
		})

		It("should ensure the metrics endpoint is serving metrics", func() {
			By("cleaning up any existing ClusterRoleBinding for metrics")
			cmd := exec.Command("kubectl", "delete", "clusterrolebinding", metricsRoleBindingName, "--ignore-not-found=true")
			_, _ = utils.Run(cmd)

			By("creating a ClusterRoleBinding for the service account to allow access to metrics")
			cmd = exec.Command("kubectl", "create", "clusterrolebinding", metricsRoleBindingName,
				"--clusterrole=mlflow-operator-metrics-reader",
				fmt.Sprintf("--serviceaccount=%s:%s", namespace, serviceAccountName),
			)
			_, err := utils.Run(cmd)
			Expect(err).NotTo(HaveOccurred(), "Failed to create ClusterRoleBinding")

			By("validating that the metrics service is available")
			cmd = exec.Command("kubectl", "get", "service", metricsServiceName, "-n", namespace)
			_, err = utils.Run(cmd)
			Expect(err).NotTo(HaveOccurred(), "Metrics service should exist")

			By("getting the service account token")
			token, err := serviceAccountToken()
			Expect(err).NotTo(HaveOccurred())
			Expect(token).NotTo(BeEmpty())

			By("ensuring the controller pod is ready")
			verifyControllerPodReady := func(g Gomega) {
				cmd := exec.Command("kubectl", "get", "pod", controllerPodName, "-n", namespace,
					"-o", "jsonpath={.status.conditions[?(@.type=='Ready')].status}")
				output, err := utils.Run(cmd)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(output).To(Equal("True"), "Controller pod not ready")
			}
			Eventually(verifyControllerPodReady, 3*time.Minute, time.Second).Should(Succeed())

			By("verifying that the controller manager is serving the metrics server")
			verifyMetricsServerStarted := func(g Gomega) {
				cmd := exec.Command("kubectl", "logs", controllerPodName, "-n", namespace)
				output, err := utils.Run(cmd)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(output).To(ContainSubstring("Serving metrics server"),
					"Metrics server not yet started")
			}
			Eventually(verifyMetricsServerStarted, 3*time.Minute, time.Second).Should(Succeed())

			// +kubebuilder:scaffold:e2e-metrics-webhooks-readiness

			By("cleaning up any existing curl-metrics pod")
			cmd = exec.Command("kubectl", "delete", "pod", "curl-metrics", "-n", namespace, "--ignore-not-found=true")
			_, _ = utils.Run(cmd)

			By("creating the curl-metrics pod to access the metrics endpoint")
			cmd = exec.Command("kubectl", "run", "curl-metrics", "--restart=Never",
				"--namespace", namespace,
				"--image=curlimages/curl:latest",
				"--overrides",
				fmt.Sprintf(`{
					"spec": {
						"containers": [{
							"name": "curl",
							"image": "curlimages/curl:latest",
							"command": ["/bin/sh", "-c"],
							"args": ["curl -v -k -H 'Authorization: Bearer %s' https://%s.%s.svc.cluster.local:8443/metrics"],
							"securityContext": {
								"readOnlyRootFilesystem": true,
								"allowPrivilegeEscalation": false,
								"capabilities": {
									"drop": ["ALL"]
								},
								"runAsNonRoot": true,
								"runAsUser": 1000,
								"seccompProfile": {
									"type": "RuntimeDefault"
								}
							}
						}],
						"serviceAccountName": "%s"
					}
				}`, token, metricsServiceName, namespace, serviceAccountName))
			_, err = utils.Run(cmd)
			Expect(err).NotTo(HaveOccurred(), "Failed to create curl-metrics pod")

			By("waiting for the curl-metrics pod to complete.")
			verifyCurlUp := func(g Gomega) {
				cmd := exec.Command("kubectl", "get", "pods", "curl-metrics",
					"-o", "jsonpath={.status.phase}",
					"-n", namespace)
				output, err := utils.Run(cmd)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(output).To(Equal("Succeeded"), "curl pod in wrong status")
			}
			Eventually(verifyCurlUp, 5*time.Minute).Should(Succeed())

			By("getting the metrics by checking curl-metrics logs")
			verifyMetricsAvailable := func(g Gomega) {
				metricsOutput, err := getMetricsOutput()
				g.Expect(err).NotTo(HaveOccurred(), "Failed to retrieve logs from curl pod")
				g.Expect(metricsOutput).NotTo(BeEmpty())
				g.Expect(metricsOutput).To(ContainSubstring("< HTTP/1.1 200 OK"))
			}
			Eventually(verifyMetricsAvailable, 2*time.Minute).Should(Succeed())
		})

		// +kubebuilder:scaffold:e2e-webhooks-checks

		It("should validate CEL constraint for singleton MLflow resource", func() {
			By("creating an MLflow resource with the correct name 'mlflow'")
			mlflowYAML := `apiVersion: mlflow.opendatahub.io/v1
kind: MLflow
metadata:
  name: mlflow
spec:
  serveArtifacts: true
  artifactsDestination: s3://mlflow-artifacts/test
  defaultArtifactRoot: s3://mlflow-artifacts/test-root
  backendStoreUri: postgresql://user:pass@db:5432/mlflow
  registryStoreUri: postgresql://user:pass@db:5432/mlflow`

			mlflowFile := filepath.Join("/tmp", "mlflow-valid.yaml")
			err := os.WriteFile(mlflowFile, []byte(mlflowYAML), os.FileMode(0o644))
			Expect(err).NotTo(HaveOccurred(), "Failed to write valid MLflow manifest")
			defer func() {
				if removeErr := os.Remove(mlflowFile); removeErr != nil {
					_, _ = fmt.Fprintf(GinkgoWriter, "failed to remove %s: %v\n", mlflowFile, removeErr)
				}
			}()

			cmd := exec.Command("kubectl", "apply", "-f", mlflowFile)
			_, err = utils.Run(cmd)
			Expect(err).NotTo(HaveOccurred(), "Failed to create MLflow resource with name 'mlflow'")

			By("verifying the MLflow resource was created successfully")
			cmd = exec.Command("kubectl", "get", "mlflow", "mlflow", "-o", "jsonpath={.metadata.name}")
			output, err := utils.Run(cmd)
			Expect(err).NotTo(HaveOccurred())
			Expect(output).To(Equal("mlflow"), "MLflow resource should exist with name 'mlflow'")

			By("attempting to create an MLflow resource with an invalid name")
			invalidYAML := `apiVersion: mlflow.opendatahub.io/v1
kind: MLflow
metadata:
  name: invalid-name
spec:
  serveArtifacts: true
  artifactsDestination: s3://mlflow-artifacts/test
  defaultArtifactRoot: s3://mlflow-artifacts/test-root
  backendStoreUri: postgresql://user:pass@db:5432/mlflow
  registryStoreUri: postgresql://user:pass@db:5432/mlflow`

			invalidFile := filepath.Join("/tmp", "mlflow-invalid.yaml")
			err = os.WriteFile(invalidFile, []byte(invalidYAML), os.FileMode(0o644))
			Expect(err).NotTo(HaveOccurred(), "Failed to write invalid MLflow manifest")
			defer func() {
				if removeErr := os.Remove(invalidFile); removeErr != nil {
					_, _ = fmt.Fprintf(GinkgoWriter, "failed to remove %s: %v\n", invalidFile, removeErr)
				}
			}()

			cmd = exec.Command("kubectl", "apply", "-f", invalidFile)
			output, err = utils.Run(cmd)
			Expect(err).To(HaveOccurred(), "Should fail to create MLflow with invalid name")
			Expect(output).To(ContainSubstring("MLflow resource name must be 'mlflow'"),
				"Error message should indicate name validation failure")

			By("cleaning up the valid MLflow resource")
			cmd = exec.Command("kubectl", "delete", "mlflow", "mlflow")
			_, err = utils.Run(cmd)
			Expect(err).NotTo(HaveOccurred(), "Failed to delete MLflow resource")

			By("verifying the MLflow resource was deleted")
			verifyDeleted := func(g Gomega) {
				cmd := exec.Command("kubectl", "get", "mlflow", "mlflow")
				_, err := utils.Run(cmd)
				g.Expect(err).To(HaveOccurred(), "MLflow resource should not exist after deletion")
			}
			Eventually(verifyDeleted, 30*time.Second).Should(Succeed())
		})

		It("should validate CEL constraint for singleton MLflowConfig resource", func() {
			By("creating an MLflowConfig resource with the correct name 'mlflow'")
			mlflowConfigYAML := `apiVersion: mlflow.opendatahub.io/v1
kind: MLflowConfig
metadata:
  name: mlflow
spec:
  artifactRootSecret: data-science-team-s3-credentials`

			mlflowConfigFile := filepath.Join("/tmp", "mlflowconfig-valid.yaml")
			err := os.WriteFile(mlflowConfigFile, []byte(mlflowConfigYAML), os.FileMode(0o644))
			Expect(err).NotTo(HaveOccurred(), "Failed to write valid MLflowConfig manifest")
			defer func() {
				if removeErr := os.Remove(mlflowConfigFile); removeErr != nil {
					_, _ = fmt.Fprintf(GinkgoWriter, "failed to remove %s: %v\n", mlflowConfigFile, removeErr)
				}
			}()

			cmd := exec.Command("kubectl", "apply", "-n", namespace, "-f", mlflowConfigFile)
			_, err = utils.Run(cmd)
			Expect(err).NotTo(HaveOccurred(), "Failed to create MLflowConfig resource with name 'mlflow'")

			By("verifying the MLflowConfig resource was created successfully")
			cmd = exec.Command("kubectl", "get", "mlflowconfig", "mlflow", "-n", namespace, "-o", "jsonpath={.metadata.name}")
			output, err := utils.Run(cmd)
			Expect(err).NotTo(HaveOccurred())
			Expect(output).To(Equal("mlflow"), "MLflowConfig resource should exist with name 'mlflow'")

			By("attempting to create an MLflowConfig resource with an invalid name")
			invalidConfigYAML := `apiVersion: mlflow.opendatahub.io/v1
kind: MLflowConfig
metadata:
  name: invalid-name
spec:
  artifactRootSecret: data-science-team-s3-credentials`

			invalidConfigFile := filepath.Join("/tmp", "mlflowconfig-invalid.yaml")
			err = os.WriteFile(invalidConfigFile, []byte(invalidConfigYAML), os.FileMode(0o644))
			Expect(err).NotTo(HaveOccurred(), "Failed to write invalid MLflowConfig manifest")
			defer func() {
				if removeErr := os.Remove(invalidConfigFile); removeErr != nil {
					_, _ = fmt.Fprintf(GinkgoWriter, "failed to remove %s: %v\n", invalidConfigFile, removeErr)
				}
			}()

			cmd = exec.Command("kubectl", "apply", "-n", namespace, "-f", invalidConfigFile)
			output, err = utils.Run(cmd)
			Expect(err).To(HaveOccurred(), "Should fail to create MLflowConfig with invalid name")
			Expect(output).To(ContainSubstring("MLflowConfig resource name must be 'mlflow'"),
				"Error message should indicate name validation failure")

			By("cleaning up the valid MLflowConfig resource")
			cmd = exec.Command("kubectl", "delete", "mlflowconfig", "mlflow", "-n", namespace)
			_, err = utils.Run(cmd)
			Expect(err).NotTo(HaveOccurred(), "Failed to delete MLflowConfig resource")

			By("verifying the MLflowConfig resource was deleted")
			verifyConfigDeleted := func(g Gomega) {
				cmd := exec.Command("kubectl", "get", "mlflowconfig", "mlflow", "-n", namespace)
				_, err := utils.Run(cmd)
				g.Expect(err).To(HaveOccurred(), "MLflowConfig resource should not exist after deletion")
			}
			Eventually(verifyConfigDeleted, 30*time.Second).Should(Succeed())
		})

		// TODO: Customize the e2e test suite with scenarios specific to your project.
		// Consider applying sample/CR(s) and check their status and/or verifying
		// the reconciliation by using the metrics, i.e.:
		// metricsOutput, err := getMetricsOutput()
		// Expect(err).NotTo(HaveOccurred(), "Failed to retrieve logs from curl pod")
		// Expect(metricsOutput).To(ContainSubstring(
		//    fmt.Sprintf(`controller_runtime_reconcile_total{controller="%s",result="success"} 1`,
		//    strings.ToLower(<Kind>),
		// ))
	})
})

// serviceAccountToken returns a token for the specified service account in the given namespace.
// It uses the Kubernetes TokenRequest API to generate a token by directly sending a request
// and parsing the resulting token from the API response.
func serviceAccountToken() (string, error) {
	const tokenRequestRawString = `{
		"apiVersion": "authentication.k8s.io/v1",
		"kind": "TokenRequest"
	}`

	// Temporary file to store the token request
	secretName := fmt.Sprintf("%s-token-request", serviceAccountName)
	tokenRequestFile := filepath.Join("/tmp", secretName)
	err := os.WriteFile(tokenRequestFile, []byte(tokenRequestRawString), os.FileMode(0o644))
	if err != nil {
		return "", err
	}

	defer func(name string) {
		err := os.Remove(name)
		if err != nil {
			_ = fmt.Sprintf("Failed to remove file %s", name)
		}
	}(tokenRequestFile) // Clean up temp file

	var out string
	verifyTokenCreation := func(g Gomega) {
		// Execute kubectl command to create the token
		cmd := exec.Command("kubectl", "create", "--raw", fmt.Sprintf(
			"/api/v1/namespaces/%s/serviceaccounts/%s/token",
			namespace,
			serviceAccountName,
		), "-f", tokenRequestFile)

		output, err := cmd.CombinedOutput()
		g.Expect(err).NotTo(HaveOccurred())

		// Parse the JSON output to extract the token
		var token tokenRequest
		err = json.Unmarshal(output, &token)
		g.Expect(err).NotTo(HaveOccurred())

		out = token.Status.Token
	}
	if !Eventually(verifyTokenCreation).Should(Succeed()) {
		return "", fmt.Errorf("failed to create service account token")
	}

	return out, nil
}

// getMetricsOutput retrieves and returns the logs from the curl pod used to access the metrics endpoint.
func getMetricsOutput() (string, error) {
	By("getting the curl-metrics logs")
	cmd := exec.Command("kubectl", "logs", "curl-metrics", "-n", namespace)
	return utils.Run(cmd)
}

// tokenRequest is a simplified representation of the Kubernetes TokenRequest API response,
// containing only the token field that we need to extract.
type tokenRequest struct {
	Status struct {
		Token string `json:"token"`
	} `json:"status"`
}

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
	"testing"

	gomega "github.com/onsi/gomega"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

func TestMlflowToHelmValues_Metrics(t *testing.T) {
	renderer := &HelmRenderer{}

	tests := []struct {
		name                    string
		mlflow                  *mlflowv1.MLflow
		namespace               string
		isOpenShift             bool
		serviceMonitorAvailable bool
		wantEnabled             bool
		wantServerName          string
	}{
		{
			name: "OpenShift: metrics enabled with CA-based tlsConfig",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
				},
			},
			namespace:               "test-namespace",
			isOpenShift:             true,
			serviceMonitorAvailable: true,
			wantEnabled:             true,
			wantServerName:          "mlflow.test-namespace.svc",
		},
		{
			name: "OpenShift: custom CR name includes suffix in serverName",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "custom-mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
				},
			},
			namespace:               "opendatahub",
			isOpenShift:             true,
			serviceMonitorAvailable: true,
			wantEnabled:             true,
			wantServerName:          "mlflow-custom-mlflow.opendatahub.svc",
		},
		{
			name: "non-OpenShift: metrics enabled with insecureSkipVerify",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
				},
			},
			namespace:               "default",
			isOpenShift:             false,
			serviceMonitorAvailable: true,
			wantEnabled:             true,
		},
		{
			name: "ServiceMonitor CRD absent: metrics disabled regardless of platform",
			mlflow: &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Spec: mlflowv1.MLflowSpec{
					BackendStoreURI: ptr(testBackendStoreURI),
				},
			},
			namespace:               "default",
			isOpenShift:             false,
			serviceMonitorAvailable: false,
			wantEnabled:             false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := gomega.NewWithT(t)

			opts := RenderOptions{IsOpenShift: tt.isOpenShift, ServiceMonitorAvailable: tt.serviceMonitorAvailable}
			values, err := renderer.mlflowToHelmValues(tt.mlflow, tt.namespace, opts)
			g.Expect(err).NotTo(gomega.HaveOccurred())

			metrics, ok := values["metrics"].(map[string]interface{})
			g.Expect(ok).To(gomega.BeTrue(), "metrics should be present in values")

			enabled, ok := metrics["enabled"].(bool)
			g.Expect(ok).To(gomega.BeTrue(), "metrics.enabled should be present")
			g.Expect(enabled).To(gomega.Equal(tt.wantEnabled))

			tlsConfig, hasTLSConfig := metrics["tlsConfig"].(map[string]interface{})
			g.Expect(hasTLSConfig).To(gomega.BeTrue(), "metrics.tlsConfig should always be present")

			if tt.isOpenShift {
				ca, ok := tlsConfig["ca"].(map[string]interface{})
				g.Expect(ok).To(gomega.BeTrue(), "tlsConfig.ca should be present")

				configMap, ok := ca["configMap"].(map[string]interface{})
				g.Expect(ok).To(gomega.BeTrue(), "tlsConfig.ca.configMap should be present")
				g.Expect(configMap["name"]).To(gomega.Equal("openshift-service-ca.crt"))
				g.Expect(configMap["key"]).To(gomega.Equal("service-ca.crt"))

				serverName, ok := tlsConfig["serverName"].(string)
				g.Expect(ok).To(gomega.BeTrue(), "tlsConfig.serverName should be present")
				g.Expect(serverName).To(gomega.Equal(tt.wantServerName))
			} else {
				insecureSkipVerify, ok := tlsConfig["insecureSkipVerify"].(bool)
				g.Expect(ok).To(gomega.BeTrue(), "tlsConfig.insecureSkipVerify should be present")
				g.Expect(insecureSkipVerify).To(gomega.BeTrue())

				_, hasCA := tlsConfig["ca"]
				g.Expect(hasCA).To(gomega.BeFalse(), "tlsConfig.ca should not be present on non-OpenShift")

				_, hasServerName := tlsConfig["serverName"]
				g.Expect(hasServerName).To(gomega.BeFalse(), "tlsConfig.serverName should not be present on non-OpenShift")
			}
		})
	}
}

func TestRenderChart_ServiceMonitorWithTLSConfig(t *testing.T) {
	g := gomega.NewWithT(t)
	renderer := NewHelmRenderer("../../charts/mlflow")

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "test-mlflow"},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr(testBackendStoreURI),
		},
	}

	// Render chart on OpenShift - CA-based tlsConfig should be set
	objs, err := renderer.RenderChart(mlflow, "opendatahub", RenderOptions{IsOpenShift: true, ServiceMonitorAvailable: true})
	g.Expect(err).NotTo(gomega.HaveOccurred())

	var serviceMonitor *unstructured.Unstructured
	for _, obj := range objs {
		if obj.GetKind() == "ServiceMonitor" {
			serviceMonitor = obj
			break
		}
	}
	g.Expect(serviceMonitor).NotTo(gomega.BeNil(), "ServiceMonitor should be rendered when metrics.enabled=true")

	// Verify ServiceMonitor metadata
	g.Expect(serviceMonitor.GetName()).To(gomega.Equal("mlflow-metrics-monitor-test-mlflow"))
	g.Expect(serviceMonitor.GetNamespace()).To(gomega.Equal("opendatahub"))

	// Verify labels include instance-specific app label
	labels := serviceMonitor.GetLabels()
	g.Expect(labels["app"]).To(gomega.Equal("mlflow-test-mlflow"))

	// Verify endpoints configuration
	endpoints, found, err := unstructured.NestedSlice(serviceMonitor.Object, "spec", "endpoints")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue())
	g.Expect(endpoints).To(gomega.HaveLen(1))

	endpoint := endpoints[0].(map[string]interface{})
	g.Expect(endpoint["path"]).To(gomega.Equal("/metrics"))
	g.Expect(endpoint["port"]).To(gomega.Equal("https"))
	g.Expect(endpoint["scheme"]).To(gomega.Equal("https"))

	// Verify TLS config with CA bundle reference
	tlsConfig, ok := endpoint["tlsConfig"].(map[string]interface{})
	g.Expect(ok).To(gomega.BeTrue(), "tlsConfig should be present")

	ca, ok := tlsConfig["ca"].(map[string]interface{})
	g.Expect(ok).To(gomega.BeTrue(), "tlsConfig.ca should be present")

	configMap, ok := ca["configMap"].(map[string]interface{})
	g.Expect(ok).To(gomega.BeTrue(), "tlsConfig.ca.configMap should be present")
	g.Expect(configMap["name"]).To(gomega.Equal("openshift-service-ca.crt"))
	g.Expect(configMap["key"]).To(gomega.Equal("service-ca.crt"))

	// Verify serverName for certificate validation
	serverName, ok := tlsConfig["serverName"].(string)
	g.Expect(ok).To(gomega.BeTrue(), "tlsConfig.serverName should be present")
	g.Expect(serverName).To(gomega.Equal("mlflow-test-mlflow.opendatahub.svc"))

	// Verify selector matches Service labels
	matchLabels, found, err := unstructured.NestedStringMap(serviceMonitor.Object, "spec", "selector", "matchLabels")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue())
	g.Expect(matchLabels["app"]).To(gomega.Equal("mlflow-test-mlflow"))

	// On OpenShift, TLS secret should use 0640 (416) since SCC provides fsGroup
	deployment := findObject(objs, deploymentKind, "mlflow-test-mlflow")
	g.Expect(deployment).NotTo(gomega.BeNil())
	volumes, found, err := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "volumes")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue())
	foundTLS := false
	for _, v := range volumes {
		vol := v.(map[string]interface{})
		if vol["name"] == "mlflow-tls" {
			foundTLS = true
			mode, foundMode, err := unstructured.NestedInt64(vol, "secret", "defaultMode")
			g.Expect(err).NotTo(gomega.HaveOccurred())
			g.Expect(foundMode).To(gomega.BeTrue(), "defaultMode should be set")
			g.Expect(mode).To(gomega.Equal(int64(416)), "OpenShift TLS defaultMode should be 0640 (416)")
		}
	}
	g.Expect(foundTLS).To(gomega.BeTrue(), "mlflow-tls volume should be present")
}

func TestRenderChart_ServiceMonitorInsecureSkipVerify(t *testing.T) {
	g := gomega.NewWithT(t)
	renderer := NewHelmRenderer("../../charts/mlflow")

	mlflow := &mlflowv1.MLflow{
		ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		Spec: mlflowv1.MLflowSpec{
			BackendStoreURI: ptr(testBackendStoreURI),
		},
	}

	// Render on non-OpenShift - should fall back to insecureSkipVerify
	objs, err := renderer.RenderChart(mlflow, "default", RenderOptions{IsOpenShift: false, ServiceMonitorAvailable: true})
	g.Expect(err).NotTo(gomega.HaveOccurred())

	var serviceMonitor *unstructured.Unstructured
	for _, obj := range objs {
		if obj.GetKind() == "ServiceMonitor" {
			serviceMonitor = obj
			break
		}
	}
	g.Expect(serviceMonitor).NotTo(gomega.BeNil(), "ServiceMonitor should be rendered when metrics.enabled=true")

	endpoints, found, err := unstructured.NestedSlice(serviceMonitor.Object, "spec", "endpoints")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue())
	g.Expect(endpoints).To(gomega.HaveLen(1))

	endpoint := endpoints[0].(map[string]interface{})

	// Verify TLS config falls back to insecureSkipVerify on non-OpenShift
	tlsConfig, ok := endpoint["tlsConfig"].(map[string]interface{})
	g.Expect(ok).To(gomega.BeTrue(), "tlsConfig should be present")

	insecureSkipVerify, ok := tlsConfig["insecureSkipVerify"].(bool)
	g.Expect(ok).To(gomega.BeTrue(), "tlsConfig.insecureSkipVerify should be present")
	g.Expect(insecureSkipVerify).To(gomega.BeTrue())

	_, hasCA := tlsConfig["ca"]
	g.Expect(hasCA).To(gomega.BeFalse(), "tlsConfig.ca should not be present on non-OpenShift")

	_, hasServerName := tlsConfig["serverName"]
	g.Expect(hasServerName).To(gomega.BeFalse(), "tlsConfig.serverName should not be present on non-OpenShift")

	// On vanilla Kubernetes, TLS secret should use 0644 (420) since there is no fsGroup
	deployment := findObject(objs, deploymentKind, "mlflow")
	g.Expect(deployment).NotTo(gomega.BeNil())
	volumes, found, err := unstructured.NestedSlice(deployment.Object, "spec", "template", "spec", "volumes")
	g.Expect(err).NotTo(gomega.HaveOccurred())
	g.Expect(found).To(gomega.BeTrue())
	foundTLS := false
	for _, v := range volumes {
		vol := v.(map[string]interface{})
		if vol["name"] == "mlflow-tls" {
			foundTLS = true
			mode, foundMode, err := unstructured.NestedInt64(vol, "secret", "defaultMode")
			g.Expect(err).NotTo(gomega.HaveOccurred())
			g.Expect(foundMode).To(gomega.BeTrue(), "defaultMode should be set")
			g.Expect(mode).To(gomega.Equal(int64(420)), "non-OpenShift TLS defaultMode should be 0644 (420)")
		}
	}
	g.Expect(foundTLS).To(gomega.BeTrue(), "mlflow-tls volume should be present")
}

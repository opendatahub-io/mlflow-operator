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
	"encoding/json"
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

func TestReferencedDeploymentSecretNames(t *testing.T) {
	mlflow := &mlflowv1.MLflow{Spec: mlflowv1.MLflowSpec{
		BackendStoreURIFrom:            secretSelector("database"),
		ReadReplicaBackendStoreURIFrom: secretSelector("database"),
		RegistryStoreURIFrom:           secretSelector("registry"),
		Env:                            []corev1.EnvVar{{ValueFrom: &corev1.EnvVarSource{SecretKeyRef: secretSelector("env")}}},
		EnvFrom: []corev1.EnvFromSource{{SecretRef: &corev1.SecretEnvSource{
			LocalObjectReference: corev1.LocalObjectReference{Name: "env-from"},
		}}},
	}}

	got := referencedDeploymentSecretNames(mlflow)
	for _, name := range []string{TLSSecretName, "database", "registry", "env", "env-from"} {
		if !got[name] {
			t.Errorf("referencedDeploymentSecretNames() missing %q", name)
		}
	}
	if len(got) != 5 {
		t.Errorf("referencedDeploymentSecretNames() returned %d names, want 5", len(got))
	}
}

func TestReferencedConfigMapRefreshAndAnnotation(t *testing.T) {
	scheme := secretReferenceTestScheme(t)
	instance := &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: "mlflow"}, Spec: mlflowv1.MLflowSpec{
		BackendStoreURI: ptr("postgresql://db/mlflow"),
		Env:             []corev1.EnvVar{{Name: rollupsRuntimeEnvName, ValueFrom: &corev1.EnvVarSource{ConfigMapKeyRef: &corev1.ConfigMapKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "runtime-settings"}, Key: "enabled"}}}},
		EnvFrom:         []corev1.EnvFromSource{{ConfigMapRef: &corev1.ConfigMapEnvSource{LocalObjectReference: corev1.LocalObjectReference{Name: "shared-settings"}}}},
	}}
	cm := &corev1.ConfigMap{ObjectMeta: metav1.ObjectMeta{Name: "runtime-settings", Namespace: "applications", ResourceVersion: "7"}, Data: map[string]string{"enabled": "true"}}
	cl := fake.NewClientBuilder().WithScheme(scheme).WithObjects(instance, cm).Build()
	r := &MLflowReconciler{Client: cl, Namespace: "applications"}
	for _, name := range []string{"runtime-settings", "shared-settings"} {
		if !referencedDeploymentConfigMapNames(instance)[name] {
			t.Fatalf("missing ConfigMap reference %s", name)
		}
		requests := r.referencedConfigMapToMLflowRequests(context.Background(), &corev1.ConfigMap{ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "applications"}})
		if len(requests) != 1 || requests[0].Name != "mlflow" {
			t.Fatalf("ConfigMap %s requests = %+v", name, requests)
		}
	}
	if requests := r.referencedConfigMapToMLflowRequests(context.Background(), &corev1.ConfigMap{ObjectMeta: metav1.ObjectMeta{Name: "unrelated", Namespace: "applications"}}); len(requests) != 0 {
		t.Fatalf("unrelated ConfigMap requests = %+v", requests)
	}
	versions, err := r.referencedConfigMapResourceVersions(context.Background(), instance, "applications")
	if err != nil {
		t.Fatal(err)
	}
	if len(versions) != 1 || versions["runtime-settings"] != "7" {
		t.Fatalf("ConfigMap versions = %+v", versions)
	}
	values, err := (&HelmRenderer{}).mlflowToHelmValues(instance, "applications", RenderOptions{ReferencedConfigMapResourceVersions: versions}, nil)
	if err != nil {
		t.Fatal(err)
	}
	annotations := values["podAnnotations"].(map[string]interface{})
	if annotations[configMapResourceVersionsAnnotation] != `{"runtime-settings":"7"}` {
		t.Fatalf("ConfigMap annotation = %+v", annotations)
	}
}

func TestSecretToMLflowRequestsFiltersUnreferencedSecrets(t *testing.T) {
	scheme := secretReferenceTestScheme(t)
	referenced := &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: "referenced"}, Spec: mlflowv1.MLflowSpec{
		EnvFrom: []corev1.EnvFromSource{{SecretRef: &corev1.SecretEnvSource{
			LocalObjectReference: corev1.LocalObjectReference{Name: "credentials"},
		}}},
	}}
	unreferenced := &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: "unreferenced"}}
	reconciler := &MLflowReconciler{
		Client:    fake.NewClientBuilder().WithScheme(scheme).WithObjects(referenced, unreferenced).Build(),
		Namespace: "applications",
	}

	requests := reconciler.secretToMLflowRequests(context.Background(), &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Name: "credentials", Namespace: "applications"},
	})
	if len(requests) != 1 || requests[0].NamespacedName != (types.NamespacedName{Name: "referenced"}) {
		t.Fatalf("secretToMLflowRequests() = %#v, want request for referenced MLflow", requests)
	}
	requests = reconciler.secretToMLflowRequests(context.Background(), &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Name: "unrelated", Namespace: "applications"},
	})
	if len(requests) != 0 {
		t.Errorf("unrelated Secret produced requests: %#v", requests)
	}
}

func TestReferencedSecretResourceVersionsAreRenderedAsPodAnnotation(t *testing.T) {
	scheme := secretReferenceTestScheme(t)
	secret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "credentials", Namespace: "applications", ResourceVersion: "17"}}
	tlsSecret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: TLSSecretName, Namespace: "applications", ResourceVersion: "23"}}
	reconciler := &MLflowReconciler{Client: fake.NewClientBuilder().WithScheme(scheme).WithObjects(secret, tlsSecret).Build()}
	mlflow := &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: ResourceName}, Spec: mlflowv1.MLflowSpec{
		PodAnnotations: map[string]string{
			"example.com/keep":               "true",
			secretResourceVersionsAnnotation: "user-value",
		},
		EnvFrom: []corev1.EnvFromSource{{SecretRef: &corev1.SecretEnvSource{
			LocalObjectReference: corev1.LocalObjectReference{Name: "credentials"},
		}}},
	}}

	versions, err := reconciler.referencedSecretResourceVersions(context.Background(), mlflow, "applications")
	if err != nil {
		t.Fatalf("referencedSecretResourceVersions() error = %v", err)
	}
	values, err := (&HelmRenderer{}).mlflowToHelmValues(mlflow, "applications", RenderOptions{
		ReferencedSecretResourceVersions: versions,
	}, nil)
	if err != nil {
		t.Fatalf("mlflowToHelmValues() error = %v", err)
	}
	annotations := values["podAnnotations"].(map[string]interface{})
	if got := annotations[secretResourceVersionsAnnotation]; got != `{"credentials":"17","mlflow-tls":"23"}` {
		t.Errorf("secret resource-version annotation = %q", got)
	}
	if got := annotations["example.com/keep"]; got != "true" {
		t.Errorf("preserved annotation = %q, want true", got)
	}

	rotated := &corev1.Secret{}
	if err := reconciler.Get(context.Background(), types.NamespacedName{Namespace: "applications", Name: "credentials"}, rotated); err != nil {
		t.Fatalf("get Secret for rotation: %v", err)
	}
	rotated.Data = map[string][]byte{"value": []byte("rotated")}
	if err := reconciler.Update(context.Background(), rotated); err != nil {
		t.Fatalf("update rotated Secret: %v", err)
	}
	rotatedVersions, err := reconciler.referencedSecretResourceVersions(context.Background(), mlflow, "applications")
	if err != nil {
		t.Fatalf("read resource versions after Secret rotation: %v", err)
	}
	rotatedValues, err := (&HelmRenderer{}).mlflowToHelmValues(mlflow, "applications", RenderOptions{
		ReferencedSecretResourceVersions: rotatedVersions,
	}, nil)
	if err != nil {
		t.Fatalf("render values after Secret rotation: %v", err)
	}
	rotatedAnnotation := rotatedValues["podAnnotations"].(map[string]interface{})[secretResourceVersionsAnnotation].(string)
	if rotatedAnnotation == annotations[secretResourceVersionsAnnotation] {
		t.Error("Secret rotation did not change the pod-template annotation")
	}
	decodedVersions := map[string]string{}
	if err := json.Unmarshal([]byte(rotatedAnnotation), &decodedVersions); err != nil {
		t.Fatalf("unmarshal rotated Secret annotation: %v", err)
	}
	if decodedVersions["credentials"] != rotated.ResourceVersion {
		t.Errorf("rotated credentials resource version = %q, want %q", decodedVersions["credentials"], rotated.ResourceVersion)
	}
}

func TestReferencedSecretResourceVersionsRejectsInvalidSecretName(t *testing.T) {
	scheme := secretReferenceTestScheme(t)
	countingClient := &getCountingClient{Client: fake.NewClientBuilder().WithScheme(scheme).Build()}
	reconciler := &MLflowReconciler{Client: countingClient}
	mlflow := &mlflowv1.MLflow{Spec: mlflowv1.MLflowSpec{
		BackendStoreURIFrom: secretSelector("not/a-secret-name"),
	}}

	_, err := reconciler.referencedSecretResourceVersions(context.Background(), mlflow, "applications")
	if err == nil {
		t.Fatal("referencedSecretResourceVersions() succeeded for an invalid Secret name")
	}
	if countingClient.getCalls != 0 {
		t.Errorf("client.Get() called %d times for an invalid Secret name, want 0", countingClient.getCalls)
	}
}

func TestRenderChart_SecretResourceVersionsAnnotation(t *testing.T) {
	mlflow := &mlflowv1.MLflow{ObjectMeta: metav1.ObjectMeta{Name: ResourceName}, Spec: mlflowv1.MLflowSpec{
		BackendStoreURI: ptr(testBackendStoreURI),
		PodAnnotations:  map[string]string{"example.com/keep": "true"},
	}}
	renderer := NewHelmRenderer("../../charts/mlflow")
	objects, err := renderer.RenderChart(mlflow, "applications", RenderOptions{
		ReferencedSecretResourceVersions: map[string]string{
			"credentials": "17",
			TLSSecretName: "23",
		},
	}, nil)
	if err != nil {
		t.Fatalf("RenderChart() error = %v", err)
	}

	deployment := findObject(objects, deploymentKind, ResourceName)
	if deployment == nil {
		t.Fatal("rendered Deployment not found")
	}
	annotations, found, err := unstructured.NestedStringMap(deployment.Object, "spec", "template", "metadata", "annotations")
	if err != nil || !found {
		t.Fatalf("rendered pod annotations not found: found=%v err=%v", found, err)
	}
	if got, want := annotations[secretResourceVersionsAnnotation], `{"credentials":"17","mlflow-tls":"23"}`; got != want {
		t.Errorf("rendered Secret resource-version annotation = %q, want %q", got, want)
	}
	if got := annotations["example.com/keep"]; got != "true" {
		t.Errorf("rendered user annotation = %q, want true", got)
	}
}

func secretSelector(name string) *corev1.SecretKeySelector {
	return &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: name}, Key: "value"}
}

type getCountingClient struct {
	client.Client
	getCalls int
}

func (c *getCountingClient) Get(
	ctx context.Context,
	key client.ObjectKey,
	obj client.Object,
	opts ...client.GetOption,
) error {
	c.getCalls++
	return c.Client.Get(ctx, key, obj, opts...)
}

func secretReferenceTestScheme(t *testing.T) *runtime.Scheme {
	t.Helper()
	scheme := runtime.NewScheme()
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	if err := mlflowv1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	return scheme
}

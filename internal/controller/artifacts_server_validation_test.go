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
	"strings"
	"testing"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

type countingReader struct {
	client.Reader
	gets int
}

func (r *countingReader) Get(ctx context.Context, key client.ObjectKey, obj client.Object, opts ...client.GetOption) error {
	r.gets++
	return r.Reader.Get(ctx, key, obj, opts...)
}

type errorReader struct {
	client.Reader
	err error
}

func (r *errorReader) Get(context.Context, client.ObjectKey, client.Object, ...client.GetOption) error {
	return r.err
}

func artifactsServerMLflow() *mlflowv1.MLflow {
	return &mlflowv1.MLflow{
		Spec: mlflowv1.MLflowSpec{
			ArtifactsServer: &mlflowv1.ArtifactsServerSpec{Enabled: true},
		},
	}
}

func secretReader(t *testing.T, objects ...client.Object) client.Reader {
	t.Helper()
	scheme := runtime.NewScheme()
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	return fake.NewClientBuilder().WithScheme(scheme).WithObjects(objects...).Build()
}

func TestValidateArtifactsServerMetadataStores(t *testing.T) {
	ctx := context.Background()

	t.Run("does not inspect metadata stores when dedicated serving is disabled", func(t *testing.T) {
		mlflow := &mlflowv1.MLflow{Spec: mlflowv1.MLflowSpec{
			BackendStoreURIFrom: metadataStoreSecretSelector("missing", "uri", false),
		}}

		if err := (&MLflowReconciler{}).validateArtifactsServerMetadataStores(ctx, mlflow, "opendatahub"); err != nil {
			t.Fatalf("validateArtifactsServerMetadataStores() error = %v", err)
		}
	})

	t.Run("rejects the legacy implicit SQLite primary store", func(t *testing.T) {
		err := (&MLflowReconciler{}).validateArtifactsServerMetadataStores(ctx, artifactsServerMLflow(), "opendatahub")
		if err == nil || !strings.Contains(err.Error(), "effective backend metadata store must be remote") {
			t.Fatalf("validateArtifactsServerMetadataStores() error = %v, want missing backend rejection", err)
		}
	})

	t.Run("rejects unsupported inline stores", func(t *testing.T) {
		mlflow := artifactsServerMLflow()
		mlflow.Spec.BackendStoreURI = ptr("s3://bucket/not-a-metadata-store")

		err := (&MLflowReconciler{}).validateArtifactsServerMetadataStores(ctx, mlflow, "opendatahub")
		if err == nil || !strings.Contains(err.Error(), "effective backend metadata store must be remote") {
			t.Fatalf("validateArtifactsServerMetadataStores() error = %v, want unsupported store rejection", err)
		}
	})

	t.Run("accepts inline remote SQL stores", func(t *testing.T) {
		mlflow := artifactsServerMLflow()
		mlflow.Spec.BackendStoreURI = ptr("postgresql://db.example.com/mlflow")
		mlflow.Spec.RegistryStoreURI = ptr("mysql+pymysql://db.example.com/registry")
		mlflow.Spec.ReadReplicaBackendStoreURI = ptr("postgresql+psycopg2://replica.example.com/mlflow")

		err := (&MLflowReconciler{}).validateArtifactsServerMetadataStores(ctx, mlflow, "opendatahub")
		if err != nil {
			t.Fatalf("validateArtifactsServerMetadataStores() error = %v", err)
		}
	})

	t.Run("validates the effective backend and remaining shared Secret references", func(t *testing.T) {
		secret := &corev1.Secret{
			ObjectMeta: metav1.ObjectMeta{Name: "database-uris", Namespace: "opendatahub"},
			Data: map[string][]byte{
				"backend":  []byte("postgresql://db.example.com/mlflow"),
				"registry": []byte("mysql://db.example.com/registry"),
				"replica":  []byte("postgresql://replica.example.com/mlflow"),
			},
		}
		reader := &countingReader{Reader: secretReader(t, secret)}
		mlflow := artifactsServerMLflow()
		mlflow.Spec.BackendStoreURIFrom = metadataStoreSecretSelector("database-uris", "backend", false)
		mlflow.Spec.RegistryStoreURIFrom = metadataStoreSecretSelector("database-uris", "registry", false)
		mlflow.Spec.ReadReplicaBackendStoreURIFrom = metadataStoreSecretSelector("database-uris", "replica", false)

		err := (&MLflowReconciler{APIReader: reader}).validateArtifactsServerMetadataStores(ctx, mlflow, "opendatahub")
		if err != nil {
			t.Fatalf("validateArtifactsServerMetadataStores() error = %v", err)
		}
		if reader.gets != 2 {
			t.Fatalf("Secret Get calls = %d, want 2", reader.gets)
		}
	})

	for _, test := range []struct {
		name      string
		field     string
		configure func(*mlflowv1.MLflowSpec, *corev1.SecretKeySelector)
	}{
		{
			name:  "backend Secret contains SQLite",
			field: "backendStoreUri",
			configure: func(spec *mlflowv1.MLflowSpec, selector *corev1.SecretKeySelector) {
				spec.BackendStoreURIFrom = selector
			},
		},
		{
			name:  "registry Secret contains SQLite",
			field: "registryStoreUri",
			configure: func(spec *mlflowv1.MLflowSpec, selector *corev1.SecretKeySelector) {
				spec.BackendStoreURI = ptr("postgresql://db.example.com/mlflow")
				spec.RegistryStoreURIFrom = selector
			},
		},
		{
			name:  "read replica Secret contains SQLite",
			field: "readReplicaBackendStoreUri",
			configure: func(spec *mlflowv1.MLflowSpec, selector *corev1.SecretKeySelector) {
				spec.BackendStoreURI = ptr("postgresql://db.example.com/mlflow")
				spec.ReadReplicaBackendStoreURIFrom = selector
			},
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			secret := &corev1.Secret{
				ObjectMeta: metav1.ObjectMeta{Name: "database-uri", Namespace: "opendatahub"},
				Data:       map[string][]byte{"uri": []byte("sqlite://user:do-not-log@/mlflow.db")},
			}
			mlflow := artifactsServerMLflow()
			test.configure(&mlflow.Spec, metadataStoreSecretSelector("database-uri", "uri", false))

			err := (&MLflowReconciler{APIReader: secretReader(t, secret)}).
				validateArtifactsServerMetadataStores(ctx, mlflow, "opendatahub")
			want := "spec." + test.field + " must resolve to a remote PostgreSQL or MySQL URI"
			if test.field == "backendStoreUri" {
				want = "effective backend metadata store must be remote"
			}
			if err == nil || !strings.Contains(err.Error(), want) {
				t.Fatalf("validateArtifactsServerMetadataStores() error = %v, want %s rejection", err, test.field)
			}
			if strings.Contains(err.Error(), "do-not-log") {
				t.Fatalf("validation error exposed Secret contents: %v", err)
			}
		})
	}
}

func TestValidateArtifactsServerMetadataStoreSecretFailures(t *testing.T) {
	ctx := context.Background()

	t.Run("rejects a missing key even when the selector is optional", func(t *testing.T) {
		secret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "database-uri", Namespace: "opendatahub"}}
		mlflow := artifactsServerMLflow()
		mlflow.Spec.BackendStoreURIFrom = metadataStoreSecretSelector("database-uri", "uri", true)

		err := (&MLflowReconciler{APIReader: secretReader(t, secret)}).
			validateArtifactsServerMetadataStores(ctx, mlflow, "opendatahub")
		if err == nil || !strings.Contains(err.Error(), "effective backend Secret reference is absent or empty") {
			t.Fatalf("validateArtifactsServerMetadataStores() error = %v, want missing key", err)
		}
	})

	t.Run("returns Secret lookup errors", func(t *testing.T) {
		mlflow := artifactsServerMLflow()
		mlflow.Spec.BackendStoreURIFrom = metadataStoreSecretSelector("database-uri", "uri", false)
		forbidden := apierrors.NewForbidden(schema.GroupResource{Resource: "secrets"}, "database-uri", nil)
		reconciler := &MLflowReconciler{APIReader: &errorReader{err: forbidden}}

		err := reconciler.validateArtifactsServerMetadataStores(ctx, mlflow, "opendatahub")
		if err == nil || !apierrors.IsForbidden(err) {
			t.Fatalf("validateArtifactsServerMetadataStores() error = %v, want Forbidden", err)
		}
	})

	t.Run("rejects a missing Secret", func(t *testing.T) {
		mlflow := artifactsServerMLflow()
		mlflow.Spec.BackendStoreURIFrom = metadataStoreSecretSelector("missing", "uri", false)

		err := (&MLflowReconciler{APIReader: secretReader(t)}).
			validateArtifactsServerMetadataStores(ctx, mlflow, "opendatahub")
		if err == nil || !apierrors.IsNotFound(err) {
			t.Fatalf("validateArtifactsServerMetadataStores() error = %v, want NotFound", err)
		}
	})
}

func metadataStoreSecretSelector(name, key string, optional bool) *corev1.SecretKeySelector {
	return &corev1.SecretKeySelector{
		LocalObjectReference: corev1.LocalObjectReference{Name: name},
		Key:                  key,
		Optional:             ptr(optional),
	}
}

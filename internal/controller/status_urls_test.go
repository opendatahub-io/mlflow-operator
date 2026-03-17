package controller

import (
	"testing"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	"github.com/opendatahub-io/mlflow-operator/internal/config"
)

func TestBuildStatusURL(t *testing.T) {
	tests := []struct {
		name       string
		mlflowName string
		baseURL    string
		configured bool
		want       string
	}{
		{
			name:       "default CR name",
			mlflowName: "mlflow",
			baseURL:    "https://gateway.example.com",
			configured: true,
			want:       "https://gateway.example.com/mlflow",
		},
		{
			name:       "custom CR name",
			mlflowName: "dev",
			baseURL:    "https://gateway.example.com",
			configured: true,
			want:       "https://gateway.example.com/mlflow-dev",
		},
		{
			name:       "trailing slash is trimmed",
			mlflowName: "mlflow",
			baseURL:    "https://gateway.example.com/",
			configured: true,
			want:       "https://gateway.example.com/mlflow",
		},
		{
			name:       "existing base path is preserved",
			mlflowName: "mlflow",
			baseURL:    "https://gateway.example.com/base",
			configured: true,
			want:       "https://gateway.example.com/base/mlflow",
		},
		{
			name:       "empty base URL",
			mlflowName: "mlflow",
			baseURL:    "",
			configured: false,
			want:       "",
		},
		{
			name:       "default placeholder URL is omitted when unset",
			mlflowName: "mlflow",
			baseURL:    config.DefaultMLflowURL,
			configured: false,
			want:       "",
		},
		{
			name:       "default placeholder URL is preserved when explicitly configured",
			mlflowName: "mlflow",
			baseURL:    config.DefaultMLflowURL,
			configured: true,
			want:       "https://mlflow.example.com/mlflow",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := buildStatusURL(tt.mlflowName, tt.baseURL, tt.configured)
			if got != tt.want {
				t.Fatalf("buildStatusURL() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestBuildStatusAddress(t *testing.T) {
	tests := []struct {
		name       string
		mlflowName string
		namespace  string
		wantURL    string
		wantNil    bool
	}{
		{
			name:       "default CR name",
			mlflowName: "mlflow",
			namespace:  "opendatahub",
			wantURL:    "https://mlflow.opendatahub.svc:8443",
		},
		{
			name:       "custom CR name",
			mlflowName: "dev",
			namespace:  "test-ns",
			wantURL:    "https://mlflow-dev.test-ns.svc:8443",
		},
		{
			name:       "empty namespace returns nil",
			mlflowName: "mlflow",
			namespace:  "",
			wantNil:    true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := buildStatusAddress(tt.mlflowName, tt.namespace)
			if tt.wantNil {
				if got != nil {
					t.Fatalf("buildStatusAddress() = %#v, want nil", got)
				}
				return
			}
			if got == nil {
				t.Fatal("buildStatusAddress() = nil, want address")
			}
			if got.URL != tt.wantURL {
				t.Fatalf("buildStatusAddress().URL = %q, want %q", got.URL, tt.wantURL)
			}
		})
	}
}

func TestSetObservedURLs(t *testing.T) {
	t.Run("public route unavailable", func(t *testing.T) {
		mlflow := &mlflowv1.MLflow{
			ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		}

		setObservedURLs(mlflow, "opendatahub", false, &config.OperatorConfig{
			MLflowURL:           "https://gateway.example.com",
			MLflowURLConfigured: true,
		})

		if mlflow.Status.URL != "" {
			t.Fatalf("status.URL = %q, want empty when public route is unavailable", mlflow.Status.URL)
		}
		if mlflow.Status.Address == nil || mlflow.Status.Address.URL != "https://mlflow.opendatahub.svc:8443" {
			t.Fatalf("status.Address = %#v, want internal service URL", mlflow.Status.Address)
		}
	})

	t.Run("public route available", func(t *testing.T) {
		mlflow := &mlflowv1.MLflow{
			ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
		}

		setObservedURLs(mlflow, "opendatahub", true, &config.OperatorConfig{
			MLflowURL:           "https://gateway.example.com",
			MLflowURLConfigured: true,
		})

		if mlflow.Status.URL != "https://gateway.example.com/mlflow" {
			t.Fatalf("status.URL = %q, want public status URL", mlflow.Status.URL)
		}
		if mlflow.Status.Address == nil || mlflow.Status.Address.URL != "https://mlflow.opendatahub.svc:8443" {
			t.Fatalf("status.Address = %#v, want internal service URL", mlflow.Status.Address)
		}
	})
}

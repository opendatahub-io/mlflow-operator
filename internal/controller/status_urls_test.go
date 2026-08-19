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

func TestBuildArtifactsURL(t *testing.T) {
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
			baseURL:    "https://gateway.example.com/",
			configured: true,
			want:       "https://gateway.example.com/mlflow-artifacts/api/2.0/mlflow-artifacts/artifacts",
		},
		{
			name:       "custom CR name and existing base path",
			mlflowName: "dev",
			baseURL:    "https://gateway.example.com/base",
			configured: true,
			want:       "https://gateway.example.com/base/mlflow-artifacts-dev/api/2.0/mlflow-artifacts/artifacts",
		},
		{
			name:       "unconfigured URL",
			mlflowName: "mlflow",
			baseURL:    config.DefaultMLflowURL,
			configured: false,
			want:       "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := buildArtifactsURL(tt.mlflowName, tt.baseURL, tt.configured); got != tt.want {
				t.Fatalf("buildArtifactsURL() = %q, want %q", got, tt.want)
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
			wantURL:    "https://mlflow.opendatahub.svc:8443/mlflow",
		},
		{
			name:       "custom CR name",
			mlflowName: "dev",
			namespace:  "test-ns",
			wantURL:    "https://mlflow-dev.test-ns.svc:8443/mlflow",
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
	configured := &config.OperatorConfig{
		MLflowURL:           "https://gateway.example.com",
		MLflowURLConfigured: true,
	}
	tests := []struct {
		name                 string
		publicRouteAvailable bool
		cfg                  *config.OperatorConfig
		artifactsEnabled     bool
		wantURL              string
		wantArtifactsURL     string
	}{
		{
			name:                 "public route unavailable",
			publicRouteAvailable: false,
			cfg:                  configured,
			artifactsEnabled:     true,
		},
		{
			name:                 "tracking route available",
			publicRouteAvailable: true,
			cfg:                  configured,
			wantURL:              "https://gateway.example.com/mlflow",
		},
		{
			name:                 "dedicated artifacts route available",
			publicRouteAvailable: true,
			cfg:                  configured,
			artifactsEnabled:     true,
			wantURL:              "https://gateway.example.com/mlflow",
			wantArtifactsURL:     "https://gateway.example.com/mlflow-artifacts/api/2.0/mlflow-artifacts/artifacts",
		},
		{
			name:                 "operator config unavailable",
			publicRouteAvailable: true,
			artifactsEnabled:     true,
		},
		{
			name:                 "base URL not explicitly configured",
			publicRouteAvailable: true,
			cfg: &config.OperatorConfig{
				MLflowURL: config.DefaultMLflowURL,
			},
			artifactsEnabled: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			mlflow := &mlflowv1.MLflow{
				ObjectMeta: metav1.ObjectMeta{Name: "mlflow"},
				Status: mlflowv1.MLflowStatus{
					URL:          "https://stale.example.com/mlflow",
					ArtifactsURL: "https://stale.example.com/mlflow-artifacts",
				},
			}
			if tt.artifactsEnabled {
				mlflow.Spec.ArtifactsServer = &mlflowv1.ArtifactsServerSpec{Enabled: true}
			}

			setObservedURLs(mlflow, "opendatahub", tt.publicRouteAvailable, tt.cfg)

			if mlflow.Status.URL != tt.wantURL {
				t.Errorf("status.URL = %q, want %q", mlflow.Status.URL, tt.wantURL)
			}
			if mlflow.Status.ArtifactsURL != tt.wantArtifactsURL {
				t.Errorf("status.ArtifactsURL = %q, want %q", mlflow.Status.ArtifactsURL, tt.wantArtifactsURL)
			}
			if mlflow.Status.Address == nil || mlflow.Status.Address.URL != "https://mlflow.opendatahub.svc:8443/mlflow" {
				t.Errorf("status.Address = %#v, want internal service URL", mlflow.Status.Address)
			}
		})
	}
}

package main

import (
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/opendatahub-io/mlflow-operator/internal/config"
)

func TestValidateStartupConfig(t *testing.T) {
	tests := []struct {
		name                   string
		namespace              string
		cfg                    *config.OperatorConfig
		supportedMLflowVersion string
		wantErr                bool
	}{
		{
			name:                   "accepts required values",
			namespace:              "opendatahub",
			cfg:                    &config.OperatorConfig{MLflowImage: "quay.io/example/mlflow:test"},
			supportedMLflowVersion: "3.11.0",
			wantErr:                false,
		},
		{
			name:                   "rejects empty namespace",
			namespace:              "",
			cfg:                    &config.OperatorConfig{MLflowImage: "quay.io/example/mlflow:test"},
			supportedMLflowVersion: "3.11.0",
			wantErr:                true,
		},
		{
			name:                   "rejects missing MLflow image",
			namespace:              "opendatahub",
			cfg:                    &config.OperatorConfig{},
			supportedMLflowVersion: "3.11.0",
			wantErr:                true,
		},
		{
			name:                   "rejects missing supported version",
			namespace:              "opendatahub",
			cfg:                    &config.OperatorConfig{MLflowImage: "quay.io/example/mlflow:test"},
			supportedMLflowVersion: "",
			wantErr:                true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := validateStartupConfig(tt.namespace, tt.cfg, tt.supportedMLflowVersion)
			if tt.wantErr && err == nil {
				t.Fatal("expected error, got nil")
			}
			if !tt.wantErr && err != nil {
				t.Fatalf("expected no error, got %v", err)
			}
		})
	}
}

func TestResolveManagerNamespace(t *testing.T) {
	tests := []struct {
		name              string
		namespace         string
		operatorConfig    *config.OperatorConfig
		expectedNamespace string
	}{
		{
			name:      "keeps legacy namespace when toggle disabled",
			namespace: "opendatahub",
			operatorConfig: &config.OperatorConfig{
				ApplicationsNamespace:                "redhat-ods-applications",
				EnableMLflowOperatorModuleController: false,
			},
			expectedNamespace: "opendatahub",
		},
		{
			name:      "uses applications namespace when toggle enabled",
			namespace: "opendatahub",
			operatorConfig: &config.OperatorConfig{
				ApplicationsNamespace:                "redhat-ods-applications",
				EnableMLflowOperatorModuleController: true,
			},
			expectedNamespace: "redhat-ods-applications",
		},
		{
			name:      "falls back when applications namespace empty",
			namespace: "opendatahub",
			operatorConfig: &config.OperatorConfig{
				EnableMLflowOperatorModuleController: true,
			},
			expectedNamespace: "opendatahub",
		},
		{
			name:              "falls back when config missing",
			namespace:         "opendatahub",
			operatorConfig:    nil,
			expectedNamespace: "opendatahub",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := resolveManagerNamespace(tt.namespace, tt.operatorConfig); got != tt.expectedNamespace {
				t.Fatalf("resolveManagerNamespace() = %q, want %q", got, tt.expectedNamespace)
			}
		})
	}
}

func TestWaitForRequiredCRDReturnsImmediatelyWhenAvailable(t *testing.T) {
	calls := 0
	err := waitForMLflowOperatorCRD(20*time.Millisecond, time.Millisecond, func() (bool, error) {
		calls++
		return true, nil
	})
	if err != nil {
		t.Fatalf("waitForRequiredCRD() unexpected error: %v", err)
	}
	if calls != 1 {
		t.Fatalf("expected a single availability check, got %d", calls)
	}
}

func TestWaitForRequiredCRDRetriesUntilAvailable(t *testing.T) {
	calls := 0
	err := waitForMLflowOperatorCRD(50*time.Millisecond, time.Millisecond, func() (bool, error) {
		calls++
		switch calls {
		case 1:
			return false, errors.New("temporary discovery failure")
		case 2:
			return false, nil
		default:
			return true, nil
		}
	})
	if err != nil {
		t.Fatalf("waitForRequiredCRD() unexpected error: %v", err)
	}
	if calls < 3 {
		t.Fatalf("expected retries before availability, got %d calls", calls)
	}
}

func TestWaitForRequiredCRDTimesOutWhenUnavailable(t *testing.T) {
	err := waitForMLflowOperatorCRD(10*time.Millisecond, time.Millisecond, func() (bool, error) {
		return false, nil
	})
	if err == nil {
		t.Fatalf("expected timeout error when CRD never becomes available")
	}
	if !strings.Contains(err.Error(), "did not become available within") {
		t.Fatalf("expected timeout message, got %v", err)
	}
}

func TestWaitForRequiredCRDRejectsNonPositiveTimeout(t *testing.T) {
	err := waitForMLflowOperatorCRD(0, time.Millisecond, func() (bool, error) {
		return true, nil
	})
	if err == nil {
		t.Fatalf("expected error for non-positive timeout")
	}
	if !strings.Contains(err.Error(), "greater than zero") {
		t.Fatalf("expected validation message, got %v", err)
	}
}

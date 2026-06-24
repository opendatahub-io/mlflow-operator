package main

import (
	"testing"

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

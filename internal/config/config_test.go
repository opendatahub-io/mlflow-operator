package config

import (
	"os"
	"testing"
	"time"

	"github.com/spf13/viper"
)

func TestLoadConfigPrefersModularInputs(t *testing.T) {
	t.Setenv("RELATED_IMAGE_ODH_MLFLOW_IMAGE", "registry.example.com/mlflow@sha256:123")
	t.Setenv("MLFLOW_IMAGE", "quay.io/opendatahub/mlflow:legacy")
	t.Setenv("APPLICATIONS_NAMESPACE", "redhat-ods-applications")
	t.Setenv("ENABLE_MLFLOW_OPERATOR_MODULE_CONTROLLER", "true")
	t.Setenv("MLFLOW_OPERATOR_MODULE_CONTROLLER_CRD_WAIT_TIMEOUT", "45s")

	cfg := loadConfig(newTestViper(), os.LookupEnv)

	if cfg.MLflowImage != "registry.example.com/mlflow@sha256:123" {
		t.Fatalf("expected modular image to win, got %q", cfg.MLflowImage)
	}
	if cfg.ApplicationsNamespace != "redhat-ods-applications" {
		t.Fatalf("expected applications namespace override, got %q", cfg.ApplicationsNamespace)
	}
	if !cfg.EnableMLflowOperatorModuleController {
		t.Fatalf("expected rollout toggle to be enabled")
	}
	if cfg.MLflowOperatorCRDWaitTimeout != 45*time.Second {
		t.Fatalf("expected CRD wait timeout override, got %s", cfg.MLflowOperatorCRDWaitTimeout)
	}
}

func TestLoadConfigFallsBackToLegacyInputs(t *testing.T) {
	t.Setenv("MLFLOW_IMAGE", "quay.io/opendatahub/mlflow:legacy")

	cfg := loadConfig(newTestViper(), os.LookupEnv)

	if cfg.MLflowImage != "quay.io/opendatahub/mlflow:legacy" {
		t.Fatalf("expected legacy image fallback, got %q", cfg.MLflowImage)
	}
	if cfg.EnableMLflowOperatorModuleController {
		t.Fatalf("expected rollout toggle to default to disabled")
	}
	if cfg.MLflowURLConfigured {
		t.Fatalf("expected MLFLOW_URL to remain unconfigured when unset")
	}
	if cfg.MLflowOperatorCRDWaitTimeout != DefaultMLflowOperatorCRDWaitTimeout {
		t.Fatalf("expected default CRD wait timeout %s, got %s", DefaultMLflowOperatorCRDWaitTimeout, cfg.MLflowOperatorCRDWaitTimeout)
	}
}

func newTestViper() *viper.Viper {
	v := viper.New()
	v.AutomaticEnv()
	v.SetDefault("GATEWAY_NAME", "data-science-gateway")
	v.SetDefault("MLFLOW_URL", DefaultMLflowURL)
	v.SetDefault("SECTION_TITLE", "MLflow")
	v.SetDefault("APPLICATIONS_NAMESPACE", "")
	v.SetDefault("ENABLE_MLFLOW_OPERATOR_MODULE_CONTROLLER", false)
	v.SetDefault("MLFLOW_OPERATOR_MODULE_CONTROLLER_CRD_WAIT_TIMEOUT", DefaultMLflowOperatorCRDWaitTimeout)
	return v
}

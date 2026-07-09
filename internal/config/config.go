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

package config

import (
	"os"
	"sync"
	"time"

	"github.com/spf13/viper"
)

const (
	DefaultMLflowURL                    = "https://mlflow.example.com"
	DefaultMLflowOperatorCRDWaitTimeout = 30 * time.Second
	DefaultAuthCRDWaitTimeout           = 30 * time.Second
)

// OperatorConfig holds the configuration for the MLflow operator
type OperatorConfig struct {
	// ApplicationsNamespace is the namespace where MLflow operands are created.
	ApplicationsNamespace string
	// EnableMLflowOperatorModuleController turns on the new MLflowOperator controller path.
	EnableMLflowOperatorModuleController bool
	// MLflowOperatorCRDWaitTimeout bounds how long startup waits for the MLflowOperator CRD
	// after the module-controller path has been explicitly enabled.
	MLflowOperatorCRDWaitTimeout time.Duration
	// MLflowImage is the default image to use for MLflow deployments
	MLflowImage string
	// GatewayName is the name of the Gateway resource for HttpRoute
	GatewayName string
	// MLflowURL is the external URL for accessing MLflow
	MLflowURL string
	// MLflowURLConfigured reports whether MLFLOW_URL was explicitly configured.
	MLflowURLConfigured bool
	// SectionTitle is the title for the ConsoleLink section in OpenShift console
	SectionTitle string
	// EnableNamespaceRBAC turns on the namespace RBAC controller for workspace RoleBindings
	EnableNamespaceRBAC bool
	// AuthCRDWaitTimeout bounds how long startup waits for the Auth CRD
	// after the namespace RBAC controller has been explicitly enabled.
	AuthCRDWaitTimeout time.Duration
	// ResourceNamePrefix is the kustomize namePrefix applied to cluster-scoped resources at deploy time
	ResourceNamePrefix string
}

var (
	instance *OperatorConfig
	once     sync.Once
)

type envLookupFn func(string) (string, bool)

func loadConfig(v *viper.Viper, lookupEnv envLookupFn) *OperatorConfig {
	_, mlflowURLConfigured := lookupEnv("MLFLOW_URL")

	// RELATED_IMAGE_* is the platform override. MLFLOW_IMAGE remains the
	// operator's built-in default image fallback rather than a legacy-only path.
	mlflowImage := v.GetString("RELATED_IMAGE_ODH_MLFLOW_IMAGE")
	if mlflowImage == "" {
		mlflowImage = v.GetString("MLFLOW_IMAGE")
	}

	return &OperatorConfig{
		ApplicationsNamespace:                v.GetString("APPLICATIONS_NAMESPACE"),
		EnableMLflowOperatorModuleController: v.GetBool("ENABLE_MLFLOW_OPERATOR_MODULE_CONTROLLER"),
		MLflowOperatorCRDWaitTimeout:         v.GetDuration("MLFLOW_OPERATOR_MODULE_CONTROLLER_CRD_WAIT_TIMEOUT"),
		MLflowImage:                          mlflowImage,
		GatewayName:                          v.GetString("GATEWAY_NAME"),
		MLflowURL:                            v.GetString("MLFLOW_URL"),
		MLflowURLConfigured:                  mlflowURLConfigured,
		SectionTitle:                         v.GetString("SECTION_TITLE"),
		EnableNamespaceRBAC:                  v.GetBool("ENABLE_NAMESPACE_RBAC"),
		AuthCRDWaitTimeout:                   v.GetDuration("AUTH_CRD_WAIT_TIMEOUT"),
		ResourceNamePrefix:                   v.GetString("RESOURCE_NAME_PREFIX"),
	}
}

// GetConfig returns the singleton operator configuration
// It reads from environment variables using viper
func GetConfig() *OperatorConfig {
	once.Do(func() {
		v := viper.New()
		v.AutomaticEnv()

		// Set defaults (these can be overridden by env vars)
		v.SetDefault("GATEWAY_NAME", "data-science-gateway")
		v.SetDefault("MLFLOW_URL", DefaultMLflowURL)
		v.SetDefault("SECTION_TITLE", "MLflow")
		v.SetDefault("APPLICATIONS_NAMESPACE", "")
		v.SetDefault("ENABLE_MLFLOW_OPERATOR_MODULE_CONTROLLER", false)
		v.SetDefault("MLFLOW_OPERATOR_MODULE_CONTROLLER_CRD_WAIT_TIMEOUT", DefaultMLflowOperatorCRDWaitTimeout)
		v.SetDefault("ENABLE_NAMESPACE_RBAC", false)
		v.SetDefault("AUTH_CRD_WAIT_TIMEOUT", DefaultAuthCRDWaitTimeout)
		v.SetDefault("RESOURCE_NAME_PREFIX", "mlflow-operator-")

		instance = loadConfig(v, os.LookupEnv)
	})
	return instance
}

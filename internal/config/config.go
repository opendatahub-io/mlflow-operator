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
	"sync"

	"github.com/spf13/viper"
)

// OperatorConfig holds the configuration for the MLflow operator
type OperatorConfig struct {
	// MLflowImage is the default image to use for MLflow deployments
	MLflowImage string
	// KubeAuthProxyImage is the default image to use for the kube-rbac-proxy sidecar
	KubeAuthProxyImage string
}

var (
	instance *OperatorConfig
	once     sync.Once
)

// GetConfig returns the singleton operator configuration
// It reads from environment variables using viper
func GetConfig() *OperatorConfig {
	once.Do(func() {
		v := viper.New()
		v.AutomaticEnv()

		// Set defaults (these can be overridden by env vars)
		v.SetDefault("MLFLOW_IMAGE", "quay.io/opendatahub/mlflow:main")
		v.SetDefault("KUBE_AUTH_PROXY_IMAGE", "quay.io/opendatahub/odh-kube-auth-proxy:latest")

		instance = &OperatorConfig{
			MLflowImage:        v.GetString("MLFLOW_IMAGE"),
			KubeAuthProxyImage: v.GetString("KUBE_AUTH_PROXY_IMAGE"),
		}
	})
	return instance
}

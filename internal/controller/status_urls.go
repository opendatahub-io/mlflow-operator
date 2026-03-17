package controller

import (
	"fmt"
	"strings"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	"github.com/opendatahub-io/mlflow-operator/internal/config"
)

const mlflowServicePort = 8443

func buildStatusURL(mlflowName, baseURL string, baseURLConfigured bool) string {
	baseURL = strings.TrimRight(baseURL, "/")
	if baseURL == "" || !baseURLConfigured {
		return ""
	}

	return fmt.Sprintf("%s/%s%s", baseURL, ResourceName, getResourceSuffix(mlflowName))
}

func buildStatusAddress(mlflowName, namespace string) *mlflowv1.MLflowAddressStatus {
	if namespace == "" {
		return nil
	}

	serviceName := ResourceName + getResourceSuffix(mlflowName)
	return &mlflowv1.MLflowAddressStatus{
		URL: fmt.Sprintf("https://%s.%s.svc:%d", serviceName, namespace, mlflowServicePort),
	}
}

func setObservedURLs(mlflow *mlflowv1.MLflow, namespace string, publicRouteAvailable bool, cfg *config.OperatorConfig) {
	mlflow.Status.Address = buildStatusAddress(mlflow.Name, namespace)

	if publicRouteAvailable && cfg != nil {
		mlflow.Status.URL = buildStatusURL(mlflow.Name, cfg.MLflowURL, cfg.MLflowURLConfigured)
	} else {
		mlflow.Status.URL = ""
	}
}

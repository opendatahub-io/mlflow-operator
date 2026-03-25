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

package controller

import "k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"

const (
	deploymentKind      = "Deployment"
	testBackendStoreURI = "postgresql://db-host:5432/mlflow"

	// CA bundle test constants - these match values from values.yaml and deployment.yaml
	caCombinedVolume = "combined-ca-bundle"
	caCombinedBundle = "/etc/pki/tls/certs/combined/ca-bundle.crt"
)

func findObject(objs []*unstructured.Unstructured, kind, name string) *unstructured.Unstructured {
	for _, obj := range objs {
		if obj.GetKind() == kind && obj.GetName() == name {
			return obj
		}
	}
	return nil
}

func collectEgressPorts(egressRules []interface{}) []int64 {
	var ports []int64
	for _, rule := range egressRules {
		ruleMap := rule.(map[string]interface{})
		rulePorts, ok := ruleMap["ports"].([]interface{})
		if !ok {
			continue
		}
		for _, p := range rulePorts {
			portMap := p.(map[string]interface{})
			if port, ok := portMap["port"]; ok {
				ports = append(ports, port.(int64))
			}
		}
	}
	return ports
}

func ptr[T any](v T) *T {
	return &v
}

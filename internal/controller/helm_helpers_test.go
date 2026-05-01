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

import (
	"strconv"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
)

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
		ruleMap, ok := rule.(map[string]interface{})
		if !ok {
			continue
		}
		rulePorts, ok := ruleMap["ports"].([]interface{})
		if !ok {
			continue
		}
		for _, p := range rulePorts {
			portMap, ok := p.(map[string]interface{})
			if !ok {
				continue
			}
			if port, ok := parsePortValue(portMap["port"]); ok {
				ports = append(ports, port)
			}
		}
	}
	return ports
}

func findEgressRulesByPort(egressRules []interface{}, port int64) []map[string]interface{} {
	var matches []map[string]interface{}
	for _, rule := range egressRules {
		ruleMap, ok := rule.(map[string]interface{})
		if !ok {
			continue
		}
		rulePorts, ok := ruleMap["ports"].([]interface{})
		if !ok {
			continue
		}
		for _, p := range rulePorts {
			portMap, ok := p.(map[string]interface{})
			if !ok {
				continue
			}
			if rulePort, ok := parsePortValue(portMap["port"]); ok && rulePort == port {
				matches = append(matches, ruleMap)
				break
			}
		}
	}
	return matches
}

func parsePortValue(v interface{}) (int64, bool) {
	switch port := v.(type) {
	case int:
		return int64(port), true
	case int32:
		return int64(port), true
	case int64:
		return port, true
	case float64:
		return int64(port), true
	case string:
		parsed, err := strconv.ParseInt(port, 10, 64)
		if err != nil {
			return 0, false
		}
		return parsed, true
	default:
		return 0, false
	}
}

func ptr[T any](v T) *T {
	return &v
}

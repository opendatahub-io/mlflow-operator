/*
Copyright 2026.

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
	"testing"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
)

func TestSupportedMLflowServesPrefixedOTLP(t *testing.T) {
	original := SupportedMLflowVersion
	t.Cleanup(func() { SupportedMLflowVersion = original })
	for _, tc := range []struct {
		version string
		want    bool
	}{
		{"v3.15.2", false},
		{"v3.16.0", true},
		{"v3.16.2.dev0", true},
		{"3.16.2-dev.0", true},
		{"v3.17.0-rc.1", true},
		{"v3.16.2+rhaiv.1", true},
		{"", false},
		{"invalid", false},
	} {
		t.Run(tc.version, func(t *testing.T) {
			SupportedMLflowVersion = tc.version
			if got := supportedMLflowServesPrefixedOTLP(); got != tc.want {
				t.Fatalf("prefixed OTLP = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestDevelopmentMLflowVersionDowngrade(t *testing.T) {
	original := SupportedMLflowVersion
	t.Cleanup(func() { SupportedMLflowVersion = original })
	SupportedMLflowVersion = "v3.16.2.dev0"
	for _, tc := range []struct {
		status string
		want   bool
	}{
		{"v3.15.2", false},
		{"v3.16.2.dev0", false},
		{"v3.16.2.dev1", true},
		{"v3.16.2", true},
	} {
		t.Run(tc.status, func(t *testing.T) {
			instance := &mlflowv1.MLflow{Status: mlflowv1.MLflowStatus{Version: tc.status}}
			if got := supportedVersionEarlierThanStatusVersion(instance); got != tc.want {
				t.Fatalf("downgrade = %v, want %v", got, tc.want)
			}
		})
	}
}

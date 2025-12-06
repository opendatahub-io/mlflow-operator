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

package e2e

import (
	"fmt"
	"os"
	"testing"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
)

var (
	// projectImage is the name of the image which will be build and loaded
	// with the code source changes to be tested.
	// This should be set via the IMG environment variable.
	projectImage = getProjectImage()
)

// getProjectImage retrieves the operator image to use for testing.
// It checks the IMG environment variable, defaulting to a standard value if not set.
func getProjectImage() string {
	img := os.Getenv("IMG")
	if img == "" {
		img = "localhost/mlflow-operator:v0.0.1"
	}
	return img
}

// TestE2E runs the end-to-end (e2e) test suite for the project. These tests execute in an isolated,
// temporary environment to validate project changes with the purpose of being used in CI jobs.
// The test assumes that a Kubernetes cluster is already running and the operator image is built and loaded.
func TestE2E(t *testing.T) {
	RegisterFailHandler(Fail)
	_, _ = fmt.Fprintf(GinkgoWriter, "Starting mlflow-operator integration test suite\n")
	RunSpecs(t, "e2e suite")
}

var _ = BeforeSuite(func() {
	// Setup
})

var _ = AfterSuite(func() {
	// Teardown
})

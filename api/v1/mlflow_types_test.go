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

package v1

import (
	"encoding/json"
	"testing"

	corev1 "k8s.io/api/core/v1"
)

func TestMLflowSpecResourceClaimsJSONRoundTrip(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name  string
		claim corev1.PodResourceClaim
	}{
		{
			name: "resource claim template reference",
			claim: corev1.PodResourceClaim{
				Name:                      "shared-gpu",
				ResourceClaimTemplateName: ptr("shared-gpu-template"),
			},
		},
		{
			name: "resource claim direct reference",
			claim: corev1.PodResourceClaim{
				Name:              "shared-gpu",
				ResourceClaimName: ptr("existing-shared-gpu"),
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()

			original := MLflowSpec{
				ResourceClaims: []corev1.PodResourceClaim{tt.claim},
			}

			data, err := json.Marshal(original)
			if err != nil {
				t.Fatalf("marshal MLflowSpec: %v", err)
			}

			var roundTripped MLflowSpec
			if err := json.Unmarshal(data, &roundTripped); err != nil {
				t.Fatalf("unmarshal MLflowSpec: %v", err)
			}

			if len(roundTripped.ResourceClaims) != 1 {
				t.Fatalf("resourceClaims length = %d, want 1", len(roundTripped.ResourceClaims))
			}

			claim := roundTripped.ResourceClaims[0]
			if claim.Name != tt.claim.Name {
				t.Fatalf("resourceClaims[0].name = %q, want %q", claim.Name, tt.claim.Name)
			}

			switch {
			case tt.claim.ResourceClaimTemplateName != nil:
				if claim.ResourceClaimTemplateName == nil || *claim.ResourceClaimTemplateName != *tt.claim.ResourceClaimTemplateName {
					t.Fatalf("resourceClaims[0].resourceClaimTemplateName = %v, want %q", claim.ResourceClaimTemplateName, *tt.claim.ResourceClaimTemplateName)
				}
				if claim.ResourceClaimName != nil {
					t.Fatalf("resourceClaims[0].resourceClaimName = %v, want nil", claim.ResourceClaimName)
				}
			case tt.claim.ResourceClaimName != nil:
				if claim.ResourceClaimName == nil || *claim.ResourceClaimName != *tt.claim.ResourceClaimName {
					t.Fatalf("resourceClaims[0].resourceClaimName = %v, want %q", claim.ResourceClaimName, *tt.claim.ResourceClaimName)
				}
				if claim.ResourceClaimTemplateName != nil {
					t.Fatalf("resourceClaims[0].resourceClaimTemplateName = %v, want nil", claim.ResourceClaimTemplateName)
				}
			}
		})
	}
}

func ptr[T any](v T) *T {
	return &v
}

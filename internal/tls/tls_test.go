package tls

import (
	"context"
	"crypto/tls"
	"errors"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/go-logr/logr"
	frameworktls "github.com/opendatahub-io/odh-platform-utilities/framework/tls"
	configv1 "github.com/openshift/api/config/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

type getErrorClient struct {
	client.Client
	err      error
	getCalls int
}

type countingReader struct {
	client.Reader
	getCalls int
}

func (r *countingReader) Get(ctx context.Context, key client.ObjectKey, obj client.Object, opts ...client.GetOption) error {
	r.getCalls++
	return r.Reader.Get(ctx, key, obj, opts...)
}

func (c *getErrorClient) Get(context.Context, client.ObjectKey, client.Object, ...client.GetOption) error {
	c.getCalls++
	return c.err
}

func TestConfigureTLSFromClientDeniedReadFailsClosed(t *testing.T) {
	apiServerGR := schema.GroupResource{Group: "config.openshift.io", Resource: "apiservers"}
	for name, readErr := range map[string]error{
		"forbidden":    apierrors.NewForbidden(apiServerGR, "cluster", errors.New("no access")),
		"unauthorized": apierrors.NewUnauthorized("no access"),
	} {
		t.Run(name, func(t *testing.T) {
			bootstrapClient := &getErrorClient{err: readErr}
			result, err := configureTLSFromClient(context.Background(), bootstrapClient, logr.Discard())
			if err == nil {
				t.Fatal("configureTLSFromClient() error = nil, want authorization error")
			}
			if bootstrapClient.getCalls != 1 {
				t.Fatalf("APIServer Get calls = %d, want 1", bootstrapClient.getCalls)
			}
			if len(result.Opts) != 0 {
				t.Fatalf("TLS options = %d, want none on denied APIServer read", len(result.Opts))
			}
		})
	}
}

func TestConfigureTLSFromClientInternalErrorUsesWatchableFallback(t *testing.T) {
	bootstrapClient := &getErrorClient{err: apierrors.NewInternalError(errors.New("temporarily unavailable"))}
	result, err := configureTLSFromClient(context.Background(), bootstrapClient, logr.Discard())
	if err != nil {
		t.Fatalf("configureTLSFromClient() error = %v, want transient fallback", err)
	}
	if bootstrapClient.getCalls != 1 {
		t.Fatalf("APIServer Get calls = %d, want 1", bootstrapClient.getCalls)
	}
	if !reflect.DeepEqual(result.Profile, *configv1.TLSProfiles[configv1.TLSProfileIntermediateType]) {
		t.Errorf("Profile = %#v, want Intermediate", result.Profile)
	}
	if !result.Watchable {
		t.Error("Watchable = false, want true so the watcher can self-heal")
	}
}

func TestConfigureTLSFromClientAPIAbsentFallbacks(t *testing.T) {
	apiServerGR := schema.GroupResource{Group: "config.openshift.io", Resource: "apiservers"}
	for name, tt := range map[string]struct {
		readErr       error
		wantWatchable bool
	}{
		"API not served is not watchable": {
			readErr: &meta.NoKindMatchError{
				GroupKind:        schema.GroupKind{Group: "config.openshift.io", Kind: "APIServer"},
				SearchedVersions: []string{"v1"},
			},
			wantWatchable: false,
		},
		"missing APIServer object stays watchable": {
			readErr:       apierrors.NewNotFound(apiServerGR, "cluster"),
			wantWatchable: true,
		},
	} {
		t.Run(name, func(t *testing.T) {
			bootstrapClient := &getErrorClient{err: tt.readErr}
			result, err := configureTLSFromClient(context.Background(), bootstrapClient, logr.Discard())
			if err != nil {
				t.Fatalf("configureTLSFromClient() error = %v, want fallback", err)
			}
			if result.Watchable != tt.wantWatchable {
				t.Errorf("Watchable = %t, want %t", result.Watchable, tt.wantWatchable)
			}
		})
	}
}

func TestConfigureTLSFromClientUnregisteredAPIServerSchemeFailsClosed(t *testing.T) {
	bootstrapClient := &countingReader{
		Reader: fake.NewClientBuilder().WithScheme(runtime.NewScheme()).Build(),
	}

	result, err := configureTLSFromClient(context.Background(), bootstrapClient, logr.Discard())
	if err == nil {
		t.Fatal("configureTLSFromClient() error = nil, want unregistered APIServer scheme error")
	}
	if bootstrapClient.getCalls != 1 {
		t.Fatalf("APIServer Get calls = %d, want 1", bootstrapClient.getCalls)
	}
	if len(result.Opts) != 0 {
		t.Fatalf("TLS options = %d, want none when the APIServer scheme is unregistered", len(result.Opts))
	}
}

func TestConfigureTLSFromClientEmptyCipherListAdherence(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := configv1.Install(scheme); err != nil {
		t.Fatal(err)
	}

	for name, tt := range map[string]struct {
		policy        configv1.TLSAdherencePolicy
		wantStrictErr bool
	}{
		"Strict rejects empty cipher list": {
			policy:        configv1.TLSAdherencePolicyStrictAllComponents,
			wantStrictErr: true,
		},
		"unknown policy rejects empty cipher list": {
			policy:        configv1.TLSAdherencePolicy("FuturePolicy"),
			wantStrictErr: true,
		},
		"NoOpinion uses Intermediate defaults": {},
		"Legacy uses Intermediate defaults": {
			policy: configv1.TLSAdherencePolicyLegacyAdheringComponentsOnly,
		},
	} {
		t.Run(name, func(t *testing.T) {
			apiServer := &configv1.APIServer{}
			apiServer.Name = "cluster"
			apiServer.Spec.TLSAdherence = tt.policy
			apiServer.Spec.TLSSecurityProfile = &configv1.TLSSecurityProfile{
				Type: configv1.TLSProfileCustomType,
				Custom: &configv1.CustomTLSProfile{TLSProfileSpec: configv1.TLSProfileSpec{
					MinTLSVersion: configv1.VersionTLS12,
				}},
			}
			bootstrapClient := &countingReader{
				Reader: fake.NewClientBuilder().WithScheme(scheme).WithObjects(apiServer).Build(),
			}

			result, err := configureTLSFromClient(context.Background(), bootstrapClient, logr.Discard())
			if bootstrapClient.getCalls != 1 {
				t.Fatalf("APIServer Get calls = %d, want one snapshot read", bootstrapClient.getCalls)
			}
			if tt.wantStrictErr {
				if err == nil || !strings.Contains(err.Error(), "invalid strict APIServer TLS profile") {
					t.Fatalf("configureTLSFromClient() error = %v, want strict validation error", err)
				}
				return
			}
			if err != nil {
				t.Fatalf("configureTLSFromClient() error = %v, want accepted lenient profile", err)
			}
			config := &tls.Config{}
			for _, option := range result.Opts {
				option(config)
			}
			if config.MinVersion != tls.VersionTLS12 || len(config.CipherSuites) == 0 {
				t.Errorf("effective TLS config = {MinVersion: %d, CipherSuites: %v}, want TLS 1.2 Intermediate defaults", config.MinVersion, config.CipherSuites)
			}
		})
	}
}

func TestConfigureTLSFromClientAdherenceGate(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := configv1.Install(scheme); err != nil {
		t.Fatal(err)
	}

	for name, tt := range map[string]struct {
		policy        configv1.TLSAdherencePolicy
		wantHonored   bool
		wantAdherence configv1.TLSAdherencePolicy
	}{
		"unset uses Intermediate defaults": {
			wantAdherence: configv1.TLSAdherencePolicyNoOpinion,
		},
		"Legacy uses Intermediate defaults": {
			policy:        configv1.TLSAdherencePolicyLegacyAdheringComponentsOnly,
			wantAdherence: configv1.TLSAdherencePolicyLegacyAdheringComponentsOnly,
		},
		"Strict honors the profile": {
			policy:        configv1.TLSAdherencePolicyStrictAllComponents,
			wantHonored:   true,
			wantAdherence: configv1.TLSAdherencePolicyStrictAllComponents,
		},
		"unknown policy is treated as Strict": {
			policy:        configv1.TLSAdherencePolicy("FuturePolicy"),
			wantHonored:   true,
			wantAdherence: configv1.TLSAdherencePolicy("FuturePolicy"),
		},
	} {
		t.Run(name, func(t *testing.T) {
			apiServer := &configv1.APIServer{}
			apiServer.Name = "cluster"
			apiServer.Spec.TLSAdherence = tt.policy
			apiServer.Spec.TLSSecurityProfile = &configv1.TLSSecurityProfile{
				Type: configv1.TLSProfileModernType,
			}
			bootstrapClient := fake.NewClientBuilder().WithScheme(scheme).WithObjects(apiServer).Build()

			result, err := configureTLSFromClient(context.Background(), bootstrapClient, logr.Discard())
			if err != nil {
				t.Fatalf("configureTLSFromClient() error = %v, want success", err)
			}
			if result.AdherencePolicy != tt.wantAdherence {
				t.Errorf("AdherencePolicy = %q, want %q", result.AdherencePolicy, tt.wantAdherence)
			}
			if !result.Watchable {
				t.Error("Watchable = false, want true so adherence flips trigger a reload")
			}
			config := &tls.Config{}
			for _, option := range result.Opts {
				option(config)
			}
			if tt.wantHonored {
				if config.MinVersion != tls.VersionTLS13 {
					t.Errorf("MinVersion = %d, want TLS 1.3 from the honored Modern profile", config.MinVersion)
				}
				return
			}
			if config.MinVersion != tls.VersionTLS12 || len(config.CipherSuites) == 0 {
				t.Errorf("effective TLS config = {MinVersion: %d, CipherSuites: %v}, want TLS 1.2 Intermediate defaults", config.MinVersion, config.CipherSuites)
			}
			if want := *configv1.TLSProfiles[configv1.TLSProfileModernType]; !reflect.DeepEqual(result.Profile, want) {
				t.Errorf("Profile = %#v, want the observed Modern spec as the watcher seed", result.Profile)
			}
		})
	}
}

func TestConfigureTLSFromClientNotHonoredSkipsProfileValidation(t *testing.T) {
	// A malformed profile must not block startup when the adherence policy
	// leaves the TLS posture to the component: the profile is never applied.
	scheme := runtime.NewScheme()
	if err := configv1.Install(scheme); err != nil {
		t.Fatal(err)
	}
	apiServer := &configv1.APIServer{}
	apiServer.Name = "cluster"
	apiServer.Spec.TLSAdherence = configv1.TLSAdherencePolicyLegacyAdheringComponentsOnly
	apiServer.Spec.TLSSecurityProfile = &configv1.TLSSecurityProfile{
		Type: configv1.TLSProfileCustomType,
		Custom: &configv1.CustomTLSProfile{TLSProfileSpec: configv1.TLSProfileSpec{
			MinTLSVersion: configv1.TLSProtocolVersion("VersionTLS99"),
		}},
	}
	bootstrapClient := fake.NewClientBuilder().WithScheme(scheme).WithObjects(apiServer).Build()

	result, err := configureTLSFromClient(context.Background(), bootstrapClient, logr.Discard())
	if err != nil {
		t.Fatalf("configureTLSFromClient() error = %v, want the profile skipped under Legacy", err)
	}
	config := &tls.Config{}
	for _, option := range result.Opts {
		option(config)
	}
	if config.MinVersion != tls.VersionTLS12 || len(config.CipherSuites) == 0 {
		t.Errorf("effective TLS config = {MinVersion: %d, CipherSuites: %v}, want TLS 1.2 Intermediate defaults", config.MinVersion, config.CipherSuites)
	}
}

func TestTLSOptionsFromProfileOldSuiteSurvival(t *testing.T) {
	profileSpec := frameworktls.ProfileSpecFromSecurityProfile(&configv1.TLSSecurityProfile{Type: configv1.TLSProfileOldType})
	opt, err := tlsOptionsFromProfile(*profileSpec, logr.Discard())
	if err != nil {
		t.Fatalf("tlsOptionsFromProfile() error = %v", err)
	}
	cfg := &tls.Config{}
	opt(cfg)
	if cfg.MinVersion != tls.VersionTLS10 {
		t.Fatalf("MinVersion = %v, want TLS 1.0 (Old honored exactly, never downgraded)", cfg.MinVersion)
	}
	// The Old profile's 3DES and RSA key-exchange suites only appear in
	// tls.InsecureCipherSuites(); their presence proves the usable-cipher
	// lookup includes them instead of silently dropping Old's suites.
	if !slices.Contains(cfg.CipherSuites, tls.TLS_RSA_WITH_3DES_EDE_CBC_SHA) {
		t.Fatalf("Old cipher list = %v, want the 3DES suite present", cfg.CipherSuites)
	}
}

func TestTLSOptionsFromProfile(t *testing.T) {
	tests := map[string]struct {
		profile  configv1.TLSProfileSpec
		wantErr  string
		wantMin  uint16
		wantCiph []uint16
	}{
		"invalid minimum version": {
			profile: configv1.TLSProfileSpec{MinTLSVersion: "invalid"},
			wantErr: `failed to resolve minimum TLS version "invalid": unsupported`,
		},
		"all custom ciphers unusable": {
			profile: configv1.TLSProfileSpec{
				MinTLSVersion: configv1.VersionTLS12,
				Ciphers:       []string{"TLS_AES_128_GCM_SHA256", "TLS_CHACHA20_POLY1305_SHA256"},
			},
			wantErr: "failed to find usable cipher suites for TLS <= 1.2: all 2 ciphers in TLS profile are TLS 1.3-only or unimplemented by Go",
		},
		"mixed custom ciphers keep usable subset": {
			profile: configv1.TLSProfileSpec{
				MinTLSVersion: configv1.VersionTLS12,
				Ciphers: []string{
					"TLS_AES_128_GCM_SHA256",
					"TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256",
				},
			},
			wantMin:  tls.VersionTLS12,
			wantCiph: []uint16{tls.TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256},
		},
		"Modern leaves TLS 1.3 ciphers Go-managed": {
			profile: *configv1.TLSProfiles[configv1.TLSProfileModernType],
			wantMin: tls.VersionTLS13,
		},
		"empty minimum defaults to TLS 1.2": {
			profile: configv1.TLSProfileSpec{},
			wantMin: tls.VersionTLS12,
		},
		"TLS 1.0 minimum is preserved": {
			profile: configv1.TLSProfileSpec{MinTLSVersion: configv1.VersionTLS10},
			wantMin: tls.VersionTLS10,
		},
		"TLS 1.1 minimum is preserved": {
			profile: configv1.TLSProfileSpec{MinTLSVersion: configv1.VersionTLS11},
			wantMin: tls.VersionTLS11,
		},
	}

	for name, tt := range tests {
		t.Run(name, func(t *testing.T) {
			option, err := tlsOptionsFromProfile(tt.profile, logr.Discard())
			if tt.wantErr != "" {
				if err == nil || err.Error() != tt.wantErr {
					t.Fatalf("tlsOptionsFromProfile() error = %v, want %q", err, tt.wantErr)
				}
				return
			}
			if err != nil {
				t.Fatalf("tlsOptionsFromProfile() error = %v", err)
			}

			config := &tls.Config{}
			option(config)
			if config.MinVersion != tt.wantMin {
				t.Errorf("MinVersion = %d, want %d", config.MinVersion, tt.wantMin)
			}
			if !reflect.DeepEqual(config.CipherSuites, tt.wantCiph) {
				t.Errorf("CipherSuites = %v, want %v", config.CipherSuites, tt.wantCiph)
			}
		})
	}
}

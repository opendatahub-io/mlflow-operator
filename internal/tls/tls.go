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

package tls

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"time"

	"github.com/go-logr/logr"
	frameworktls "github.com/opendatahub-io/odh-platform-utilities/framework/tls"
	configv1 "github.com/openshift/api/config/v1"
	tlspkg "github.com/openshift/controller-runtime-common/pkg/tls"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	apimeta "k8s.io/apimachinery/pkg/api/meta"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/client-go/rest"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/manager"
)

// TLSConfig contains the resolved metrics TLS settings and watcher seed state.
type TLSConfig struct {
	Opts            []func(*tls.Config)
	Profile         configv1.TLSProfileSpec
	AdherencePolicy configv1.TLSAdherencePolicy
	Watchable       bool
}

// Configure reads the cluster TLS profile once and prepares metrics TLS options.
func Configure(cfg *rest.Config, scheme *runtime.Scheme, log logr.Logger) (TLSConfig, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	bootstrapCfg := rest.CopyConfig(cfg)
	bootstrapCfg.Timeout = 10 * time.Second
	bootstrapClient, err := client.New(bootstrapCfg, client.Options{Scheme: scheme})
	if err != nil {
		return TLSConfig{}, fmt.Errorf("unable to create bootstrap client for TLS profile: %w", err)
	}

	result, err := configureTLSFromClient(ctx, bootstrapClient, log)
	if err != nil {
		return TLSConfig{}, err
	}
	result.Opts = append(result.Opts, func(c *tls.Config) {
		c.NextProtos = []string{"h2", "http/1.1"}
	})
	return result, nil
}

func configureTLSFromClient(ctx context.Context, bootstrapClient client.Reader, log logr.Logger) (TLSConfig, error) {
	reader := &apiServerReader{Reader: bootstrapClient}
	loaded, err := frameworktls.LoadWithAdherence(ctx, reader)
	if err != nil {
		if reader.err == nil {
			return TLSConfig{}, err
		}
		if apierrors.IsNotFound(reader.err) || apimeta.IsNoMatchError(reader.err) {
			return fallbackTLSConfig(false, log)
		}
		if isTransientAPIError(reader.err) {
			log.Info("Transient API error reading TLS profile, using Intermediate fallback", "error", reader.err)
			return fallbackTLSConfig(true, log)
		}
		return TLSConfig{}, classifyAPIServerReadError(reader.err)
	}

	profile := loaded.Spec
	if reader.apiServer != nil {
		profile, err = tlspkg.GetTLSProfileSpec(reader.apiServer.Spec.TLSSecurityProfile)
		if err != nil {
			return TLSConfig{}, fmt.Errorf("unable to resolve TLS profile from APIServer %q: %w", tlspkg.APIServerName, err)
		}
	} else if reader.err != nil {
		log.Error(reader.err, "APIServer resource unavailable, using Intermediate TLS defaults")
	}

	option, err := tlsOptionsFromProfile(profile, log)
	if err != nil {
		return TLSConfig{}, err
	}
	return TLSConfig{
		Opts:            []func(*tls.Config){option},
		Profile:         profile,
		AdherencePolicy: loaded.AdherencePolicy,
		Watchable:       loaded.Watchable,
	}, nil
}

type apiServerReader struct {
	client.Reader
	apiServer *configv1.APIServer
	err       error
}

func (r *apiServerReader) Get(ctx context.Context, key client.ObjectKey, obj client.Object, opts ...client.GetOption) error {
	err := r.Reader.Get(ctx, key, obj, opts...)
	if apiServer, ok := obj.(*configv1.APIServer); ok {
		r.err = err
		if err == nil {
			r.apiServer = apiServer
		}
	}
	return err
}

func fallbackTLSConfig(watchable bool, log logr.Logger) (TLSConfig, error) {
	profile := *configv1.TLSProfiles[configv1.TLSProfileIntermediateType]
	option, err := tlsOptionsFromProfile(profile, log)
	if err != nil {
		return TLSConfig{}, err
	}
	return TLSConfig{
		Opts:            []func(*tls.Config){option},
		Profile:         profile,
		AdherencePolicy: configv1.TLSAdherencePolicyNoOpinion,
		Watchable:       watchable,
	}, nil
}

func tlsOptionsFromProfile(profile configv1.TLSProfileSpec, log logr.Logger) (func(*tls.Config), error) {
	minVersion, err := minTLSVersion(profile.MinTLSVersion)
	if err != nil {
		return nil, err
	}

	profileTLSConfig, unsupported := frameworktls.ConfigFromProfile(profile)
	if len(unsupported) > 0 {
		log.Info("TLS profile contains ciphers unsupported by Go", "unsupported", unsupported)
	}
	if minVersion == tls.VersionTLS13 {
		return profileTLSConfig, nil
	}

	scratch := &tls.Config{}
	profileTLSConfig(scratch)
	usable := usableCipherSuites(scratch.CipherSuites)
	if len(profile.Ciphers) > 0 && len(usable) == 0 {
		return nil, fmt.Errorf("failed to find usable cipher suites for TLS <= 1.2: all %d ciphers in TLS profile are TLS 1.3-only or unimplemented by Go", len(profile.Ciphers))
	}

	return func(config *tls.Config) {
		profileTLSConfig(config)
		if len(profile.Ciphers) > 0 {
			config.CipherSuites = usable
		}
	}, nil
}

func minTLSVersion(version configv1.TLSProtocolVersion) (uint16, error) {
	switch version {
	case "":
		return tls.VersionTLS12, nil
	case configv1.VersionTLS10:
		return tls.VersionTLS10, nil
	case configv1.VersionTLS11:
		return tls.VersionTLS11, nil
	case configv1.VersionTLS12:
		return tls.VersionTLS12, nil
	case configv1.VersionTLS13:
		return tls.VersionTLS13, nil
	default:
		return 0, fmt.Errorf("failed to resolve minimum TLS version %q: unsupported", version)
	}
}

func usableCipherSuites(mapped []uint16) []uint16 {
	implemented := make(map[uint16]struct{})
	for _, suites := range [][]*tls.CipherSuite{tls.CipherSuites(), tls.InsecureCipherSuites()} {
		for _, suite := range suites {
			implemented[suite.ID] = struct{}{}
		}
	}

	usable := make([]uint16, 0, len(mapped))
	for _, id := range mapped {
		if id >= 0x1300 && id <= 0x13ff {
			continue
		}
		if _, ok := implemented[id]; ok {
			usable = append(usable, id)
		}
	}
	return usable
}

func classifyAPIServerReadError(err error) error {
	switch {
	case apierrors.IsForbidden(err):
		return fmt.Errorf("forbidden reading APIServer TLS configuration (check operator RBAC for the APIServer resource): %w", err)
	case apierrors.IsUnauthorized(err):
		return fmt.Errorf("unauthorized reading APIServer TLS configuration (check operator credentials for the APIServer resource): %w", err)
	default:
		return fmt.Errorf("unexpected error reading APIServer TLS configuration: %w", err)
	}
}

func isTransientAPIError(err error) bool {
	return apierrors.IsServiceUnavailable(err) ||
		apierrors.IsTimeout(err) ||
		apierrors.IsServerTimeout(err) ||
		apierrors.IsTooManyRequests(err) ||
		apierrors.IsInternalError(err) ||
		errors.Is(err, context.DeadlineExceeded)
}

// RegisterWatcher restarts the process when the profile or adherence policy changes.
func RegisterWatcher(mgr manager.Manager, result TLSConfig, cancel context.CancelFunc, log logr.Logger) error {
	if !result.Watchable {
		return nil
	}

	watcher := &frameworktls.SecurityProfileWatcher{
		Client:                    mgr.GetClient(),
		InitialTLSProfileSpec:     result.Profile,
		InitialTLSAdherencePolicy: result.AdherencePolicy,
		OnProfileChange: func(context.Context, configv1.TLSProfileSpec, configv1.TLSProfileSpec) {
			log.Info("TLS profile changed, initiating shutdown to reload")
			cancel()
		},
		OnAdherencePolicyChange: func(context.Context, configv1.TLSAdherencePolicy, configv1.TLSAdherencePolicy) {
			log.Info("TLS adherence policy changed, initiating shutdown to reload")
			cancel()
		},
	}
	return watcher.SetupWithManager(mgr)
}

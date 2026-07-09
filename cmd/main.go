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

package main

import (
	"context"
	"crypto/tls"
	"flag"
	"fmt"
	"os"
	"time"

	// Import all Kubernetes client auth plugins (e.g. Azure, GCP, OIDC, etc.)
	// to ensure that exec-entrypoint and run can make use of them.
	_ "k8s.io/client-go/plugin/pkg/client/auth"

	configv1 "github.com/openshift/api/config/v1"
	consolev1 "github.com/openshift/api/console/v1"
	tlspkg "github.com/openshift/controller-runtime-common/pkg/tls"
	monitoringv1 "github.com/prometheus-operator/prometheus-operator/pkg/apis/monitoring/v1"
	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	apimeta "k8s.io/apimachinery/pkg/api/meta"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/fields"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	"k8s.io/apimachinery/pkg/util/wait"
	"k8s.io/client-go/discovery"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/cache"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/healthz"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"
	"sigs.k8s.io/controller-runtime/pkg/metrics/filters"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"
	gatewayv1 "sigs.k8s.io/gateway-api/apis/v1"

	modulev1alpha1 "github.com/opendatahub-io/mlflow-operator/api/mlflowoperator/v1alpha1"
	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	"github.com/opendatahub-io/mlflow-operator/internal/config"
	"github.com/opendatahub-io/mlflow-operator/internal/controller"
	// +kubebuilder:scaffold:imports
)

var (
	scheme   = runtime.NewScheme()
	setupLog = ctrl.Log.WithName("setup")
)

const (
	defaultNamespace    = "opendatahub"
	crdWaitPollInterval = 2 * time.Second
)

func validateStartupConfig(namespace string, cfg *config.OperatorConfig, supportedMLflowVersion string) error {
	if namespace == "" {
		return fmt.Errorf("namespace cannot be empty")
	}
	if cfg.MLflowImage == "" {
		return fmt.Errorf("MLFLOW_IMAGE must be specified")
	}
	if supportedMLflowVersion == "" {
		return fmt.Errorf(
			"SupportedMLflowVersion must be injected via build ldflags from config/component_metadata.yaml")
	}
	return nil
}

func init() {
	utilruntime.Must(clientgoscheme.AddToScheme(scheme))
	utilruntime.Must(modulev1alpha1.AddToScheme(scheme))
	utilruntime.Must(mlflowv1.AddToScheme(scheme))
	utilruntime.Must(configv1.Install(scheme))
	utilruntime.Must(consolev1.AddToScheme(scheme))
	utilruntime.Must(monitoringv1.AddToScheme(scheme))
	utilruntime.Must(gatewayv1.Install(scheme))
	// +kubebuilder:scaffold:scheme
}

func inferPodNamespace() string {
	if ns := os.Getenv("POD_NAMESPACE"); ns != "" {
		return ns
	}
	return defaultNamespace
}

func resolveManagerNamespace(namespace string, operatorConfig *config.OperatorConfig) string {
	if operatorConfig != nil &&
		operatorConfig.EnableMLflowOperatorModuleController &&
		operatorConfig.ApplicationsNamespace != "" {
		return operatorConfig.ApplicationsNamespace
	}
	return namespace
}

func waitForCRD(
	crdName string,
	timeout time.Duration,
	interval time.Duration,
	isAvailable func() (bool, error),
) error {
	if timeout <= 0 {
		return fmt.Errorf(
			"%s CRD wait timeout must be greater than zero",
			crdName,
		)
	}
	if interval <= 0 {
		return fmt.Errorf(
			"%s CRD wait interval must be greater than zero",
			crdName,
		)
	}

	var lastErr error
	err := wait.PollUntilContextTimeout(
		context.Background(),
		interval,
		timeout,
		true,
		func(context.Context) (bool, error) {
			available, err := isAvailable()
			if err != nil {
				lastErr = err
				return false, nil
			}
			return available, nil
		},
	)
	if err == nil {
		return nil
	}
	if lastErr != nil {
		return fmt.Errorf(
			"%s CRD did not become available within %s: %w",
			crdName,
			timeout,
			lastErr,
		)
	}
	return fmt.Errorf(
		"%s CRD did not become available within %s",
		crdName,
		timeout,
	)
}

// nolint:gocyclo
func main() {
	var metricsAddr string
	var metricsCertPath, metricsCertName, metricsCertKey string
	var enableLeaderElection bool
	var probeAddr string
	var secureMetrics bool
	var namespace string
	var tlsOpts []func(*tls.Config)
	flag.StringVar(&metricsAddr, "metrics-bind-address", "0", "The address the metrics endpoint binds to. "+
		"Use :8443 for HTTPS or :8080 for HTTP, or leave as 0 to disable the metrics service.")
	flag.StringVar(&probeAddr, "health-probe-bind-address", ":8081", "The address the probe endpoint binds to.")
	flag.StringVar(&namespace, "namespace", inferPodNamespace(),
		"Target namespace for MLflow resources. Defaults to the pod's own namespace when running in-cluster.")
	flag.BoolVar(&enableLeaderElection, "leader-elect", false,
		"Enable leader election for controller manager. "+
			"Enabling this will ensure there is only one active controller manager.")
	flag.BoolVar(&secureMetrics, "metrics-secure", true,
		"If set, the metrics endpoint is served securely via HTTPS. Use --metrics-secure=false to use HTTP instead.")
	flag.StringVar(&metricsCertPath, "metrics-cert-path", "",
		"The directory that contains the metrics server certificate.")
	flag.StringVar(&metricsCertName, "metrics-cert-name", "tls.crt", "The name of the metrics server certificate file.")
	flag.StringVar(&metricsCertKey, "metrics-cert-key", "tls.key", "The name of the metrics server key file.")
	opts := zap.Options{
		Development: false,
	}
	opts.BindFlags(flag.CommandLine)
	flag.Parse()

	ctrl.SetLogger(zap.New(zap.UseFlagOptions(&opts)))
	operatorConfig := config.GetConfig()
	namespace = resolveManagerNamespace(namespace, operatorConfig)

	if err := validateStartupConfig(namespace, operatorConfig, controller.SupportedMLflowVersion); err != nil {
		setupLog.Error(err, "invalid startup configuration")
		os.Exit(1)
	}
	setupLog.Info("Starting operator", "targetNamespace", namespace)

	// Fetch cluster TLS profile from apiservers.config.openshift.io/cluster
	cfg := ctrl.GetConfigOrDie()
	bootstrapClient, err := client.New(cfg, client.Options{Scheme: scheme})
	if err != nil {
		setupLog.Error(err, "unable to create bootstrap client for TLS profile fetch")
		os.Exit(1)
	}

	bootstrapCtx, cancelBootstrap := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancelBootstrap()

	tlsProfileFetched := false
	tlsProfile, err := tlspkg.FetchAPIServerTLSProfile(bootstrapCtx, bootstrapClient)
	if err != nil {
		switch {
		case apierrors.IsNotFound(err), apimeta.IsNoMatchError(err):
			setupLog.Info("APIServer TLS profile API unavailable, using Intermediate defaults")
		case apierrors.IsServiceUnavailable(err),
			apierrors.IsTimeout(err),
			apierrors.IsTooManyRequests(err):
			setupLog.Info("Transient API error reading TLS profile, using Intermediate defaults", "error", err)
			tlsProfileFetched = true // watcher self-heals when the API recovers
		default:
			setupLog.Error(err, "unable to fetch APIServer TLS profile")
			os.Exit(1)
		}
		tlsProfile = *configv1.TLSProfiles[configv1.TLSProfileIntermediateType]
	} else {
		tlsProfileFetched = true
	}
	tlsConfigFn, unsupported := tlspkg.NewTLSConfigFromProfile(tlsProfile)
	if len(unsupported) > 0 {
		setupLog.Info("TLS profile contains ciphers unsupported by Go", "unsupported", unsupported)
	}
	tlsOpts = append(tlsOpts, tlsConfigFn)

	tlsAdherence, err := tlspkg.FetchAPIServerTLSAdherencePolicy(bootstrapCtx, bootstrapClient)
	if err != nil {
		setupLog.Info("unable to fetch TLS adherence policy, watcher will retry", "error", err)
	}
	tlsAdherenceFetched := true

	tlsOpts = append(tlsOpts, func(c *tls.Config) {
		c.NextProtos = []string{"h2", "http/1.1"}
	})

	// Metrics endpoint is enabled in 'config/base/kustomization.yaml'. The Metrics options configure the server.
	// More info:
	// - https://pkg.go.dev/sigs.k8s.io/controller-runtime@v0.22.4/pkg/metrics/server
	// - https://book.kubebuilder.io/reference/metrics.html
	metricsServerOptions := metricsserver.Options{
		BindAddress:   metricsAddr,
		SecureServing: secureMetrics,
		TLSOpts:       tlsOpts,
	}

	if secureMetrics {
		// FilterProvider is used to protect the metrics endpoint with authn/authz.
		// These configurations ensure that only authorized users and service accounts
		// can access the metrics endpoint. The RBAC are configured in 'config/rbac/kustomization.yaml'. More info:
		// https://pkg.go.dev/sigs.k8s.io/controller-runtime@v0.22.4/pkg/metrics/filters#WithAuthenticationAndAuthorization
		metricsServerOptions.FilterProvider = filters.WithAuthenticationAndAuthorization
	}

	// If the certificate is not specified, controller-runtime will automatically
	// generate self-signed certificates for the metrics server. While convenient for development and testing,
	// this setup is not recommended for production.
	if len(metricsCertPath) > 0 {
		setupLog.Info("Initializing metrics certificate watcher using provided certificates",
			"metrics-cert-path", metricsCertPath, "metrics-cert-name", metricsCertName, "metrics-cert-key", metricsCertKey)
		metricsServerOptions.CertDir = metricsCertPath
		metricsServerOptions.CertName = metricsCertName
		metricsServerOptions.KeyName = metricsCertKey
	}

	// Lease is valid for 60 seconds, but renew it after 15 seconds and then give up after 50 seconds.
	// This will lessen the load on the Kubernetes API server and help reduce the number of restarts on the pod.
	leaseDuration := 60 * time.Second
	retryPeriod := 15 * time.Second
	renewDeadline := 50 * time.Second

	// Create label selector for MLflow-owned resources
	labelSelector := labels.SelectorFromSet(labels.Set{"app": "mlflow"})
	migrationJobLabelSelector := labels.SelectorFromSet(labels.Set{controller.MigrationJobLabelKey: "true"})
	sharedClusterRoleFieldSelector := fields.OneTermEqualSelector("metadata.name", controller.ClusterRoleName)
	sharedClusterRoleBindingFieldSelector := fields.OneTermEqualSelector(
		"metadata.name", controller.ClusterRoleBindingName)

	// Check ConsoleLink availability early to configure cache
	discoveryClient, err := discovery.NewDiscoveryClientForConfig(cfg)
	if err != nil {
		setupLog.Error(err, "unable to create discovery client")
		os.Exit(1)
	}

	// Build the ByObject cache configuration
	byObjectCache := map[client.Object]cache.ByObject{
		&appsv1.Deployment{}:            {Label: labelSelector},
		&batchv1.Job{}:                  {Label: migrationJobLabelSelector},
		&corev1.Pod{}:                   {Label: migrationJobLabelSelector},
		&corev1.Secret{}:                {Label: labelSelector},
		&corev1.Service{}:               {Label: labelSelector},
		&corev1.ServiceAccount{}:        {Label: labelSelector},
		&corev1.PersistentVolumeClaim{}: {Label: labelSelector},
		// Use metadata.name field selectors so list/watch authorization stays aligned with
		// resourceNames-scoped RBAC for the shared server ClusterRole/ClusterRoleBinding.
		&rbacv1.ClusterRole{}:        {Field: sharedClusterRoleFieldSelector},
		&rbacv1.ClusterRoleBinding{}: {Field: sharedClusterRoleBindingFieldSelector},
	}

	// Conditionally add ConsoleLink to cache if available
	consoleLinkAvailable, err := controller.IsConsoleLinkAvailable(discoveryClient)
	if err != nil {
		setupLog.Error(err, "Failed to check ConsoleLink availability")
	} else if consoleLinkAvailable {
		setupLog.Info("ConsoleLink CRD available, adding to cache with label selector")
		byObjectCache[&consolev1.ConsoleLink{}] = cache.ByObject{Label: labelSelector}
	} else {
		setupLog.Info("ConsoleLink CRD not available, skipping cache configuration")
	}

	// Conditionally add HTTPRoute to cache if available
	httpRouteAvailable, err := controller.IsHTTPRouteAvailable(discoveryClient)
	if err != nil {
		setupLog.Error(err, "Failed to check HTTPRoute availability")
	} else if httpRouteAvailable {
		setupLog.Info("HTTPRoute CRD available, adding to cache with label selector")
		byObjectCache[&gatewayv1.HTTPRoute{}] = cache.ByObject{Label: labelSelector}
	} else {
		setupLog.Info("HTTPRoute CRD not available, skipping cache configuration")
	}

	// Conditionally add ServiceMonitor to cache if available
	serviceMonitorAvailable, err := controller.IsServiceMonitorAvailable(discoveryClient)
	if err != nil {
		setupLog.Error(err, "Failed to check ServiceMonitor availability")
	} else if serviceMonitorAvailable {
		setupLog.Info("ServiceMonitor CRD available, adding to cache with label selector")
		byObjectCache[&monitoringv1.ServiceMonitor{}] = cache.ByObject{Label: labelSelector}
	} else {
		setupLog.Info("ServiceMonitor CRD not available, skipping cache configuration")
	}

	// Conditionally configure namespace RBAC controller cache entries
	authAvailable := false
	if operatorConfig.EnableNamespaceRBAC {
		setupLog.Info(
			"Namespace RBAC controller enabled; waiting for Auth CRD before controller setup",
			"timeout", operatorConfig.AuthCRDWaitTimeout,
		)
		if err := waitForCRD(
			controller.AuthCRDName,
			operatorConfig.AuthCRDWaitTimeout,
			crdWaitPollInterval,
			func() (bool, error) {
				return controller.IsAuthAvailable(discoveryClient)
			},
		); err != nil {
			setupLog.Error(err, "Namespace RBAC controller enabled but Auth CRD did not become available")
			os.Exit(1)
		}
		authAvailable = true
		setupLog.Info("Auth CRD available, adding Auth to cache")
		authObj := &unstructured.Unstructured{}
		authObj.SetGroupVersionKind(schema.GroupVersionKind{
			Group:   "services.platform.opendatahub.io",
			Version: "v1alpha1",
			Kind:    "Auth",
		})
		byObjectCache[authObj] = cache.ByObject{
			Field: fields.OneTermEqualSelector("metadata.name", controller.AuthCRName),
		}
	}

	if operatorConfig.EnableMLflowOperatorModuleController {
		setupLog.Info(
			"MLflowOperator controller enabled; waiting for required CRD before controller setup",
			"timeout", operatorConfig.MLflowOperatorCRDWaitTimeout,
		)
		if err := waitForCRD(
			controller.MLflowOperatorCRDName,
			operatorConfig.MLflowOperatorCRDWaitTimeout,
			crdWaitPollInterval,
			func() (bool, error) {
				return controller.IsMLflowOperatorAvailable(discoveryClient)
			},
		); err != nil {
			setupLog.Error(err, "MLflowOperator controller enabled but required CRD did not become available")
			os.Exit(1)
		}
		setupLog.Info("MLflowOperator CRD available, proceeding with controller setup")
	}

	mgr, err := ctrl.NewManager(cfg, ctrl.Options{
		Scheme:                 scheme,
		Metrics:                metricsServerOptions,
		HealthProbeBindAddress: probeAddr,
		LeaderElection:         enableLeaderElection,
		LeaderElectionID:       "a5eb1b3b.opendatahub.io",
		LeaseDuration:          &leaseDuration,
		RenewDeadline:          &renewDeadline,
		RetryPeriod:            &retryPeriod,
		// Cache configuration to limit watch scope to deployment namespace and MLflow-owned resources
		Cache: cache.Options{
			// Limit owned resources to the target namespace only
			DefaultNamespaces: map[string]cache.Config{
				namespace: {},
			},
			// Apply label selector specifically to owned resources
			ByObject: byObjectCache,
		},
		// LeaderElectionReleaseOnCancel defines if the leader should step down voluntarily
		// when the Manager ends. This requires the binary to immediately end when the
		// Manager is stopped, otherwise, this setting is unsafe. Setting this significantly
		// speeds up voluntary leader transitions as the new leader don't have to wait
		// LeaseDuration time first.
		//
		// In the default scaffold provided, the program ends immediately after
		// the manager stops, so would be fine to enable this option. However,
		// if you are doing or is intended to do any operation such as perform cleanups
		// after the manager stops then its usage might be unsafe.
		// LeaderElectionReleaseOnCancel: true,
	})
	if err != nil {
		setupLog.Error(err, "unable to start manager")
		os.Exit(1)
	}

	// Keep a dedicated exact-name cache for the singleton GC RBAC objects.
	// We intentionally avoid widening the main manager cache back to label-scoped RBAC watches
	// because the operator now relies on tight resourceNames-scoped ClusterRole/ClusterRoleBinding
	// permissions. Kubernetes only honors those list/watch permissions when the request is restricted
	// to an exact metadata.name field selector, and the main cache can only express one selector per
	// GVK. Since the shared server objects are watched as `mlflow`, we need a second cache to watch
	// the singleton `mlflow-gc` RBAC objects without broadening RBAC.
	gcRBACWatchCache, err := controller.NewGCRBACWatchCache(cfg, scheme)
	if err != nil {
		setupLog.Error(err, "unable to create GC RBAC watch cache")
		os.Exit(1)
	}
	if err := mgr.Add(gcRBACWatchCache); err != nil {
		setupLog.Error(err, "unable to add GC RBAC watch cache")
		os.Exit(1)
	}

	if err := (&controller.MLflowReconciler{
		Client:                  mgr.GetClient(),
		Scheme:                  mgr.GetScheme(),
		Namespace:               namespace,
		ChartPath:               "charts/mlflow",
		ConsoleLinkAvailable:    consoleLinkAvailable,
		HTTPRouteAvailable:      httpRouteAvailable,
		ServiceMonitorAvailable: serviceMonitorAvailable,
		GCRBACWatchCache:        gcRBACWatchCache,
	}).SetupWithManager(mgr); err != nil {
		setupLog.Error(err, "unable to create controller", "controller", "MLflow")
		os.Exit(1)
	}
	// Only turn on the new MLflowOperator ownership path during the coordinated ODH handoff.
	if operatorConfig.EnableMLflowOperatorModuleController {
		if err := (&controller.MLflowOperatorReconciler{
			Client:                mgr.GetClient(),
			Scheme:                mgr.GetScheme(),
			ApplicationsNamespace: operatorConfig.ApplicationsNamespace,
		}).SetupWithManager(mgr); err != nil {
			setupLog.Error(err, "unable to create controller", "controller", "MLflowOperator")
			os.Exit(1)
		}
	} else {
		setupLog.Info("MLflowOperator controller disabled; keeping legacy module ownership path inactive")
	}
	if operatorConfig.EnableNamespaceRBAC && authAvailable {
		viewRBWatchCache, err := controller.NewRoleBindingWatchCache(cfg, scheme, controller.RoleBindingViewName)
		if err != nil {
			setupLog.Error(err, "unable to create RoleBinding view watch cache")
			os.Exit(1)
		}
		if err := mgr.Add(viewRBWatchCache); err != nil {
			setupLog.Error(err, "unable to add RoleBinding view watch cache")
			os.Exit(1)
		}
		editRBWatchCache, err := controller.NewRoleBindingWatchCache(cfg, scheme, controller.RoleBindingEditName)
		if err != nil {
			setupLog.Error(err, "unable to create RoleBinding edit watch cache")
			os.Exit(1)
		}
		if err := mgr.Add(editRBWatchCache); err != nil {
			setupLog.Error(err, "unable to add RoleBinding edit watch cache")
			os.Exit(1)
		}
		namePrefix := operatorConfig.ResourceNamePrefix
		if err := (&controller.NamespaceRBACReconciler{
			Client:              mgr.GetClient(),
			Scheme:              mgr.GetScheme(),
			ViewRBWatchCache:    viewRBWatchCache,
			EditRBWatchCache:    editRBWatchCache,
			ViewClusterRoleName: namePrefix + controller.ViewClusterRoleBaseName,
			EditClusterRoleName: namePrefix + controller.EditClusterRoleBaseName,
		}).SetupWithManager(mgr); err != nil {
			setupLog.Error(err, "unable to create controller", "controller", "NamespaceRBAC")
			os.Exit(1)
		}
	} else if !operatorConfig.EnableNamespaceRBAC {
		setupLog.Info("Namespace RBAC controller disabled")
	}
	// +kubebuilder:scaffold:builder

	// Register SecurityProfileWatcher to restart on TLS profile changes
	ctx, cancel := context.WithCancel(ctrl.SetupSignalHandler())
	defer cancel()

	if tlsProfileFetched {
		watcher := &tlspkg.SecurityProfileWatcher{
			Client:                mgr.GetClient(),
			InitialTLSProfileSpec: tlsProfile,
			OnProfileChange: func(_ context.Context, _, _ configv1.TLSProfileSpec) {
				setupLog.Info("TLS profile changed, initiating shutdown to reload")
				cancel()
			},
		}
		if tlsAdherenceFetched {
			watcher.InitialTLSAdherencePolicy = tlsAdherence
			watcher.OnAdherencePolicyChange = func(_ context.Context, _, _ configv1.TLSAdherencePolicy) {
				setupLog.Info("TLS adherence policy changed, initiating shutdown to reload")
				cancel()
			}
		}
		if err := watcher.SetupWithManager(mgr); err != nil {
			setupLog.Error(err, "unable to set up TLS profile watcher")
			os.Exit(1)
		}
	}

	if err := mgr.AddHealthzCheck("healthz", healthz.Ping); err != nil {
		setupLog.Error(err, "unable to set up health check")
		os.Exit(1)
	}
	if err := mgr.AddReadyzCheck("readyz", healthz.Ping); err != nil {
		setupLog.Error(err, "unable to set up ready check")
		os.Exit(1)
	}

	setupLog.Info("starting manager")
	if err := mgr.Start(ctx); err != nil {
		setupLog.Error(err, "problem running manager")
		os.Exit(1)
	}
}

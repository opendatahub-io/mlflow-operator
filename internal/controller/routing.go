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
	"context"
	_ "embed"
	"encoding/base64"
	"fmt"

	mlflowv1 "github.com/opendatahub-io/mlflow-operator/api/v1"
	"github.com/opendatahub-io/mlflow-operator/internal/config"
	consolev1 "github.com/openshift/api/console/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/discovery"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	gatewayv1 "sigs.k8s.io/gateway-api/apis/v1"
)

//go:embed assets/mlflow_console_link_icon.svg
var consoleLinkIconSVG []byte

// IsConsoleLinkAvailable checks if ConsoleLink CRD is available in the cluster using discovery API
func IsConsoleLinkAvailable(discoveryClient discovery.DiscoveryInterface) (bool, error) {
	ctx := context.Background()
	log := logf.FromContext(ctx)

	// Check if the ConsoleLink resource exists
	gv := schema.GroupVersion{Group: "console.openshift.io", Version: "v1"}
	resourceList, err := discoveryClient.ServerResourcesForGroupVersion(gv.String())
	if err != nil {
		// If we get a NotFound error, the API group doesn't exist
		if errors.IsNotFound(err) || discovery.IsGroupDiscoveryFailedError(err) {
			log.V(1).Info("ConsoleLink CRD not available in cluster")
			return false, nil
		}
		return false, fmt.Errorf("failed to check for ConsoleLink availability: %w", err)
	}

	// Check if ConsoleLink resource is in the list
	for _, resource := range resourceList.APIResources {
		if resource.Kind == "ConsoleLink" {
			log.V(1).Info("ConsoleLink CRD is available in cluster")
			return true, nil
		}
	}

	log.V(1).Info("ConsoleLink CRD not found in resource list")
	return false, nil
}

// IsHTTPRouteAvailable checks if HTTPRoute CRD is available in the cluster using discovery API
func IsHTTPRouteAvailable(discoveryClient discovery.DiscoveryInterface) (bool, error) {
	ctx := context.Background()
	log := logf.FromContext(ctx)

	// Check if the HTTPRoute resource exists
	gv := schema.GroupVersion{Group: "gateway.networking.k8s.io", Version: "v1"}
	resourceList, err := discoveryClient.ServerResourcesForGroupVersion(gv.String())
	if err != nil {
		// If we get a NotFound error, the API group doesn't exist
		if errors.IsNotFound(err) || discovery.IsGroupDiscoveryFailedError(err) {
			log.V(1).Info("HTTPRoute CRD not available in cluster")
			return false, nil
		}
		return false, fmt.Errorf("failed to check for HTTPRoute availability: %w", err)
	}

	// Check if HTTPRoute resource is in the list
	for _, resource := range resourceList.APIResources {
		if resource.Kind == "HTTPRoute" {
			log.V(1).Info("HTTPRoute CRD is available in cluster")
			return true, nil
		}
	}

	log.V(1).Info("HTTPRoute CRD not found in resource list")
	return false, nil
}

// reconcileConsoleLink creates or updates the ConsoleLink for MLflow
func (r *MLflowReconciler) reconcileConsoleLink(ctx context.Context, mlflow *mlflowv1.MLflow) error {
	log := logf.FromContext(ctx)

	if !r.ConsoleLinkAvailable {
		log.V(1).Info("Skipping ConsoleLink creation - not available in cluster")
		return nil
	}

	cfg := config.GetConfig()

	// Determine ConsoleLink name based on CR name
	// If CR name is "mlflow", ConsoleLink name is "mlflow"
	// Otherwise ConsoleLink name is "mlflow-${cr_name}"
	consoleLinkName := ResourceName + getResourceSuffix(mlflow.Name)

	// Encode SVG icon to base64
	iconBase64 := base64.StdEncoding.EncodeToString(consoleLinkIconSVG)
	iconDataURL := "data:image/svg+xml;base64," + iconBase64

	// Create ConsoleLink object
	consoleLink := &consolev1.ConsoleLink{
		TypeMeta: metav1.TypeMeta{
			APIVersion: "console.openshift.io/v1",
			Kind:       "ConsoleLink",
		},
		ObjectMeta: metav1.ObjectMeta{
			Name: consoleLinkName,
			Labels: map[string]string{
				"app": ResourceName,
			},
		},
		Spec: consolev1.ConsoleLinkSpec{
			Link: consolev1.Link{
				Text: "MLflow",
				Href: fmt.Sprintf("%s/%s", cfg.MLflowURL, consoleLinkName),
			},
			Location: consolev1.ApplicationMenu,
			ApplicationMenu: &consolev1.ApplicationMenuSpec{
				Section:  cfg.SectionTitle,
				ImageURL: iconDataURL,
			},
		},
	}

	// Set owner reference
	if err := controllerutil.SetControllerReference(mlflow, consoleLink, r.Scheme); err != nil {
		return fmt.Errorf("failed to set controller reference on ConsoleLink: %w", err)
	}

	// Create or update the ConsoleLink
	if err := r.applyObject(ctx, consoleLink); err != nil {
		log.Error(err, "Failed to apply ConsoleLink", "name", consoleLinkName)
		return err
	}

	log.V(1).Info("Successfully reconciled ConsoleLink", "name", consoleLinkName)
	return nil
}

// reconcileHttpRoute creates or updates the HttpRoute for MLflow
func (r *MLflowReconciler) reconcileHttpRoute(ctx context.Context, mlflow *mlflowv1.MLflow, namespace string) error {
	log := logf.FromContext(ctx)

	if !r.HTTPRouteAvailable {
		log.V(1).Info("Skipping HTTPRoute creation - not available in cluster")
		return nil
	}

	cfg := config.GetConfig()

	// Determine HttpRoute name and path prefix based on CR name using resource suffix
	// If CR name is "mlflow", HttpRoute name is "mlflow" and path prefix is "/mlflow"
	// Otherwise HttpRoute name is "mlflow-${cr_name}" and path prefix is "/mlflow-${cr_name}"
	suffix := getResourceSuffix(mlflow.Name)
	httpRouteName := ResourceName + suffix
	pathPrefix := "/" + ResourceName + suffix
	apiPathPrefix := pathPrefix + "/api"
	v1PathPrefix := pathPrefix + "/v1"
	replaceApiPrefix := "/api"
	replaceV1Prefix := "/v1"
	serviceName := ResourceName + suffix

	// Create HttpRoute object
	pathMatchType := gatewayv1.PathMatchPathPrefix
	servicePort := gatewayv1.PortNumber(8443)
	weight := int32(1)

	gatewayNamespace := "openshift-ingress"
	httpRoute := &gatewayv1.HTTPRoute{
		TypeMeta: metav1.TypeMeta{
			APIVersion: "gateway.networking.k8s.io/v1",
			Kind:       "HTTPRoute",
		},
		ObjectMeta: metav1.ObjectMeta{
			Name:      httpRouteName,
			Namespace: namespace,
			Labels: map[string]string{
				"app": ResourceName,
			},
		},
		Spec: gatewayv1.HTTPRouteSpec{
			CommonRouteSpec: gatewayv1.CommonRouteSpec{
				ParentRefs: []gatewayv1.ParentReference{
					{
						Name:      gatewayv1.ObjectName(cfg.GatewayName),
						Namespace: (*gatewayv1.Namespace)(&gatewayNamespace),
					},
				},
			},
			Rules: []gatewayv1.HTTPRouteRule{
				{
					Matches: []gatewayv1.HTTPRouteMatch{
						{
							Path: &gatewayv1.HTTPPathMatch{
								Type:  &pathMatchType,
								Value: &apiPathPrefix,
							},
						},
					},
					Filters: []gatewayv1.HTTPRouteFilter{
						{
							Type: gatewayv1.HTTPRouteFilterURLRewrite,
							URLRewrite: &gatewayv1.HTTPURLRewriteFilter{
								Path: &gatewayv1.HTTPPathModifier{
									Type:               gatewayv1.PrefixMatchHTTPPathModifier,
									ReplacePrefixMatch: &replaceApiPrefix,
								},
							},
						},
					},
					BackendRefs: []gatewayv1.HTTPBackendRef{
						{
							BackendRef: gatewayv1.BackendRef{
								BackendObjectReference: gatewayv1.BackendObjectReference{
									Name: gatewayv1.ObjectName(serviceName),
									Port: &servicePort,
								},
								Weight: &weight,
							},
						},
					},
				},
				{
					Matches: []gatewayv1.HTTPRouteMatch{
						{
							Path: &gatewayv1.HTTPPathMatch{
								Type:  &pathMatchType,
								Value: &v1PathPrefix,
							},
						},
					},
					Filters: []gatewayv1.HTTPRouteFilter{
						{
							Type: gatewayv1.HTTPRouteFilterURLRewrite,
							URLRewrite: &gatewayv1.HTTPURLRewriteFilter{
								Path: &gatewayv1.HTTPPathModifier{
									Type:               gatewayv1.PrefixMatchHTTPPathModifier,
									ReplacePrefixMatch: &replaceV1Prefix,
								},
							},
						},
					},
					BackendRefs: []gatewayv1.HTTPBackendRef{
						{
							BackendRef: gatewayv1.BackendRef{
								BackendObjectReference: gatewayv1.BackendObjectReference{
									Name: gatewayv1.ObjectName(serviceName),
									Port: &servicePort,
								},
								Weight: &weight,
							},
						},
					},
				},
				{
					Matches: []gatewayv1.HTTPRouteMatch{
						{
							Path: &gatewayv1.HTTPPathMatch{
								Type:  &pathMatchType,
								Value: &pathPrefix,
							},
						},
					},
					BackendRefs: []gatewayv1.HTTPBackendRef{
						{
							BackendRef: gatewayv1.BackendRef{
								BackendObjectReference: gatewayv1.BackendObjectReference{
									Name: gatewayv1.ObjectName(serviceName),
									Port: &servicePort,
								},
								Weight: &weight,
							},
						},
					},
				},
			},
		},
	}

	// Set owner reference
	if err := controllerutil.SetControllerReference(mlflow, httpRoute, r.Scheme); err != nil {
		return fmt.Errorf("failed to set controller reference on HttpRoute: %w", err)
	}

	// Create or update the HttpRoute
	if err := r.applyObject(ctx, httpRoute); err != nil {
		log.Error(err, "Failed to apply HttpRoute", "name", httpRouteName)
		return err
	}

	log.V(1).Info("Successfully reconciled HttpRoute", "name", httpRouteName, "pathPrefix", pathPrefix)
	return nil
}

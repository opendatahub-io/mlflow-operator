# MLflow Helm Chart

This Helm chart deploys MLflow with optional kube-rbac-proxy for authentication.

## Authentication with kube-rbac-proxy

When `kubeRbacProxy.enabled` is set to `true` (the default), the chart deploys a [kube-rbac-proxy](https://github.com/brancz/kube-rbac-proxy) sidecar container alongside MLflow. This section explains how the authentication and authorization flow works.

### Background: How kube-rbac-proxy Works

kube-rbac-proxy is a small HTTP proxy that sits in front of your application and enforces Kubernetes authentication and authorization. It performs two main functions:

1. **Authentication**: Validates that incoming requests have a valid identity (via TokenReview against the Kubernetes API server)
2. **Authorization**: Checks whether the authenticated identity is allowed to perform the requested action (via SubjectAccessReview)

### The Problem: Redundant Authorization Checks

In a typical setup, kube-rbac-proxy performs a SubjectAccessReview (SAR) for every request to verify the caller has permission to access the proxied service. However, MLflow has its own authorization layer using the `kubernetes-auth` app that also performs authorization checks.

This means:
1. kube-rbac-proxy authenticates the token and performs a SAR
2. The request is forwarded to MLflow
3. MLflow's kubernetes-auth performs its own authorization checks

The SAR in step 1 is redundant because MLflow handles fine-grained authorization itself.

### The Solution: Static Authorization with Authentication

This chart configures kube-rbac-proxy to **keep authentication but skip the SAR authorization check**. Here's how:

```yaml
authorization:
  resourceAttributes: {}
  static:
    - resourceRequest: true
```

Let's break down what each part does:

#### `resourceAttributes: {}`

This tells kube-rbac-proxy to treat every incoming request as a "resource request" (as opposed to a "non-resource request" which would use URL path-based authorization). By setting this to an empty object, we're not specifying any particular resource - this is important for the next part.

#### `static: [{resourceRequest: true}]`

The `static` authorizer is a list of rules that are evaluated in order. Each rule can match on various attributes (resource, namespace, verb, etc.). When a field is omitted or empty, it acts as a **wildcard** that matches anything.

In our case:
- `resourceRequest: true` means "match any resource request"
- All other fields (resource, namespace, verb, etc.) are omitted, so they match anything

When the static authorizer finds a matching rule, it **immediately returns "Allow"** without calling the next authorizer (SAR).

### The Authorization Chain

kube-rbac-proxy evaluates authorizers in a chain: `static -> SAR`. The chain short-circuits on the first "Allow" result.

With our configuration:
1. Request comes in with a bearer token
2. **Authentication**: kube-rbac-proxy validates the token via TokenReview - unauthenticated requests get 401
3. **Authorization**: The static authorizer matches (because `resourceRequest: true` matches everything) and returns "Allow"
4. **SAR is never called** because the static authorizer already allowed the request
5. Request is forwarded to MLflow with the authenticated user identity in headers

### Security Implications

- **Unauthenticated requests are still rejected** (401 Unauthorized)
- **Any authenticated user can access MLflow** through this proxy
- **MLflow's kubernetes-auth handles fine-grained authorization** (e.g., which experiments a user can access)

This is the intended behavior: kube-rbac-proxy acts as an authentication gateway that verifies identity, while MLflow handles the actual authorization decisions.

### Configuration Reference

The proxy configuration is stored in a ConfigMap (`mlflow-kube-rbac-proxy-config`) and mounted into the sidecar container. The relevant values are:

| Value | Description | Default |
|-------|-------------|---------|
| `kubeRbacProxy.enabled` | Enable kube-rbac-proxy sidecar | `true` |
| `kubeRbacProxy.image.name` | Proxy image | `quay.io/opendatahub/odh-kube-auth-proxy:latest` |
| `kubeRbacProxy.tls.secretName` | Secret containing TLS cert/key | `mlflow-tls` |

### Request Flow Diagram

```
Client Request (with bearer token)
        |
        v
+-------------------+
| kube-rbac-proxy   |
|                   |
| 1. TokenReview    |---> Kubernetes API (validate token)
|    (Auth'n)       |<--- Valid/Invalid
|                   |
| 2. Static Allow   |     (SAR skipped)
|    (Auth'z)       |
+-------------------+
        |
        | (authenticated request with user headers)
        v
+-------------------+
| MLflow Server     |
| (kubernetes-auth) |
|                   |
| Fine-grained      |
| authorization     |
+-------------------+
```

## Other Configuration

See `values.yaml` for all available configuration options.

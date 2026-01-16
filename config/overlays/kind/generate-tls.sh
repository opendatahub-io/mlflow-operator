#!/bin/bash
# Generate self-signed TLS certificate for Kind deployment
# This script is called by kustomize to create TLS secrets at deployment time

set -e

# Create temporary directory for certificates
CERT_DIR=$(mktemp -d)
trap "rm -rf $CERT_DIR" EXIT

# Generate self-signed certificate and key
openssl req -x509 -newkey rsa:2048 -keyout "$CERT_DIR/tls.key" -out "$CERT_DIR/tls.crt" \
    -days 365 -nodes -subj "/CN=mlflow.opendatahub.svc.cluster.local" \
    -addext "subjectAltName=DNS:mlflow.opendatahub.svc.cluster.local,DNS:mlflow,DNS:localhost"

# Output the certificate or key based on the argument
case "${1:-}" in
    "cert")
        cat "$CERT_DIR/tls.crt"
        ;;
    "key")
        cat "$CERT_DIR/tls.key"
        ;;
    *)
        echo "Usage: $0 {cert|key}" >&2
        exit 1
        ;;
esac
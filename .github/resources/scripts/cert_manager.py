#!/usr/bin/env python3
"""
Certificate Manager for Kubernetes Users

This module creates actual Kubernetes users with valid certificates.
Steps: Private Key -> CSR -> Signed Certificate -> Kubeconfig
"""

import base64
import yaml
from pathlib import Path


class CertManager:
    def __init__(self, run_command_func, ca_cert_path=None, ca_key_path=None, cluster_name="kind"):
        """
        Initialize the certificate manager

        Args:
            run_command_func: Function to run shell commands (shared with other managers)
            ca_cert_path: Path to cluster CA certificate
            ca_key_path: Path to cluster CA private key
            cluster_name: Name of the Kubernetes cluster (for CA file extraction)
        """
        self.run_command = run_command_func
        self.ca_cert_path = ca_cert_path or "/etc/kubernetes/pki/ca.crt"
        self.ca_key_path = ca_key_path or "/etc/kubernetes/pki/ca.key"
        self.cluster_name = cluster_name
        self.cert_dir = Path("/tmp/k8s-user-certs")
        self.cert_dir.mkdir(exist_ok=True)

    def create_private_key(self, username):
        """
        Step 1: Create a private key for the user
        This is like the user's cryptographic "password"
        """
        key_file = self.cert_dir / f"{username}.key"

        self.run_command([
            "openssl", "genrsa",
            "-out", str(key_file),
            "2048"
        ], f"Creating private key for {username}")

        return key_file

    def create_certificate_request(self, username, key_file, groups=None):
        """
        Step 2: Create a Certificate Signing Request (CSR)
        This asks the cluster CA to sign our certificate

        Args:
            username: The user name (becomes CN in certificate)
            key_file: Path to the private key
            groups: List of groups user belongs to (becomes O in certificate)
        """
        csr_file = self.cert_dir / f"{username}.csr"

        # Default group for authenticated users
        if groups is None:
            groups = ["system:authenticated"]

        # Build the certificate subject
        # CN = Common Name (username), O = Organization (groups)
        subject = f"/CN={username}"
        for group in groups:
            subject += f"/O={group}"

        self.run_command([
            "openssl", "req",
            "-new",                    # Create new request
            "-key", str(key_file),     # Use this private key
            "-out", str(csr_file),     # Output CSR file
            "-subj", subject           # Certificate subject
        ], f"Creating certificate request for {username}")

        return csr_file

    def sign_certificate(self, username, csr_file, days=365):
        """
        Step 3: Sign the certificate with cluster CA
        This creates the actual certificate that Kubernetes will trust
        """
        cert_file = self.cert_dir / f"{username}.crt"

        # Verify CA files exist
        if not Path(self.ca_cert_path).exists():
            print(f"❌ Cluster CA certificate not found: {self.ca_cert_path}")
            print("   For kind clusters, copy it with:")
            print(f"   docker cp kind-control-plane:/etc/kubernetes/pki/ca.crt {self.ca_cert_path}")
            return None

        if not Path(self.ca_key_path).exists():
            print(f"❌ Cluster CA private key not found: {self.ca_key_path}")
            print("   For kind clusters, copy it with:")
            print(f"   docker cp kind-control-plane:/etc/kubernetes/pki/ca.key {self.ca_key_path}")
            return None

        self.run_command([
            "openssl", "x509",
            "-req",                    # Sign a request
            "-in", str(csr_file),      # Input CSR
            "-CA", self.ca_cert_path,  # Cluster CA certificate
            "-CAkey", self.ca_key_path, # Cluster CA private key
            "-CAcreateserial",         # Create serial number file
            "-out", str(cert_file),    # Output certificate
            "-days", str(days)         # Certificate validity
        ], f"Signing certificate for {username}")

        return cert_file

    def get_cluster_server_url(self):
        """Get the Kubernetes API server URL"""
        try:
            result = self.run_command(
                "kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}'",
                "Getting cluster server URL",
                capture_output=True
            )
            return result if result else "https://kubernetes.default.svc"
        except:
            return "https://kubernetes.default.svc"

    def create_kubeconfig(self, username, cert_file, key_file, namespace=None):
        """
        Step 4: Create kubeconfig file for the user
        This bundles everything the user needs to connect to Kubernetes
        """
        kubeconfig_file = self.cert_dir / f"{username}-kubeconfig.yaml"

        # Read and encode certificate files
        with open(cert_file, 'r') as f:
            cert_data = base64.b64encode(f.read().encode()).decode()

        with open(key_file, 'r') as f:
            key_data = base64.b64encode(f.read().encode()).decode()

        with open(self.ca_cert_path, 'r') as f:
            ca_data = base64.b64encode(f.read().encode()).decode()

        # Build kubeconfig structure
        kubeconfig = {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [{
                "cluster": {
                    "certificate-authority-data": ca_data,
                    "server": self.get_cluster_server_url()
                },
                "name": "kubernetes"
            }],
            "contexts": [{
                "context": {
                    "cluster": "kubernetes",
                    "user": username,
                    "namespace": namespace or "default"
                },
                "name": f"{username}-context"
            }],
            "current-context": f"{username}-context",
            "users": [{
                "name": username,
                "user": {
                    "client-certificate-data": cert_data,
                    "client-key-data": key_data
                }
            }]
        }

        # Write kubeconfig file
        with open(kubeconfig_file, 'w') as f:
            yaml.dump(kubeconfig, f, default_flow_style=False)

        print(f"✅ Kubeconfig created: {kubeconfig_file}")
        return kubeconfig_file

    def create_user_certificate(self, username, namespace=None, groups=None, days=365):
        """
        Complete workflow: Create a user with certificate authentication

        Args:
            username: The username
            namespace: Default namespace for user
            groups: Groups the user belongs to
            days: Certificate validity period

        Returns:
            dict: Paths to all created files, or None if failed
        """
        print(f"🔐 Creating certificate-based user: {username}")

        try:
            # Step 1: Create private key
            key_file = self.create_private_key(username)

            # Step 2: Create certificate signing request
            csr_file = self.create_certificate_request(username, key_file, groups)

            # Step 3: Sign certificate with cluster CA
            cert_file = self.sign_certificate(username, csr_file, days)
            if not cert_file:
                return None

            # Step 4: Create kubeconfig file
            kubeconfig_file = self.create_kubeconfig(username, cert_file, key_file, namespace)

            print(f"✅ Certificate user '{username}' created successfully!")

            return {
                "username": username,
                "private_key": str(key_file),
                "certificate": str(cert_file),
                "kubeconfig": str(kubeconfig_file)
            }

        except Exception as e:
            print(f"❌ Failed to create certificate for {username}: {e}")
            return None

    def setup_ca_for_cluster(self):
        """
        Copy CA files from specified cluster
        Works with kind, minikube, and other local development clusters
        """
        print(f"🔧 Setting up CA files from cluster: {self.cluster_name}")

        ca_dir = Path(self.ca_cert_path).parent
        ca_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Dynamic container name based on cluster name
            container_name = f"{self.cluster_name}-control-plane"

            # Copy CA certificate
            self.run_command([
                "docker", "cp",
                f"{container_name}:/etc/kubernetes/pki/ca.crt",
                self.ca_cert_path
            ], f"Copying CA certificate from {container_name}")

            # Copy CA private key
            self.run_command([
                "docker", "cp",
                f"{container_name}:/etc/kubernetes/pki/ca.key",
                self.ca_key_path
            ], f"Copying CA private key from {container_name}")

            print(f"✅ CA files copied successfully from {container_name}")
            return True

        except Exception as e:
            print(f"❌ Failed to copy CA files from {container_name}: {e}")
            print(f"   Make sure the cluster '{self.cluster_name}' is running")
            print(f"   Expected container: {container_name}")
            return False

    def verify_user_access(self, kubeconfig_file, namespace="default"):
        """Test if the user can authenticate with the cluster"""
        print(f"🔍 Testing user authentication...")

        result = self.run_command([
            "kubectl", "--kubeconfig", str(kubeconfig_file),
            "auth", "can-i", "get", "pods", "-n", namespace
        ], "Testing user access", capture_output=True, check=False)

        if result and "yes" in result.lower():
            print("✅ User authentication successful")
            return True
        else:
            print(f"⚠️  User authentication failed: {result}")
            return False
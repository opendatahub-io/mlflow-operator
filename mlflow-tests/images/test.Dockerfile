FROM registry.access.redhat.com/ubi9/python-311:9.6

ENV KUBECONFIG=/mlflow/.kube/config
ENV DEPLOY_PY=/mlflow/.github/actions/deploy/deploy.py

USER root

ARG OC_VERSION=4.18.3
ARG UV_VERSION=0.9.7
ARG KUSTOMIZE_VERSION=5.8.0

RUN curl -fsSL "https://mirror.openshift.com/pub/openshift-v4/clients/ocp/${OC_VERSION}/openshift-client-linux.tar.gz" \
      -o /tmp/oc-client.tar.gz && \
    tar -xf /tmp/oc-client.tar.gz -C /tmp && \
    cp /tmp/oc /usr/local/bin/oc && \
    cp /tmp/kubectl /usr/local/bin/kubectl && \
    rm -f /tmp/oc-client.tar.gz /tmp/oc /tmp/kubectl

# Download latest Kustomize library
RUN wget -q https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize/v${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz -P kustomize && \
    tar -xf kustomize/kustomize_v5.8.0_linux_amd64.tar.gz -C kustomize && \
    cp kustomize/kustomize /usr/local/bin/kustomize && \
    rm -rf kustomize

# Install uv
COPY --from=ghcr.io/astral-sh/uv:${UV_VERSION} /uv /uvx /usr/local/bin/

# Drop back to non-root for runtime (required for OpenShift SCC compliance)
USER 1001

# Expose port 8080 for port forwarding (local tests)
EXPOSE 8080

# Declare working directory
WORKDIR /mlflow

# Install dependencies
COPY --chown=1001:1001 ./mlflow-tests/uv.lock .
COPY --chown=1001:1001 ./mlflow-tests/pyproject.toml .
COPY --chown=1001:1001 ./mlflow-tests/README.md .
RUN uv sync

# Copy all required package files from the project
COPY --chown=1001:1001 .github/actions/deploy/deploy.py ./.github/actions/deploy/deploy.py
COPY --chown=1001:1001 ./config ./config
COPY --chown=1001:1001 ./mlflow-tests ./mlflow-tests

# Command to run the tests
ENTRYPOINT ["bash", "mlflow-tests/images/test-run.sh"]

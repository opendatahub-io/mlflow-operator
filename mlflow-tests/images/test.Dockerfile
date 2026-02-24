FROM registry.access.redhat.com/ubi9/python-311:9.6

ENV KUBECONFIG=/mlflow/.kube/config

USER root

ARG OC_VERSION=4.18.3
ARG UV_VERSION=0.9.7

RUN curl -fsSL "https://mirror.openshift.com/pub/openshift-v4/clients/ocp/${OC_VERSION}/openshift-client-linux.tar.gz" \
      -o /tmp/oc-client.tar.gz && \
    tar -xf /tmp/oc-client.tar.gz -C /tmp && \
    cp /tmp/oc /usr/local/bin/oc && \
    rm -f /tmp/oc-client.tar.gz /tmp/oc /tmp/kubectl

# Install uv
COPY --from=ghcr.io/astral-sh/uv:${UV_VERSION} /uv /uvx /usr/local/bin/

# Drop back to non-root for runtime (required for OpenShift SCC compliance)
USER 1001

# Declare working directory
WORKDIR /mlflow
 
# Copy all required package files from the project
COPY --chown=1001:1001 ./mlflow-tests .

# Download dependencies
RUN uv sync

# Command to run the tests
ENTRYPOINT ["bash", "images/test-run.sh"]

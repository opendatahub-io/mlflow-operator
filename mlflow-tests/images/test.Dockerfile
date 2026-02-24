#FROM registry.access.redhat.com/ubi9/python-311:9.6
FROM python:3.11

ENV KUBECONFIG=/mlflow/mlflow-tests/.kube/config

# Declare working directory
WORKDIR /mlflow

# Download the latest OpenShift CLI binary
RUN wget -q https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable-4.20/openshift-client-linux.tar.gz -P oc-client && \
    tar -xf oc-client/openshift-client-linux.tar.gz -C oc-client && \
    cp oc-client/oc /usr/local/bin && \
    rm -rf oc-client/

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Download latest Kustomize library
RUN wget -q https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize/v5.8.0/kustomize_v5.8.0_linux_amd64.tar.gz -P kustomize && \
    tar -xf kustomize/kustomize_v5.8.0_linux_amd64.tar.gz -C kustomize && \
    cp kustomize/kustomize /usr/local/bin/kustomize && \
    rm -rf kustomize
 
# Copy all required package files from the project
COPY ./mlflow-tests .

# Download dependencies
RUN uv sync

# Command to run the tests
ENTRYPOINT ["bash", "images/test-run.sh"]

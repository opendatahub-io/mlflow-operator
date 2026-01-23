# Build the manager binary
FROM registry.access.redhat.com/ubi9/go-toolset:1.24 AS builder
ARG TARGETOS
ARG TARGETARCH
ARG CGO_ENABLED=1

USER root

WORKDIR /workspace
# Copy the Go Modules manifests
COPY go.mod go.mod
COPY go.sum go.sum
# Copy the API module (required for replace directive in go.mod)
COPY api/go.mod api/go.mod
COPY api/go.sum api/go.sum
# cache deps before building and copying source so that we don't need to re-download as much
# and so that source changes don't invalidate our downloaded layer
RUN go mod download

# Copy the Go source (relies on .dockerignore to filter)
COPY . .

# Build
# CGO_ENABLED=1 (default): FIPS-compliant build with strictfipsruntime
# CGO_ENABLED=0: Non-FIPS build for local development on Apple Silicon
RUN if [ "${CGO_ENABLED}" = "1" ]; then \
      CGO_ENABLED=1 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH:-amd64} GO111MODULE=on \
        GOEXPERIMENT=strictfipsruntime go build -tags strictfipsruntime -a -o manager cmd/main.go; \
    else \
      CGO_ENABLED=0 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH:-amd64} GO111MODULE=on \
        go build -a -o manager cmd/main.go; \
    fi

FROM registry.access.redhat.com/ubi9/ubi-minimal:latest
WORKDIR /
COPY --from=builder /workspace/manager .
COPY --from=builder /workspace/charts/mlflow charts/mlflow

USER 1001
ENTRYPOINT ["/manager"]

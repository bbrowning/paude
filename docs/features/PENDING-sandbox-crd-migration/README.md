# Sandbox CRD Migration

## Overview

Migrate the OpenShift backend from Kubernetes StatefulSet (apps/v1) to the
[agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) Sandbox CRD
(agents.x-k8s.io/v1alpha1).

## Why

The agent-sandbox project from kubernetes-sigs provides a purpose-built Sandbox
CRD designed for isolated, stateful, singleton workloads - exactly the use case
paude needs. Benefits:

- **Simplified resource management**: No manual serviceName/selector configuration
- **Built-in lifecycle policies**: shutdownPolicy: Retain preserves state
- **Ecosystem alignment**: Follows Kubernetes direction for agent workloads

## Prerequisites

The Sandbox CRD and controller must be installed on the cluster:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.1.1/manifest.yaml
```

## Verification

```bash
# Unit tests
make test

# Linting and type checking
make lint
make typecheck

# Manual (requires cluster with agent-sandbox)
paude create --backend openshift test-session
paude list --backend openshift
paude stop test-session
paude start test-session
paude delete test-session --confirm
```

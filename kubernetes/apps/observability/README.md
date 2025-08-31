# Observability Stack - GitOps Deployment

This directory contains the GitOps configuration for the observability stack, managed by ArgoCD.

## Components

### Loki
- **Purpose**: Log aggregation and storage
- **Path**: `./loki/`
- **Helm Chart**: grafana/loki v6.24.0
- **Features**:
  - Single binary deployment for simplicity
  - 50Gi persistent storage
  - 30-day retention
  - Loki Canary for health monitoring

### Alloy
- **Purpose**: Log collection from all Kubernetes pods
- **Path**: `./alloy/`
- **Helm Chart**: grafana/alloy v0.10.0
- **Features**:
  - Kubernetes API-based log collection
  - Automatic pod discovery
  - Forwards logs to Loki

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Pods      │────▶│    Alloy    │────▶│    Loki     │
│ (All NS)    │     │ (Collector) │     │  (Storage)  │
└─────────────┘     └─────────────┘     └─────────────┘
                            │                    │
                            ▼                    ▼
                    ┌─────────────┐     ┌─────────────┐
                    │  K8s API    │     │ Loki Canary │
                    └─────────────┘     │ (Monitor)   │
                                        └─────────────┘
```

## Deployment

### Via ArgoCD (Recommended)

The observability stack uses the **App of Apps pattern** for deployment:
- Parent App: `/kubernetes/gitops/apps/observability-stack-app.yaml`
- Child Apps: `/kubernetes/apps/observability/apps/`
  - `namespace-app.yaml` - Creates the observability namespace
  - `loki-app.yaml` - Deploys Loki for log storage
  - `alloy-app.yaml` - Deploys Alloy for log collection

The parent application automatically manages all child applications, ensuring proper ordering through sync waves and centralized lifecycle management.

### Via Ansible

For initial deployment or manual sync:

```bash
# Deploy via GitOps
ansible-playbook homelab/ansible/02-install/07-deploy-observability-gitops.yaml

# Cleanup
ansible-playbook homelab/ansible/03-cleanup/07-cleanup-observability.yaml
```

## Configuration

### Cluster Domain
The cluster uses `home-hk1-cluster.orbb.li` as the domain (not `cluster.local`).

All service endpoints are configured accordingly:
- Loki: `loki.observability.svc.home-hk1-cluster.orbb.li:3100`

### Loki Canary Patch
Due to a limitation in the Loki Helm chart, the loki-canary DaemonSet requires a post-sync patch to use the correct cluster domain. This is handled automatically by the ArgoCD hook in `loki/patch-loki-canary.yaml`.

## Access

Services are accessible via Tailscale network:
- Loki API: `http://loki.observability.svc.home-hk1-cluster.orbb.li:3100`
- Loki Ready: `http://loki.observability.svc.home-hk1-cluster.orbb.li:3100/ready`

## Verification

```bash
# Check ArgoCD App of Apps status
kubectl get application observability-stack -n gitops

# Check all child applications
kubectl get applications -n gitops | grep observability

# Check Loki health
curl http://loki.observability.svc.home-hk1-cluster.orbb.li:3100/ready

# View loki-canary logs (should show successful connections)
kubectl logs -n observability -l app.kubernetes.io/component=canary --tail=10

# Query logs
curl -G "http://loki.observability.svc.home-hk1-cluster.orbb.li:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={namespace="observability"}' \
  --data-urlencode 'limit=5'
```

## Troubleshooting

### Loki Canary Connection Issues
If loki-canary shows connection errors to `cluster.local`:
1. Check if the patch job ran successfully
2. Manually patch if needed:
```bash
kubectl patch daemonset loki-canary -n observability --type='json' \
  -p='[{"op": "replace", "path": "/spec/template/spec/containers/0/args", 
        "value": ["-addr=loki.observability.svc.home-hk1-cluster.orbb.li:3100", 
                 "-labelname=pod", "-labelvalue=$(POD_NAME)", "-push=true"]}]'
```

### ArgoCD Sync Issues
1. Check application status: `kubectl describe application observability-loki -n gitops`
2. Force sync if needed: `argocd app sync observability-loki`
3. Check events: `kubectl get events -n observability --sort-by='.lastTimestamp'`

## GitOps Workflow

1. Make changes to configuration files in this directory
2. Commit and push to the repository
3. ArgoCD automatically detects changes and syncs
4. Monitor sync status in ArgoCD UI or CLI

## Migration from Helm-managed to GitOps

If migrating from direct Helm deployment:
1. The GitOps playbook will automatically uninstall existing Helm releases
2. ArgoCD will then take over management
3. Existing PVCs are preserved to maintain data continuity
# DevOps Namespace

This namespace contains CI/CD and GitOps tooling.

## Components

### ArgoCD (To be deployed)
- Headless deployment planned
- UI to be hosted separately
- GitOps controller for application deployment

## Installation

```bash
# Install ArgoCD (headless)
ansible-playbook ansible/02-install/03-install_argocd.yaml
```

## Configuration Files

- `values-headless.yaml` - Helm values for headless ArgoCD deployment
- `ingress.yaml` - Ingress configuration (if needed)

## Access

- ArgoCD Server: To be configured
- Admin Secret: `kubectl -n devops get secret argocd-initial-admin-secret -o json | jq -r .data.password | base64 -d`
# ArgoCD Core Self-Hosting Configuration

This directory contains the configuration for self-hosted ArgoCD Core (headless - no UI/server).

## Components

- `kustomization.yaml` - Kustomize configuration that references the official ArgoCD Core manifest
- `argocd-self-host.yaml` - ArgoCD Application that enables self-hosting

## Version Management

To upgrade ArgoCD Core version:

1. Edit `kustomization.yaml`
2. **IMPORTANT**: Update the version in TWO places (Kustomize limitation - no variable substitution in resource URLs):
   - Line 15: `version=vX.Y.Z` in configMapGenerator
   - Line 19: Update the version in the resource URL

Example:
```yaml
# Line 15:
  - version=v3.2.0  # UPDATE 1: Change version here

# Line 19:
- https://raw.githubusercontent.com/argoproj/argo-cd/v3.2.0/manifests/core-install.yaml  # UPDATE 2: Change version here too
```

3. Commit and push to Git
4. ArgoCD will automatically sync and update itself

## Resource Adjustments

The `kustomization.yaml` includes patches to adjust memory limits for Raspberry Pi deployment.
Modify these as needed for your environment.

## Deployment

This configuration is applied by the Ansible playbook:
```bash
ansible-playbook 02-install/02-self-hosting.yaml
```

The ArgoCD Application will watch this Git repository and automatically apply any changes.
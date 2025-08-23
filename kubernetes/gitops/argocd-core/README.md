# ArgoCD Core Self-Hosting Configuration

This directory contains the configuration for self-hosted ArgoCD Core (headless - no UI/server).

## Components

- `kustomization.yaml` - Kustomize configuration that references the official ArgoCD Core manifest
- `argocd-self-host.yaml` - ArgoCD Application that enables self-hosting

## Version Management

To upgrade ArgoCD Core version:

1. Edit `kustomization.yaml`
2. Update the version in two places:
   - `configMapGenerator` → `literals` → `version=vX.Y.Z`
   - `resources` → Update the URL to the new version

Example:
```yaml
configMapGenerator:
- name: argocd-version
  literals:
  - version=v3.2.0  # Update version here

resources:
- https://raw.githubusercontent.com/argoproj/argo-cd/v3.2.0/manifests/core-install.yaml  # And here
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
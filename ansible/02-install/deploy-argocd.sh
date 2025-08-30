#!/bin/bash
# Deploy ArgoCD Core with self-hosting enabled
# This script runs both playbooks in sequence

set -e

echo "========================================="
echo "Deploying ArgoCD Core (Headless)"
echo "========================================="

# Install ArgoCD Core from Git Kustomization
echo "Step 1: Installing ArgoCD Core..."
ansible-playbook 01-install-argocd.yaml

echo ""
echo "Step 2: Waiting for ArgoCD to stabilize..."
sleep 15

# Enable self-hosting
echo "Step 3: Configuring self-hosting..."
ansible-playbook 02-argocd-self-hosting.yaml

echo ""
echo "========================================="
echo "ArgoCD Core Deployment Complete!"
echo "========================================="
echo ""
echo "Check status with:"
echo "  kubectl get pods -n gitops"
echo "  ARGOCD_OPTS=\"--core\" argocd app list"
echo ""
echo "ArgoCD is now self-hosting from:"
echo "  https://github.com/orbli/homelab.git"
echo "  Path: kubernetes/apps/argocd"
echo "========================================="
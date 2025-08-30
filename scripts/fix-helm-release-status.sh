#!/bin/bash

# Script to fix Helm release status when deployment succeeded but Helm timed out
# This commonly happens with slow deployments on resource-constrained hardware

set -e

RELEASE_NAME=${1:-keycloak}
NAMESPACE=${2:-iam}

echo "========================================="
echo "Helm Release Status Fix"
echo "========================================="
echo ""
echo "Release: $RELEASE_NAME"
echo "Namespace: $NAMESPACE"
echo ""

# Check current status
echo "Current status:"
helm list -n $NAMESPACE | grep $RELEASE_NAME || echo "Release not found"
echo ""

# Method 1: Mark as deployed using helm upgrade with same version
echo "Method 1: Using helm upgrade --force to refresh status"
echo "This will reconcile the release without changing the deployment"
echo ""
read -p "Do you want to fix the release status? (y/N): " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Getting current chart version..."
    CHART_VERSION=$(helm list -n $NAMESPACE -o json | jq -r ".[] | select(.name==\"$RELEASE_NAME\") | .chart" | cut -d'-' -f2)
    CHART_NAME=$(helm list -n $NAMESPACE -o json | jq -r ".[] | select(.name==\"$RELEASE_NAME\") | .chart" | sed "s/-$CHART_VERSION//")
    
    echo "Chart: $CHART_NAME"
    echo "Version: $CHART_VERSION"
    echo ""
    
    # Get the repository name
    case $CHART_NAME in
        "keycloak")
            REPO="bitnami/keycloak"
            ;;
        "argo-cd")
            REPO="argo/argo-cd"
            ;;
        *)
            echo "Unknown chart. Please specify the repository."
            exit 1
            ;;
    esac
    
    echo "Attempting to fix release status..."
    
    # Use helm upgrade with --force and --no-hooks to just update the status
    helm upgrade $RELEASE_NAME $REPO \
        --namespace $NAMESPACE \
        --version $CHART_VERSION \
        --reuse-values \
        --force \
        --no-hooks \
        --timeout 1m \
        --wait=false
    
    echo ""
    echo "New status:"
    helm list -n $NAMESPACE | grep $RELEASE_NAME
    echo ""
    echo "✓ Release status updated!"
else
    echo "Cancelled."
fi

echo ""
echo "========================================="
echo "Alternative Method (if above doesn't work):"
echo "========================================="
echo ""
echo "You can also manually edit the release status in the Helm secret:"
echo ""
echo "1. Get the secret:"
echo "   kubectl get secret -n $NAMESPACE sh.helm.release.v1.$RELEASE_NAME.v1 -o yaml > helm-release-backup.yaml"
echo ""
echo "2. Decode, modify status from 'failed' to 'deployed', re-encode:"
echo "   kubectl get secret -n $NAMESPACE sh.helm.release.v1.$RELEASE_NAME.v1 -o jsonpath='{.data.release}' | base64 -d | base64 -d | gzip -d > release.json"
echo "   # Edit release.json, change status to 'deployed'"
echo "   cat release.json | gzip | base64 | base64 > new-release.txt"
echo ""
echo "3. Update the secret with the new data"
echo ""
echo "Note: This is more complex and should only be used if the upgrade method fails."
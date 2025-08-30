#!/bin/bash

# ArgoCD Complete Cleanup Script
# This script removes ArgoCD installed via Helm and all associated resources

set -e

echo "========================================="
echo "ArgoCD Complete Removal Script"
echo "========================================="
echo ""

# Check if ArgoCD helm release exists
echo "Checking for ArgoCD Helm release..."
if helm list -n devops | grep -q argocd; then
    echo "✓ Found ArgoCD Helm release in devops namespace"
    
    # Uninstall ArgoCD using Helm
    echo ""
    echo "Step 1: Uninstalling ArgoCD via Helm..."
    helm uninstall argocd -n devops
    echo "✓ ArgoCD Helm release removed"
else
    echo "⚠ No ArgoCD Helm release found, checking for manual resources..."
fi

# Wait for resources to terminate
echo ""
echo "Step 2: Waiting for pods to terminate..."
kubectl wait --for=delete pod -l app.kubernetes.io/part-of=argocd -n devops --timeout=60s 2>/dev/null || true

# Delete any remaining ArgoCD applications (if any exist)
echo ""
echo "Step 3: Checking for ArgoCD Applications..."
if kubectl get applications.argoproj.io -A 2>/dev/null | grep -v "No resources found"; then
    echo "Deleting all ArgoCD Applications..."
    kubectl delete applications.argoproj.io --all -A
else
    echo "✓ No ArgoCD Applications found"
fi

# Delete any remaining AppProjects
echo ""
echo "Step 4: Checking for ArgoCD AppProjects..."
if kubectl get appprojects.argoproj.io -A 2>/dev/null | grep -v "No resources found"; then
    echo "Deleting all ArgoCD AppProjects..."
    kubectl delete appprojects.argoproj.io --all -A
else
    echo "✓ No ArgoCD AppProjects found"
fi

# Delete any remaining ApplicationSets
echo ""
echo "Step 5: Checking for ArgoCD ApplicationSets..."
if kubectl get applicationsets.argoproj.io -A 2>/dev/null | grep -v "No resources found"; then
    echo "Deleting all ArgoCD ApplicationSets..."
    kubectl delete applicationsets.argoproj.io --all -A
else
    echo "✓ No ArgoCD ApplicationSets found"
fi

# Delete ArgoCD CRDs
echo ""
echo "Step 6: Removing ArgoCD Custom Resource Definitions..."
kubectl delete crd applications.argoproj.io 2>/dev/null || echo "✓ CRD applications.argoproj.io already removed"
kubectl delete crd applicationsets.argoproj.io 2>/dev/null || echo "✓ CRD applicationsets.argoproj.io already removed"
kubectl delete crd appprojects.argoproj.io 2>/dev/null || echo "✓ CRD appprojects.argoproj.io already removed"

# Delete cluster-wide resources
echo ""
echo "Step 7: Removing cluster-wide resources..."

# Remove ClusterRoles
kubectl delete clusterrole -l app.kubernetes.io/part-of=argocd 2>/dev/null || echo "✓ No ArgoCD ClusterRoles found"

# Remove ClusterRoleBindings
kubectl delete clusterrolebinding -l app.kubernetes.io/part-of=argocd 2>/dev/null || echo "✓ No ArgoCD ClusterRoleBindings found"

# Clean up any remaining resources in devops namespace
echo ""
echo "Step 8: Cleaning up devops namespace..."
kubectl delete all -l app.kubernetes.io/part-of=argocd -n devops 2>/dev/null || true

# Optional: Ask if user wants to delete the entire devops namespace
echo ""
read -p "Do you want to delete the entire 'devops' namespace? (y/N): " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Deleting devops namespace..."
    kubectl delete namespace devops
    echo "✓ Namespace devops deleted"
else
    echo "✓ Keeping devops namespace"
fi

# Final verification
echo ""
echo "========================================="
echo "Verification"
echo "========================================="
echo ""

echo "Checking for remaining ArgoCD resources..."
echo ""

# Check CRDs
echo "CRDs:"
kubectl get crd | grep argoproj || echo "✓ No ArgoCD CRDs found"

echo ""
echo "Helm releases:"
helm list -A | grep argo || echo "✓ No ArgoCD Helm releases found"

echo ""
echo "Pods in devops namespace:"
kubectl get pods -n devops 2>/dev/null || echo "✓ Namespace devops not found or no pods"

echo ""
echo "========================================="
echo "✓ ArgoCD cleanup completed successfully!"
echo "========================================="
echo ""
echo "You can now reinstall ArgoCD with your desired configuration."
echo "For a headless setup, you'll want to disable the server UI components."
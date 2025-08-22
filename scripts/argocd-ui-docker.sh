#!/bin/bash

# Script to run ArgoCD UI locally via Docker, connecting to headless ArgoCD in cluster
# This provides a UI when needed without deploying it in the cluster

set -e

# Configuration
NAMESPACE="gitops"
ARGOCD_VERSION="v2.13.2"  # Match the deployed version
LOCAL_PORT="8080"
GRPC_PORT="8083"

echo "========================================="
echo "ArgoCD UI Docker Frontend"
echo "========================================="
echo ""
echo "This will run ArgoCD UI locally and connect to your headless deployment"
echo ""

# Check if kubectl can connect to cluster
if ! kubectl cluster-info &>/dev/null; then
    echo "❌ Cannot connect to Kubernetes cluster"
    echo "Please ensure kubectl is configured correctly"
    exit 1
fi

# Check if ArgoCD is deployed
if ! kubectl get deployment -n $NAMESPACE argocd-server &>/dev/null; then
    echo "❌ ArgoCD server not found in namespace $NAMESPACE"
    exit 1
fi

# Get the initial admin password
ADMIN_PASSWORD=$(kubectl -n $NAMESPACE get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" 2>/dev/null | base64 -d)

if [ -z "$ADMIN_PASSWORD" ]; then
    echo "⚠️  No initial admin password found. Admin might be disabled."
    echo "   You can use kubectl exec to set a password if needed."
else
    echo "✅ Admin password retrieved"
fi

echo ""
echo "Starting port-forward to ArgoCD server..."
echo ""

# Kill any existing port-forwards
pkill -f "kubectl.*port-forward.*argocd-server" 2>/dev/null || true

# Start port-forward in background
kubectl port-forward -n $NAMESPACE svc/argocd-server 8080:80 8083:83 &
PF_PID=$!

echo "Port-forward PID: $PF_PID"
echo ""

# Wait for port-forward to be ready
echo "Waiting for port-forward to be ready..."
sleep 5

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "Cleaning up..."
    if [ ! -z "$PF_PID" ]; then
        kill $PF_PID 2>/dev/null || true
    fi
    pkill -f "kubectl.*port-forward.*argocd-server" 2>/dev/null || true
    echo "✅ Cleanup complete"
}

trap cleanup EXIT

echo ""
echo "========================================="
echo "ArgoCD UI Access Information"
echo "========================================="
echo ""
echo "URL: http://localhost:$LOCAL_PORT"
echo "Username: admin"
if [ ! -z "$ADMIN_PASSWORD" ]; then
    echo "Password: $ADMIN_PASSWORD"
else
    echo "Password: (admin disabled or custom auth configured)"
fi
echo ""
echo "API/gRPC endpoint: localhost:$GRPC_PORT"
echo ""
echo "To access with ArgoCD CLI:"
echo "  argocd login localhost:$LOCAL_PORT --username admin --password '$ADMIN_PASSWORD' --insecure"
echo ""
echo "========================================="
echo ""
echo "Press Ctrl+C to stop the UI and port-forward"
echo ""

# Keep the script running
wait $PF_PID
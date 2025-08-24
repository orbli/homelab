#!/bin/bash

# ArgoCD UI Docker container for headless ArgoCD deployment
# Uses host network to directly access Kubernetes services

set -e

# Configuration
NAMESPACE="gitops"
ARGOCD_VERSION="v2.13.1"  # Adjust to match your ArgoCD version
CONTAINER_NAME="argocd-ui"
UI_PORT="8080"
GRPC_PORT="8083"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================="
echo "ArgoCD UI Docker Container (Host Network)"
echo "========================================="
echo ""

# Check if kubectl can connect to cluster
if ! kubectl cluster-info &>/dev/null; then
    echo -e "${RED}❌ Cannot connect to Kubernetes cluster${NC}"
    echo "Please ensure kubectl is configured correctly"
    exit 1
fi

# Check if ArgoCD is deployed
if ! kubectl get pods -n $NAMESPACE | grep -q argocd; then
    echo -e "${RED}❌ ArgoCD not found in namespace $NAMESPACE${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Connected to Kubernetes cluster${NC}"
echo -e "${GREEN}✓ ArgoCD found in namespace $NAMESPACE${NC}"

# Get service IPs
REPO_SERVER_IP=$(kubectl get svc -n $NAMESPACE argocd-repo-server -o jsonpath='{.spec.clusterIP}')
REDIS_IP=$(kubectl get svc -n $NAMESPACE argocd-redis -o jsonpath='{.spec.clusterIP}')
METRICS_IP=$(kubectl get svc -n $NAMESPACE argocd-metrics -o jsonpath='{.spec.clusterIP}')
APPSET_IP=$(kubectl get svc -n $NAMESPACE argocd-applicationset-controller -o jsonpath='{.spec.clusterIP}')

echo ""
echo "Service IPs discovered:"
echo "  Repo Server: $REPO_SERVER_IP:8081"
echo "  Redis: $REDIS_IP:6379"
echo "  Metrics: $METRICS_IP:8082"
echo "  AppSet Controller: $APPSET_IP:7000"

# Get ArgoCD admin password
ADMIN_PASSWORD=$(kubectl -n $NAMESPACE get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" 2>/dev/null | base64 -d)

if [ -z "$ADMIN_PASSWORD" ]; then
    echo -e "${YELLOW}⚠ No initial admin password found${NC}"
    echo "  Creating a new admin password..."
    # Generate a new password
    ADMIN_PASSWORD=$(openssl rand -base64 14)
    # Create the secret
    kubectl -n $NAMESPACE create secret generic argocd-initial-admin-secret \
        --from-literal=password=$(echo -n "$ADMIN_PASSWORD" | base64 -w0) \
        --dry-run=client -o yaml | kubectl apply -f -
    echo -e "${GREEN}✓ Admin password created${NC}"
else
    echo -e "${GREEN}✓ Admin password retrieved${NC}"
fi

# Stop and remove existing container if it exists
if docker ps -a | grep -q $CONTAINER_NAME; then
    echo ""
    echo "Stopping existing ArgoCD UI container..."
    docker stop $CONTAINER_NAME 2>/dev/null || true
    docker rm $CONTAINER_NAME 2>/dev/null || true
fi

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "Cleaning up..."
    docker stop $CONTAINER_NAME 2>/dev/null || true
    docker rm $CONTAINER_NAME 2>/dev/null || true
    echo -e "${GREEN}✓ Cleanup complete${NC}"
}

trap cleanup EXIT

echo ""
echo "Starting ArgoCD UI container..."
echo ""

# Run ArgoCD server container with host network
# This will provide the UI and API server components
docker run -d \
    --name $CONTAINER_NAME \
    --network host \
    --restart unless-stopped \
    quay.io/argoproj/argocd:$ARGOCD_VERSION \
    argocd-server \
    --insecure \
    --port $UI_PORT \
    --grpc-port $GRPC_PORT \
    --repo-server ${REPO_SERVER_IP}:8081 \
    --redis ${REDIS_IP}:6379 \
    --dex-server '' \
    --disable-auth=false \
    --staticassets-dir /shared/app

# Wait for container to start
echo "Waiting for ArgoCD UI to start..."
for i in {1..10}; do
    if curl -s http://localhost:$UI_PORT/healthz >/dev/null 2>&1; then
        break
    fi
    echo -n "."
    sleep 2
done
echo ""

# Check if container is running
if ! docker ps | grep -q $CONTAINER_NAME; then
    echo -e "${RED}❌ Failed to start ArgoCD UI container${NC}"
    echo "Checking container logs:"
    docker logs --tail 30 $CONTAINER_NAME
    exit 1
fi

# Check if UI is accessible
if ! curl -s http://localhost:$UI_PORT/healthz >/dev/null 2>&1; then
    echo -e "${YELLOW}⚠ Container is running but UI is not accessible yet${NC}"
    echo "Check logs with: docker logs -f $CONTAINER_NAME"
else
    echo -e "${GREEN}✓ ArgoCD UI is accessible${NC}"
fi

echo ""
echo "========================================="
echo -e "${GREEN}ArgoCD UI is running!${NC}"
echo "========================================="
echo ""
echo "Access URL: http://localhost:$UI_PORT"
echo "Username: admin"
echo "Password: $ADMIN_PASSWORD"
echo ""
echo "Service connections:"
echo "  Repo Server: ${REPO_SERVER_IP}:8081"
echo "  Redis: ${REDIS_IP}:6379"
echo ""
echo "Container status:"
docker ps --filter name=$CONTAINER_NAME --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
echo ""
echo "To view logs:"
echo "  docker logs -f $CONTAINER_NAME"
echo ""
echo "To access with ArgoCD CLI:"
echo "  argocd login localhost:$UI_PORT --username admin --password '$ADMIN_PASSWORD' --insecure"
echo ""
echo "To stop the container:"
echo "  docker stop $CONTAINER_NAME"
echo ""
echo "Press Ctrl+C to stop the UI container and cleanup"
echo ""

# Keep script running and follow logs
docker logs -f $CONTAINER_NAME
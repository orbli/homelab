#!/bin/bash

# Run ArgoCD UI in Docker connecting to headless ArgoCD in Kubernetes
# This provides a web UI without deploying it in the cluster

set -e

NAMESPACE="gitops"
ARGOCD_VERSION="v2.13.2"

echo "========================================="
echo "ArgoCD UI Docker Solution"
echo "========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check prerequisites
echo "Checking prerequisites..."

if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker is not installed${NC}"
    exit 1
fi

if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}❌ kubectl is not installed${NC}"
    exit 1
fi

if ! kubectl cluster-info &>/dev/null 2>&1; then
    echo -e "${RED}❌ Cannot connect to Kubernetes cluster${NC}"
    exit 1
fi

echo -e "${GREEN}✅ All prerequisites met${NC}"
echo ""

# Get admin password
ADMIN_PASSWORD=$(kubectl -n $NAMESPACE get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" 2>/dev/null | base64 -d || echo "")

# Function to cleanup
cleanup() {
    echo ""
    echo "Cleaning up..."
    
    # Stop port-forwards
    pkill -f "kubectl.*port-forward.*argocd" 2>/dev/null || true
    
    # Stop Docker container
    docker stop argocd-ui 2>/dev/null || true
    docker rm argocd-ui 2>/dev/null || true
    
    echo -e "${GREEN}✅ Cleanup complete${NC}"
}

trap cleanup EXIT

# Kill any existing port-forwards
pkill -f "kubectl.*port-forward.*argocd" 2>/dev/null || true

echo "Starting port-forwards to ArgoCD components..."
echo ""

# Port-forward to ArgoCD server (gRPC)
kubectl port-forward -n $NAMESPACE svc/argocd-server 8083:443 2>/dev/null &
PF1=$!

# Port-forward to ArgoCD repo-server
kubectl port-forward -n $NAMESPACE svc/argocd-repo-server 8081:8081 2>/dev/null &
PF2=$!

# Port-forward to Redis
kubectl port-forward -n $NAMESPACE svc/argocd-redis 6379:6379 2>/dev/null &
PF3=$!

# Port-forward to application controller metrics (for UI)
kubectl port-forward -n $NAMESPACE deployment/argocd-application-controller 8082:8082 2>/dev/null &
PF4=$!

echo "Port-forwards started:"
echo "  - ArgoCD Server gRPC: localhost:8083"
echo "  - Repo Server: localhost:8081"
echo "  - Redis: localhost:6379"
echo "  - App Controller: localhost:8082"
echo ""

# Wait for port-forwards
sleep 5

# Run ArgoCD UI container
echo "Starting ArgoCD UI container..."
echo ""

docker run -d \
    --name argocd-ui \
    --network host \
    --rm \
    -e ARGOCD_SERVER_INSECURE=true \
    -e ARGOCD_GRPC_WEB=true \
    quay.io/argoproj/argocd:${ARGOCD_VERSION} \
    argocd-server \
    --insecure \
    --staticassets /shared/app \
    --grpc-web \
    --disable-auth \
    --repo-server localhost:8081 \
    --application-controller localhost:8082 \
    --redis localhost:6379

# Wait for container to start
sleep 5

# Check if container is running
if ! docker ps | grep -q argocd-ui; then
    echo -e "${RED}❌ Failed to start ArgoCD UI container${NC}"
    docker logs argocd-ui 2>/dev/null || true
    exit 1
fi

echo ""
echo "========================================="
echo -e "${GREEN}ArgoCD UI is running!${NC}"
echo "========================================="
echo ""
echo -e "${YELLOW}Access Information:${NC}"
echo "  URL: http://localhost:8080"
echo "  Username: admin"
if [ ! -z "$ADMIN_PASSWORD" ]; then
    echo "  Password: $ADMIN_PASSWORD"
else
    echo "  Password: (admin disabled or needs to be set)"
fi
echo ""
echo -e "${YELLOW}Alternative access with CLI:${NC}"
echo "  argocd login localhost:8080 --insecure --username admin"
echo ""
echo -e "${YELLOW}View container logs:${NC}"
echo "  docker logs -f argocd-ui"
echo ""
echo "========================================="
echo ""
echo "Press Ctrl+C to stop the UI and cleanup"
echo ""

# Keep script running and show logs
docker logs -f argocd-ui
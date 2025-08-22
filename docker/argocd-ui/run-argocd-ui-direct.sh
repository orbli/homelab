#!/bin/bash

# Run ArgoCD UI in Docker connecting directly to headless ArgoCD in cluster
# Uses direct cluster IPs (accessible via Tailscale advertised routes)

set -e

NAMESPACE="gitops"
ARGOCD_VERSION="v2.13.2"

echo "========================================="
echo "ArgoCD UI Docker Solution (Direct Connect)"
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

# Get service IPs directly from cluster
echo "Getting ArgoCD service endpoints..."

# Get ClusterIPs for services
ARGOCD_SERVER_IP=$(kubectl get svc argocd-server -n $NAMESPACE -o jsonpath='{.spec.clusterIP}')
ARGOCD_REPO_SERVER_IP=$(kubectl get svc argocd-repo-server -n $NAMESPACE -o jsonpath='{.spec.clusterIP}')
ARGOCD_REDIS_IP=$(kubectl get svc argocd-redis -n $NAMESPACE -o jsonpath='{.spec.clusterIP}')

# Get application controller pod IP (no service for this)
ARGOCD_APP_CONTROLLER_IP=$(kubectl get pod -n $NAMESPACE -l app.kubernetes.io/name=argocd-application-controller -o jsonpath='{.items[0].status.podIP}')

echo "  ArgoCD Server: $ARGOCD_SERVER_IP"
echo "  Repo Server: $ARGOCD_REPO_SERVER_IP"
echo "  Redis: $ARGOCD_REDIS_IP"
echo "  App Controller: $ARGOCD_APP_CONTROLLER_IP"
echo ""

# Test connectivity
echo "Testing connectivity to cluster..."
if ping -c 1 -W 2 $ARGOCD_SERVER_IP &>/dev/null; then
    echo -e "${GREEN}✅ Can reach cluster network directly${NC}"
else
    echo -e "${YELLOW}⚠️  Cannot ping cluster IP, but may still work${NC}"
fi
echo ""

# Get admin password
ADMIN_PASSWORD=$(kubectl -n $NAMESPACE get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" 2>/dev/null | base64 -d || echo "")

# Function to cleanup
cleanup() {
    echo ""
    echo "Cleaning up..."
    
    # Stop Docker container
    docker stop argocd-ui 2>/dev/null || true
    docker rm argocd-ui 2>/dev/null || true
    
    echo -e "${GREEN}✅ Cleanup complete${NC}"
}

trap cleanup EXIT

# Stop any existing container
docker stop argocd-ui 2>/dev/null || true
docker rm argocd-ui 2>/dev/null || true

# Run ArgoCD UI container
echo "Starting ArgoCD UI container..."
echo ""

# Prepare kubeconfig for ArgoCD container
mkdir -p /tmp/argocd-kubeconfig
cp ~/.kube/config /tmp/argocd-kubeconfig/config
chmod 644 /tmp/argocd-kubeconfig/config

docker run -d \
    --name argocd-ui \
    --network host \
    --restart unless-stopped \
    -v /tmp/argocd-kubeconfig:/home/argocd/.kube:ro \
    -e HOME=/home/argocd \
    -e ARGOCD_NAMESPACE=${NAMESPACE} \
    quay.io/argoproj/argocd:${ARGOCD_VERSION} \
    argocd-server \
    --insecure \
    --staticassets /shared/app \
    --disable-auth \
    --repo-server ${ARGOCD_REPO_SERVER_IP}:8081 \
    --redis ${ARGOCD_REDIS_IP}:6379 \
    --namespace ${NAMESPACE}

# Wait for container to start
echo "Waiting for UI to start..."
sleep 5

# Check if container is running
if ! docker ps | grep -q argocd-ui; then
    echo -e "${RED}❌ Failed to start ArgoCD UI container${NC}"
    echo "Container logs:"
    docker logs argocd-ui 2>&1 | tail -20
    exit 1
fi

# Check if UI is responding
if curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/healthz | grep -q "200"; then
    echo -e "${GREEN}✅ UI is healthy${NC}"
else
    echo -e "${YELLOW}⚠️  UI may still be starting up${NC}"
fi

echo ""
echo "========================================="
echo -e "${GREEN}ArgoCD UI is running!${NC}"
echo "========================================="
echo ""
echo -e "${YELLOW}Access Information:${NC}"
echo "  🌐 URL: http://localhost:8080"
echo "  👤 Username: admin"
if [ ! -z "$ADMIN_PASSWORD" ]; then
    echo "  🔑 Password: $ADMIN_PASSWORD"
else
    echo "  🔑 Password: (admin disabled or needs to be set)"
fi
echo ""
echo -e "${YELLOW}Direct Cluster Access:${NC}"
echo "  Using ClusterIPs via Tailscale advertised routes"
echo "  No port-forwarding needed!"
echo ""
echo -e "${YELLOW}Container Management:${NC}"
echo "  View logs:    docker logs -f argocd-ui"
echo "  Stop:         docker stop argocd-ui"
echo "  Restart:      docker restart argocd-ui"
echo "  Remove:       docker rm -f argocd-ui"
echo ""
echo -e "${YELLOW}ArgoCD CLI Access:${NC}"
echo "  argocd login localhost:8080 --insecure --username admin"
echo "  argocd login ${ARGOCD_SERVER_IP}:443 --insecure --username admin"
echo ""
echo "========================================="
echo ""
echo "Press Ctrl+C to stop the UI"
echo ""

# Keep script running and show logs
docker logs -f argocd-ui
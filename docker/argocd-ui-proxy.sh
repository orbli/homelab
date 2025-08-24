#!/bin/bash

# ArgoCD UI Docker container for headless ArgoCD deployment
# Uses host network to directly access Kubernetes services
#
# IMPORTANT: This cluster uses custom domain "home-hk1-cluster.orbb.li" 
# instead of default "cluster.local"
# Services are accessible via: service.namespace.svc.home-hk1-cluster.orbb.li

set -e

# Configuration
NAMESPACE="gitops"
ARGOCD_VERSION="v2.13.1"  # Adjust to match your ArgoCD version
CONTAINER_NAME="argocd-ui"
UI_PORT="8080"

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

# Get Redis password if it exists
REDIS_PASSWORD=$(kubectl get secret -n $NAMESPACE argocd-redis -o jsonpath='{.data.auth}' 2>/dev/null | base64 -d)

echo ""
echo "Service IPs discovered:"
echo "  Repo Server: $REPO_SERVER_IP:8081"
echo "  Redis: $REDIS_IP:6379"
echo "  Metrics: $METRICS_IP:8082"
echo "  AppSet Controller: $APPSET_IP:7000"

if [ ! -z "$REDIS_PASSWORD" ]; then
    echo -e "${GREEN}✓ Redis authentication configured${NC}"
else
    echo -e "${YELLOW}⚠ No Redis password found${NC}"
fi

# Check if running in headless/core mode (no server deployment)
if kubectl get deploy -n $NAMESPACE argocd-server &>/dev/null; then
    echo -e "${YELLOW}⚠ ArgoCD server deployment found - not in core/headless mode${NC}"
else
    echo -e "${GREEN}✓ Running in core/headless mode (no auth required)${NC}"
fi

# Stop and remove existing container if it exists
if docker ps -a | grep -q $CONTAINER_NAME; then
    echo ""
    echo "Stopping existing ArgoCD UI container..."
    docker stop $CONTAINER_NAME 2>/dev/null || true
    docker rm $CONTAINER_NAME 2>/dev/null || true
fi

# Kill any existing port-forwards
echo "Stopping any existing port-forwards..."
pkill -f "kubectl.*port-forward.*argocd" 2>/dev/null || true

# Start port-forwards for ArgoCD services
echo ""
echo "Starting kubectl port-forwards for ArgoCD services..."
kubectl port-forward -n $NAMESPACE svc/argocd-repo-server 18081:8081 &
REPO_PF_PID=$!
kubectl port-forward -n $NAMESPACE svc/argocd-redis 16379:6379 &
REDIS_PF_PID=$!
kubectl port-forward -n $NAMESPACE svc/argocd-metrics 18082:8082 &
METRICS_PF_PID=$!

echo "Port-forward PIDs: Repo=$REPO_PF_PID, Redis=$REDIS_PF_PID, Metrics=$METRICS_PF_PID"
sleep 3  # Wait for port-forwards to establish

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "Cleaning up..."
    docker stop $CONTAINER_NAME 2>/dev/null || true
    docker rm $CONTAINER_NAME 2>/dev/null || true
    
    # Kill port-forwards
    [ ! -z "$REPO_PF_PID" ] && kill $REPO_PF_PID 2>/dev/null || true
    [ ! -z "$REDIS_PF_PID" ] && kill $REDIS_PF_PID 2>/dev/null || true
    [ ! -z "$METRICS_PF_PID" ] && kill $METRICS_PF_PID 2>/dev/null || true
    pkill -f "kubectl.*port-forward.*argocd" 2>/dev/null || true
    
    echo -e "${GREEN}✓ Cleanup complete${NC}"
}

trap cleanup EXIT

echo ""
echo "Starting ArgoCD UI container..."
echo ""

# Get Kubernetes cluster DNS IP (usually kube-dns or coredns)
KUBE_DNS_IP=$(kubectl get svc -n kube-system kube-dns -o jsonpath='{.spec.clusterIP}' 2>/dev/null || kubectl get svc -n kube-system coredns -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo "10.43.0.10")

echo "Using Kubernetes DNS: $KUBE_DNS_IP"
echo "Cluster hostname: home-hk1-cluster.orbb.li"

# Run ArgoCD server container with host network
# Using port-forwarded services on localhost
docker run -d \
    --name $CONTAINER_NAME \
    --network host \
    --restart unless-stopped \
    -v /tmp/argocd-kube:/home/argocd/.kube:ro \
    -e HOME=/home/argocd \
    -e REDIS_PASSWORD="${REDIS_PASSWORD}" \
    quay.io/argoproj/argocd:$ARGOCD_VERSION \
    argocd-server \
    --insecure \
    --port $UI_PORT \
    --repo-server localhost:18081 \
    --redis localhost:16379 \
    --disable-auth

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
echo ""
echo "Authentication: DISABLED (no login required)"
echo ""
echo "Service connections (via kubectl port-forward):"
echo "  Repo Server: localhost:18081 → ${REPO_SERVER_IP}:8081"
echo "  Redis: localhost:16379 → ${REDIS_IP}:6379"
echo "  Metrics: localhost:18082 → ${METRICS_IP}:8082"
echo "  Cluster: home-hk1-cluster.orbb.li"
echo ""
echo "Container status:"
docker ps --filter name=$CONTAINER_NAME --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
echo ""
echo "To view logs:"
echo "  docker logs -f $CONTAINER_NAME"
echo ""
echo "To access with ArgoCD CLI:"
echo "  argocd login localhost:$UI_PORT --insecure"
echo ""
echo "To stop the container:"
echo "  docker stop $CONTAINER_NAME"
echo ""
echo "Press Ctrl+C to stop the UI container and cleanup"
echo ""

# Keep script running and follow logs
docker logs -f $CONTAINER_NAME
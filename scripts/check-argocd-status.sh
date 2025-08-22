#!/bin/bash

echo "=============================================="
echo "ArgoCD Status Checker"
echo "=============================================="

echo -e "\n1. Checking ArgoCD pods in devops namespace..."
kubectl get pods -n devops | grep argocd || echo "No ArgoCD pods found!"

echo -e "\n2. Checking ArgoCD services..."
kubectl get svc -n devops | grep argocd || echo "No ArgoCD services found!"

echo -e "\n3. Checking all ArgoCD applications..."
kubectl get applications -n devops || echo "No applications found or ArgoCD CRDs not installed!"

echo -e "\n4. Checking k8s-monitoring application specifically..."
kubectl get applications -n devops k8s-monitoring -o wide 2>/dev/null || echo "k8s-monitoring application not found!"

echo -e "\n5. Checking if observability namespace exists..."
kubectl get namespace observability 2>/dev/null && echo "observability namespace exists" || echo "observability namespace does not exist"

echo -e "\n6. Checking ArgoCD application controller logs (last 10 lines)..."
kubectl logs -n devops deployment/argocd-application-controller --tail=10 2>/dev/null || echo "Could not get application controller logs"

echo -e "\n7. Checking ArgoCD server logs (last 10 lines)..."
kubectl logs -n devops deployment/argocd-server --tail=10 2>/dev/null || echo "Could not get server logs"

echo -e "\n8. Checking recent Kubernetes events..."
kubectl get events --all-namespaces --sort-by='.lastTimestamp' | tail -5

echo -e "\n=============================================="
echo "Quick Fix Commands:"
echo "=============================================="
echo "1. To access ArgoCD UI via port-forward:"
echo "   kubectl port-forward svc/argocd-server -n devops 8080:443"
echo "   Then access: https://localhost:8080"
echo ""
echo "2. To get ArgoCD admin password:"
echo "   kubectl -n devops get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
echo ""
echo "3. To redeploy k8s-monitoring application:"
echo "   kubectl delete application k8s-monitoring -n devops --ignore-not-found"
echo "   ansible-playbook homelab/ansible/02-install/05-install_monitoring.yaml"
echo ""
echo "4. To run detailed diagnostics:"
echo "   ansible-playbook homelab/ansible/02-install/05-install_monitoring_debug.yaml"
echo "==============================================" 
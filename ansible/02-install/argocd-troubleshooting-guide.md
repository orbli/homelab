# ArgoCD Troubleshooting Guide

## Issue: k8s-monitoring Application Not Visible in ArgoCD Portal

### Quick Diagnostic Commands

Run these commands to diagnose the issue:

```bash
# 1. Check if ArgoCD is running
kubectl get pods -n devops | grep argocd

# 2. Check if the application exists
kubectl get applications -n devops

# 3. Check application details
kubectl get applications -n devops k8s-monitoring -o yaml

# 4. Check ArgoCD server logs
kubectl logs -n devops deployment/argocd-server --tail=50
```

### Common Issues and Solutions

#### 1. ArgoCD Not Running
**Symptoms:** No ArgoCD pods in devops namespace
**Solution:** 
```bash
# Reinstall ArgoCD
ansible-playbook homelab/ansible/02-install/03-install_argocd.yaml
```

#### 2. Application in Wrong Namespace
**Symptoms:** Application exists but not visible in UI
**Solution:**
```bash
# Check all namespaces for the application
kubectl get applications --all-namespaces

# If found in wrong namespace, delete and recreate
kubectl delete application k8s-monitoring -n <wrong-namespace>
ansible-playbook homelab/ansible/02-install/05-install_monitoring.yaml
```

#### 3. Repository Access Issues
**Symptoms:** Application shows sync errors
**Solution:**
```bash
# Check if repository is accessible
kubectl get applications -n devops k8s-monitoring -o yaml | grep -A 5 -B 5 "ComparisonError\|SyncError"

# If repository access fails, check network connectivity
kubectl run test-pod --image=curlimages/curl --rm -it -- curl -I https://github.com/orbli/homelab.git
```

#### 4. ArgoCD UI Access Issues
**Symptoms:** Can't access ArgoCD portal
**Solutions:**

**Option A: Port Forward (Recommended for troubleshooting)**
```bash
kubectl port-forward svc/argocd-server -n devops 8080:443
# Access: https://localhost:8080
```

**Option B: Check Service Configuration**
```bash
kubectl get svc -n devops argocd-server
# Verify the service is running and accessible
```

#### 5. Authentication Issues
**Symptoms:** Can't login to ArgoCD
**Solution:**
```bash
# Get admin password
kubectl -n devops get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d

# If using Keycloak, check OIDC configuration
kubectl get configmap -n devops argocd-cm -o yaml | grep -A 10 oidc
```

### Step-by-Step Recovery Process

#### Step 1: Clean and Reinstall
```bash
# 1. Remove the problematic application
kubectl delete application k8s-monitoring -n devops --ignore-not-found

# 2. Wait for cleanup
sleep 10

# 3. Run the debug version
ansible-playbook homelab/ansible/02-install/05-install_monitoring_debug.yaml
```

#### Step 2: Verify ArgoCD Health
```bash
# Check ArgoCD components
kubectl get all -n devops | grep argocd

# Check ArgoCD application controller logs
kubectl logs -n devops deployment/argocd-application-controller --tail=50

# Check ArgoCD server logs
kubectl logs -n devops deployment/argocd-server --tail=50
```

#### Step 3: Manual Application Creation (Alternative)
If the Ansible approach fails, try manual creation:

```bash
cat << EOF | kubectl apply -f -
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: k8s-monitoring
  namespace: devops
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: https://github.com/orbli/homelab.git
    targetRevision: HEAD
    path: homelab/argocd/k8s-monitoring
  destination:
    server: https://kubernetes.default.svc
    namespace: observability
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
EOF
```

### Advanced Troubleshooting

#### Check ArgoCD Configuration
```bash
# Check ArgoCD configuration
kubectl get configmap -n devops argocd-cm -o yaml

# Check ArgoCD RBAC
kubectl get configmap -n devops argocd-rbac-cm -o yaml
```

#### Check Network Policies
```bash
# Check if network policies are blocking communication
kubectl get networkpolicies --all-namespaces
```

#### Check Resource Quotas
```bash
# Check if resource quotas are preventing deployment
kubectl get resourcequotas --all-namespaces
```

### Validation Steps

After fixing the issue, validate:

1. **ArgoCD Portal Access:**
   - Access the ArgoCD UI
   - Verify you can see the k8s-monitoring application

2. **Application Health:**
   ```bash
   kubectl get applications -n devops k8s-monitoring
   ```
   Should show: `HEALTH: Healthy` and `SYNC: Synced`

3. **Resources Deployed:**
   ```bash
   kubectl get all -n observability
   ```
   Should show monitoring components

### Prevention Tips

1. **Always check ArgoCD health before deploying applications**
2. **Use the debug version first when troubleshooting**
3. **Monitor ArgoCD logs regularly**
4. **Ensure proper RBAC permissions**
5. **Verify repository accessibility**

### Contact Information

If issues persist, check:
- ArgoCD documentation: https://argo-cd.readthedocs.io/
- Kubernetes events: `kubectl get events --all-namespaces --sort-by='.lastTimestamp'`
- Cluster resource usage: `kubectl top nodes` and `kubectl top pods --all-namespaces` 
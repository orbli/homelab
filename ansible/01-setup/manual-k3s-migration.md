# Manual K3s Network Migration Guide

This guide provides manual steps to migrate your K3s cluster from `192.168.86.x` to `192.168.88.x` network without reinstalling.

## Prerequisites
- Ensure all nodes can communicate on the new network
- Have backup of important data
- SSH access to all nodes

## Step 1: Stop K3s Services (All Nodes)

```bash
# On all nodes, stop k3s
sudo systemctl stop k3s
sudo systemctl status k3s  # Verify it's stopped
```

## Step 2: Backup K3s Data (All Nodes)

```bash
# Create backup
sudo tar -czf /tmp/k3s-backup-$(date +%Y%m%d-%H%M%S).tar.gz \
  /var/lib/rancher/k3s \
  /etc/rancher/k3s
```

## Step 3: Update Primary Node Configuration (home-hk1-pi1)

```bash
# Update kubeconfig server address
sudo sed -i 's/192\.168\.86\.41/192.168.88.41/g' /etc/rancher/k3s/k3s.yaml

# Update any localhost references to new IP
sudo sed -i 's/server: https:\/\/127\.0\.0\.1:/server: https:\/\/192.168.88.41:/g' /etc/rancher/k3s/k3s.yaml
sudo sed -i 's/server: https:\/\/localhost:/server: https:\/\/192.168.88.41:/g' /etc/rancher/k3s/k3s.yaml

# Update etcd member configuration if using embedded etcd
sudo find /var/lib/rancher/k3s/server/db -name "*.json" -exec sed -i 's/192\.168\.86\.41/192.168.88.41/g' {} \;
```

## Step 4: Update Secondary Nodes Configuration (All Other Nodes)

```bash
# Replace old server IP in agent configuration files
sudo find /var/lib/rancher/k3s/agent -name "*.json" -exec sed -i 's/192\.168\.86\.41/192.168.88.41/g' {} \;

# Update server database references
sudo find /var/lib/rancher/k3s/server -name "*.json" -exec sed -i 's/192\.168\.86\.41/192.168.88.41/g' {} \; 2>/dev/null || true
```

## Step 5: Update CNI Configuration (All Nodes)

```bash
# Update CNI network configurations
sudo find /var/lib/rancher/k3s/agent/etc/cni/net.d -name "*.conflist" -exec grep -l "192.168.86" {} \; | \
  xargs -r sudo sed -i 's/192\.168\.86/192.168.88/g'
```

## Step 6: Start Services in Order

### Start Primary Node First:
```bash
# On home-hk1-pi1 only
sudo systemctl start k3s
sudo systemctl status k3s

# Wait for it to be ready
while ! sudo k3s kubectl get nodes >/dev/null 2>&1; do
  echo "Waiting for k3s to be ready..."
  sleep 10
done
```

### Start Secondary Nodes:
```bash
# On each secondary node (home-hk1-pi2, home-hk1-pi3, home-hk1-pi4)
sudo systemctl start k3s
sudo systemctl status k3s
```

## Step 7: Verify Cluster Status

```bash
# On primary node, check cluster status
sudo k3s kubectl get nodes -o wide
sudo k3s kubectl get pods -A
```

## Step 8: Update Node IP Addresses in Kubernetes

Sometimes the node objects in Kubernetes still have the old IP addresses. Update them:

```bash
# On primary node, get current node IPs
sudo k3s kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.addresses[?(@.type=="InternalIP")].address}{"\n"}{end}'

# If nodes still show old IPs, delete and let them re-register
sudo k3s kubectl delete node home-hk1-pi2 home-hk1-pi3 home-hk1-pi4

# Restart secondary nodes to re-register with new IPs
# On each secondary node:
sudo systemctl restart k3s
```

## Step 9: Update Local Kubeconfig

```bash
# On your local machine, update kubeconfig
sed -i 's/192\.168\.86\.41/192.168.88.41/g' ~/.kube/config

# Test connectivity
kubectl get nodes
```

## Step 10: Update NFS Storage Class

```bash
# Apply the updated storage class
kubectl apply -f k8s_configs/k8s-sc.yaml
```

## Troubleshooting

### If Nodes Don't Join:
1. Check `/var/log/syslog` or `journalctl -u k3s` for errors
2. Verify token files are intact: `/var/lib/rancher/k3s/server/token`
3. Ensure firewall allows traffic on new network
4. Check DNS resolution if using hostnames

### If Pods Don't Start:
1. Check CNI configuration: `ls -la /var/lib/rancher/k3s/agent/etc/cni/net.d/`
2. Restart k3s services: `sudo systemctl restart k3s`
3. Check for network policy conflicts

### Reset if Issues Persist:
```bash
# Emergency reset (last resort)
sudo /usr/local/bin/k3s-uninstall.sh
# Then reinstall using your Ansible playbook
```

## Verification Commands

```bash
# Check cluster health
kubectl get nodes -o wide
kubectl get pods -A
kubectl get svc -A

# Check k3s logs
sudo journalctl -u k3s -f

# Test pod connectivity
kubectl run test-pod --image=nginx --rm -it -- /bin/bash
```

## Post-Migration Tasks

1. Update any applications that reference the old IP addresses
2. Update monitoring configurations (if any)
3. Update backup scripts
4. Test application connectivity
5. Update DNS records if using external DNS 
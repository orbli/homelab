# Homelab Infrastructure

Production-ready Kubernetes homelab running on Raspberry Pi cluster with comprehensive monitoring, identity management, and GitOps deployment.

## 🏗️ Infrastructure

### Hardware
- **4x Raspberry Pi nodes** (home-hk1-pi[1-4])
- **1x NFS Storage Server** (sd1)
- **Network**: 192.168.88.0/24
- **Cluster Domain**: home-hk1-cluster.orbb.li

### Software Stack
- **Kubernetes**: K3s (lightweight Kubernetes)
- **Networking**: Tailscale + Cloudflare Tunnels
- **GitOps**: ArgoCD
- **Monitoring**: Prometheus + Loki + Grafana
- **Identity**: Keycloak
- **Storage**: NFS CSI Driver

## 📁 Repository Structure

```
homelab/
├── README.md                    # This file
├── ansible/                     # Infrastructure automation
│   ├── 01-setup/               # Initial cluster setup
│   ├── 02-install/             # Service deployments
│   ├── applications/           # Application configs
│   ├── inventory.yaml          # Ansible inventory
│   └── secrets/                # Sensitive data (gitignored)
├── kubernetes/                  # Kubernetes configurations
│   ├── namespaces/             # Namespace-specific configs
│   │   ├── devops/            # CI/CD tools
│   │   ├── iam/               # Identity management
│   │   ├── networking/        # Network services
│   │   └── observability/     # Monitoring stack
│   ├── argocd/                # ArgoCD applications
│   └── helm-charts/           # Custom Helm charts
├── monitoring/                  # Monitoring configurations
│   ├── dashboards/            # Grafana dashboards
│   └── alerts/                # Prometheus alerts
├── scripts/                    # Management scripts
│   ├── cleanup-argocd.sh     # ArgoCD removal
│   └── sql/                   # Analysis queries
├── docs/                       # Documentation
│   └── analysis/              # System analysis
└── backup/                     # Backup configurations
```

## 🚀 Quick Start

### Prerequisites
- Ansible installed locally
- kubectl configured
- Access to cluster nodes

### Initial Setup

1. **Configure Ansible Inventory**
   ```bash
   cp ansible/inventory-example.yaml ansible/inventory.yaml
   # Edit inventory.yaml with your node details
   ```

2. **Run Setup Playbooks**
   ```bash
   # Install base infrastructure
   cd ansible
   ansible-playbook 01-setup/05-install_k3s.yaml
   ansible-playbook 01-setup/06-install_helm.yaml
   ansible-playbook 01-setup/07-install_k8s_sc.yaml
   ```

3. **Deploy Core Services**
   ```bash
   # Identity Management
   ansible-playbook 02-install/01-keycloak.yaml
   
   # Networking
   ansible-playbook 02-install/02-cloudflared.yaml
   
   # Monitoring
   ansible-playbook 02-install/04-install_monitoring_storages.yaml
   ansible-playbook 02-install/05-install_monitoring.yaml
   ```

## 🔧 Management

### Common Operations

**Check cluster status:**
```bash
kubectl get nodes
kubectl get pods -A
```

**Access Grafana:**
```bash
kubectl port-forward -n observability svc/kube-prometheus-stack-grafana 3000:80
# Visit http://localhost:3000
```

**Clean up ArgoCD:**
```bash
./scripts/cleanup-argocd.sh
```

### Namespace Overview

| Namespace | Purpose | Key Services |
|-----------|---------|--------------|
| devops | CI/CD & GitOps | ArgoCD |
| iam | Identity Management | Keycloak, PostgreSQL |
| networking | Network Services | Tailscale, Cloudflare |
| observability | Monitoring Stack | Prometheus, Loki, Grafana |

## 📊 Monitoring

### Metrics
- **Prometheus**: System and application metrics
- **Node Exporters**: Hardware and OS metrics
- **Kube State Metrics**: Kubernetes object metrics

### Logs
- **Loki**: Centralized log aggregation
- **Grafana Alloy**: Log collection from all pods

### Dashboards
- Cluster overview
- Node metrics
- Pod resources
- Application-specific dashboards

## 🔐 Security

### Network Security
- Tailscale for secure remote access
- Cloudflare tunnels for public services
- Internal services not exposed directly

### Identity & Access
- Keycloak for centralized authentication
- OIDC integration with services
- RBAC policies for Kubernetes

### Secrets Management
- Ansible vault for sensitive data
- Kubernetes secrets for runtime configs
- **TODO**: Implement Sealed Secrets or External Secrets Operator

## 📝 Documentation

- [DevOps Namespace](kubernetes/namespaces/devops/README.md)
- [IAM Namespace](kubernetes/namespaces/iam/README.md)
- [Networking Namespace](kubernetes/namespaces/networking/README.md)
- [Observability Namespace](kubernetes/namespaces/observability/README.md)

## 🚧 Roadmap

- [ ] Headless ArgoCD deployment
- [ ] Ingress controller (Traefik/Nginx)
- [ ] Certificate management (cert-manager)
- [ ] Backup solution (Velero)
- [ ] Sealed Secrets for secret management
- [ ] Network policies for namespace isolation
- [ ] Automated testing pipeline
- [ ] Disaster recovery procedures

## 🤝 Contributing

1. Create feature branch
2. Test changes in dev environment
3. Update documentation
4. Submit pull request

## 📄 License

Private repository - All rights reserved

## 🆘 Support

For issues or questions, check the [docs](docs/) directory or review namespace-specific READMEs.

---

**Last Updated**: 2025-08-22
**Cluster Version**: K3s on Kubernetes v1.x
**Status**: Production
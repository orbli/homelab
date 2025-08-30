# Homelab Infrastructure

Production-ready Kubernetes homelab running on Raspberry Pi cluster with GitOps-managed infrastructure, comprehensive monitoring, and centralized authentication.

## 🏗️ Infrastructure

### Hardware
- **4x Raspberry Pi nodes** (home-hk1-pi[1-4])
- **1x NFS Storage Server** (sd1)
- **Network**: 192.168.88.0/24
- **Cluster Domain**: home-hk1-cluster.orbb.li (via Tailscale)

### Software Stack
- **Kubernetes**: K3s (lightweight Kubernetes)
- **GitOps**: ArgoCD Core (headless)
- **Networking**: Tailscale + Cloudflare Tunnels
- **Identity**: Keycloak with Google OAuth
- **Monitoring**: Prometheus + Loki + Grafana (OAuth integrated)
- **Log Collection**: Grafana Alloy
- **Storage**: NFS CSI Driver

## 📁 Repository Structure

```
homelab/
├── README.md                    # This file
├── .gitignore                   # Git ignore rules
├── ansible/                     # Infrastructure automation
│   ├── 01-setup/               # Initial cluster setup
│   │   └── k8s_configs/        # Kubernetes configurations
│   ├── 02-install/             # GitOps deployments
│   │   ├── files/              # Jinja2 templates
│   │   │   ├── helm-values/    # Helm chart values
│   │   │   └── *.yaml.j2       # OAuth, secrets templates
│   │   └── *.yaml              # Deployment playbooks
│   └── secrets/                # Sensitive data (gitignored)
├── kubernetes/                  # GitOps-managed resources
│   ├── apps/                   # Kubernetes manifests
│   │   ├── argocd/            # Self-hosting config
│   │   ├── cloudflared/       # Tunnel deployment
│   │   ├── iam/               # Keycloak & identity
│   │   └── observability/     # Monitoring namespace
│   └── gitops/                 # ArgoCD Applications
│       └── apps/               # Application definitions
├── docs/                       # Documentation
└── TODOs/                      # Future projects
```

## 🚀 Quick Start

### Prerequisites
- Ansible installed locally
- kubectl configured with cluster access
- Access to cluster nodes
- Secrets file: `ansible/secrets/keycloak-secrets.yaml`

### Deployment Order

1. **Configure Ansible Inventory**
   ```bash
   cp ansible/inventory-example.yaml ansible/inventory.yaml
   # Edit inventory.yaml with your node details
   ```

2. **Bootstrap GitOps (ArgoCD)**
   ```bash
   cd ansible
   ansible-playbook 02-install/01-install-argocd.yaml
   ansible-playbook 02-install/02-argocd-self-hosting.yaml
   ```

3. **Deploy Core Services**
   ```bash
   # Identity Provider (Keycloak)
   ansible-playbook 02-install/03-deploy-keycloak.yaml
   
   # Networking (Cloudflare Tunnel)
   ansible-playbook 02-install/04-deploy-cloudflared.yaml
   
   # OAuth Configuration for Grafana
   ansible-playbook 02-install/06-config-grafana-oauth.yaml
   
   # Monitoring Stack
   ansible-playbook 02-install/07-deploy-log-collection.yaml  # Loki + Alloy
   ansible-playbook 02-install/08-deploy-prometheus-grafana.yaml
   ```

## 🔧 Management

### Common Operations

**Check cluster status:**
```bash
kubectl get nodes
kubectl get pods -A
ARGOCD_OPTS="--core" argocd app list
```

**Access Services:**
- **Grafana**: https://grafana-lab.orbb.li (OAuth via Keycloak)
- **Keycloak**: https://keycloak-lab.orbb.li/admin
- **Prometheus**: Internal at prometheus-kube-prometheus-prometheus:9090

**Cleanup Playbooks:**
```bash
ansible-playbook 02-install/cleanup-keycloak.yaml
ansible-playbook 02-install/cleanup-cloudflared.yaml
ansible-playbook 02-install/cleanup-observability.yaml
```

### Namespace Overview

| Namespace | Purpose | Key Services | Management |
|-----------|---------|--------------|------------|
| gitops | ArgoCD Core | ArgoCD components | Self-hosted |
| iam | Identity Management | Keycloak, PostgreSQL | ArgoCD + Ansible |
| ingress | Network Access | Cloudflare Tunnel | ArgoCD + Ansible |
| observability | Monitoring Stack | Prometheus, Loki, Grafana, Alloy | Helm + Ansible |

## 📊 Monitoring

### Metrics Collection
- **Prometheus**: System and application metrics (100Gi storage, 30-day retention)
- **Node Exporters**: Hardware and OS metrics from all nodes
- **Kube State Metrics**: Kubernetes object metrics

### Log Aggregation
- **Loki**: Centralized log storage (50Gi, 30-day retention)
- **Grafana Alloy**: Kubernetes API-based log collection
- **Log Sources**: All namespaces and pods

### Visualization
- **Grafana**: OAuth-integrated dashboards
- **Default Dashboards**: Kubernetes cluster, node exporter, pod metrics
- **Access**: https://grafana-lab.orbb.li (SSO via Keycloak)

## 🔐 Security

### Network Security
- **Tailscale**: Secure cluster access (all pods/services routable)
- **Cloudflare Tunnels**: Public service exposure
- **Internal DNS**: home-hk1-cluster.orbb.li domain

### Identity & Access Management
- **Keycloak**: Centralized authentication with Google OAuth
- **Grafana OAuth**: Role-based access (Admin/Editor/Viewer)
- **Protocol Mappers**: Proper role extraction in JWT tokens

### Secrets Management
- **Ansible Templates**: Jinja2 templates for secret generation
- **GitOps Separation**: Secrets managed by Ansible, infrastructure by ArgoCD
- **Runtime Secrets**: Kubernetes secrets for service credentials

## 📝 Key Features

### GitOps Architecture
- **Separation of Concerns**: ArgoCD manages infrastructure, Ansible manages secrets
- **Declarative Deployments**: All resources defined in Git
- **Self-Hosted ArgoCD**: Core mode for resource efficiency

### OAuth Integration
- **Simplified Authentication**: No offline_access tokens
- **Role Mapping**: Automatic role assignment based on email
- **Single Sign-On**: Google OAuth via Keycloak

## 🚧 Roadmap

- [x] Headless ArgoCD deployment (Core mode)
- [x] Keycloak identity provider
- [x] OAuth integration for Grafana
- [x] Comprehensive monitoring stack
- [ ] Ingress controller (Traefik/Nginx) - See TODOs/ingress-project.md
- [ ] Certificate management (cert-manager)
- [ ] Backup solution (Velero)
- [ ] Sealed Secrets or External Secrets Operator
- [ ] Network policies for namespace isolation
- [ ] Automated testing with homelab-playbook-tester agent

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

**Last Updated**: 2025-08-30
**Infrastructure**: K3s on Raspberry Pi cluster
**Architecture**: GitOps with ArgoCD Core
**Status**: Production
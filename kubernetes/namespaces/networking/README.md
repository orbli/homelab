# Networking Namespace

This namespace contains network connectivity and ingress services.

## Components

### Tailscale Operator
- **Deployment**: operator
- **Connector**: ts-home-hk1-cluster-connector
- **OAuth Client ID**: kEqgyLmZ5A11CNTRL
- **Hostname**: home-hk1-cluster-tsop

### Cloudflare Tunnels
- **Replicas**: 2 (HA configuration)
- **Purpose**: External access to cluster services
- **Credentials**: Stored in secrets

## Services

- `ts-home-hk1-cluster-connector-flbwc` - Tailscale connector service
- Cloudflare tunnel endpoints (configured via secrets)

## Installation

```bash
# Install Tailscale Operator
ansible-playbook ansible/01-setup/09-install_ts_k8s_operator.yaml

# Install Cloudflare Tunnels
ansible-playbook ansible/02-install/02-cloudflared.yaml
```

## Configuration Files

- `ts-connector.yaml` - Tailscale connector configuration
- `tunnel-credentials.json` - Cloudflare tunnel credentials (in secrets)

## Network Details

- Cluster advertises routes via Tailscale
- Cloudflare provides public ingress
- Internal services accessible via Tailscale network
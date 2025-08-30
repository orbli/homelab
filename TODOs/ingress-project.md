# Ingress & Authentication Project

## Overview
Transition from Cloudflare Tunnel direct service routing to NGINX Ingress Controller with Keycloak authentication support.

## Current Setup
- **Cloudflare Tunnel**: Directly routes to services (bypasses Kubernetes Ingress)
  - Config location: `configmap/cloudflared` in `networking` namespace
  - Routes: `*.orbb.li` domains to internal services
- **Tailscale Operator**: Installed but not actively used for ingress
- **Keycloak**: Running at `keycloak-lab.orbb.li`

## Proposed Architecture
```
Public Access:
Internet → Cloudflare Tunnel → NGINX Ingress → Services
                                     ↓
                            (Auth via annotations)

Alternative Access:
Internet → Tailscale Funnel (orbb-li.ts.net) → NGINX Ingress → Services
```

## Implementation Steps

### 1. Install NGINX Ingress Controller
- Deploy via ArgoCD (recommended) or Helm
- Configure as `ClusterIP` type (not LoadBalancer)
- Namespace: `ingress-nginx`

### 2. Configure Keycloak for Multi-Domain Support
Update Keycloak client settings:
- Valid Redirect URIs:
  - `https://*.orbb.li/oauth2/callback`
  - `https://orbb-li.ts.net/oauth2/callback`
- Web Origins:
  - `https://*.orbb.li`
  - `https://orbb-li.ts.net`

### 3. Deploy OAuth2 Proxy
Deploy OAuth2 Proxy with multi-domain support:
- Provider: `keycloak-oidc`
- Cookie domains: `.orbb.li,.ts.net`
- Whitelist domains: `.orbb.li,.ts.net`

### 4. Update Cloudflare Tunnel Configuration
Change from direct service routing to NGINX:
```yaml
# From:
- hostname: grafana-lab.orbb.li
  service: http://kube-prometheus-stack-grafana.observability:80

# To:
- hostname: grafana-lab.orbb.li
  service: http://ingress-nginx-controller.ingress-nginx:80
```

### 5. Create Ingress Resources
Create ingress resources with authentication annotations:
```yaml
annotations:
  nginx.ingress.kubernetes.io/auth-url: "https://$host/oauth2/auth"
  nginx.ingress.kubernetes.io/auth-signin: "https://$host/oauth2/start?rd=$escaped_request_uri"
```

### 6. Configure Tailscale Funnel (Optional)
For alternative access via `orbb-li.ts.net`:
- Expose NGINX as NodePort or Tailscale service
- Configure Tailscale Funnel to point to NGINX

## Benefits
- **Centralized Authentication**: All services can use Keycloak auth via simple annotations
- **Standard Kubernetes Patterns**: Use native Ingress resources
- **Flexibility**: Support both public (Cloudflare) and alternative (Tailscale) access
- **Security**: Add authentication without modifying applications

## Services to Protect
- [ ] Grafana (`grafana-lab.orbb.li`)
- [ ] ArgoCD (`argocd-gitops.home-hk1-cluster.orbb.li`)
- [ ] Other internal services as needed

## Technical Notes
- Cookie isolation: Separate sessions for `.orbb.li` and `.ts.net` domains
- Keycloak must remain publicly accessible for auth redirects
- OAuth2 Proxy handles the actual OAuth flow with Keycloak
- NGINX Ingress Controller acts as the reverse proxy implementing routing rules

## Key Concepts Learned
- **Ingress Resource**: Kubernetes API for defining HTTP routing rules (configuration)
- **Ingress Controller**: The actual reverse proxy (NGINX) that implements those rules
- **Current Setup**: Cloudflare Tunnel bypasses Kubernetes Ingress entirely
- **Reverse Proxy Chain**: Can stack multiple reverse proxies (Tailscale → NGINX → Service)

## Resources
- [NGINX Ingress Controller](https://kubernetes.github.io/ingress-nginx/)
- [OAuth2 Proxy](https://oauth2-proxy.github.io/oauth2-proxy/)
- [Keycloak OIDC Documentation](https://www.keycloak.org/docs/latest/securing_apps/#_oidc)
- [Tailscale Kubernetes Operator](https://tailscale.com/kb/1185/kubernetes)
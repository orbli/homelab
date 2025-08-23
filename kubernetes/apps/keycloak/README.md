# Keycloak Deployment

This directory contains the Kubernetes manifests for deploying Keycloak with PostgreSQL backend.

## Features

- Keycloak 23.0 with PostgreSQL 15
- Google OAuth integration
- Custom realm (orbb.li) with admin user
- Persistent storage for database
- Health checks and monitoring enabled
- Ingress configuration for external access

## Prerequisites

1. **Create secrets file**: Copy `ansible/secrets/keycloak-secrets.yaml.example` to `ansible/secrets/keycloak-secrets.yaml` and fill in:
   - Strong passwords for PostgreSQL and Keycloak admin
   - Google OAuth credentials from Google Cloud Console
   - Initial password for mail@orbb.li user

2. **Google OAuth Setup**:
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create or select a project
   - Enable Google+ API
   - Create OAuth 2.0 credentials
   - Add authorized redirect URI: `https://keycloak.orbb.li/realms/orbb.li/broker/google/endpoint`
   - Copy Client ID and Client Secret to secrets file

3. **DNS Configuration**:
   - Add DNS A record: `keycloak.orbb.li` → your cluster ingress IP

4. **Ingress Controller**:
   - Ensure you have an ingress controller installed (e.g., nginx-ingress)
   - Optionally install cert-manager for automatic TLS certificates

## Deployment

Deploy using Ansible and ArgoCD:

```bash
ansible-playbook ansible/02-install/03-deploy-keycloak.yaml
```

## Access

- **Admin Console**: https://keycloak.orbb.li/admin
  - Username: `admin`
  - Password: (from your secrets file)

- **User Realm**: https://keycloak.orbb.li/realms/orbb.li
  - Admin User: `mail@orbb.li`
  - Initial Password: (from your secrets file, temporary)

## Configuration

### Realm Settings

The `orbb.li` realm is configured with:
- Email as username
- Google OAuth integration
- Brute force protection
- Email verification required
- Admin role for mail@orbb.li

### Resource Limits

Configured for Raspberry Pi deployment:
- Keycloak: 1Gi memory, 1 CPU
- PostgreSQL: 512Mi memory, 500m CPU
- Persistent storage: 5Gi for database

Adjust these in the deployment files if needed.

## Troubleshooting

Check pod status:
```bash
kubectl get pods -n keycloak
kubectl logs -n keycloak deployment/keycloak
kubectl logs -n keycloak deployment/postgres
```

Check ArgoCD sync status:
```bash
ARGOCD_OPTS="--core" argocd app get keycloak
```

Force sync if needed:
```bash
ARGOCD_OPTS="--core" argocd app sync keycloak
```

## Security Notes

1. **Change default passwords** immediately after deployment
2. **Enable 2FA** for admin accounts
3. **Restrict Google OAuth** to your domain if needed
4. **Regular backups** of PostgreSQL database recommended
5. **Monitor logs** for suspicious activity

## Backup

To backup Keycloak data:
```bash
# Backup database
kubectl exec -n keycloak deployment/postgres -- pg_dump -U keycloak keycloak > keycloak-backup.sql

# Backup realm configuration
kubectl exec -n keycloak deployment/keycloak -- \
  /opt/keycloak/bin/kc.sh export --dir /tmp --realm orbb.li
```
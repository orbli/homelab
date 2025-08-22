# IAM (Identity and Access Management) Namespace

This namespace contains identity and access management services.

## Components

### Keycloak
- **Version**: Latest (via Helm)
- **Database**: PostgreSQL (StatefulSet)
- **Storage**: 8Gi PVC for PostgreSQL
- **Domain**: keycloak-lab.orbb.li

## Services

- `keycloak` - Main Keycloak service (80/443)
- `keycloak-headless` - Headless service for StatefulSet (8080/8443)
- `keycloak-postgresql` - PostgreSQL database (5432)
- `keycloak-postgresql-hl` - PostgreSQL headless service

## Installation

```bash
# Install Keycloak
ansible-playbook ansible/02-install/01-keycloak.yaml
```

## Configuration

- OIDC integration with ArgoCD
- Realm: orbb.li
- Client ID for ArgoCD: `argocd`

## Access

- Admin Console: https://keycloak-lab.orbb.li
- Database: PostgreSQL on port 5432 (internal only)
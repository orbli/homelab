# Kubernetes Resources Structure

## Directory Layout

```
kubernetes/
├── apps/
│   └── iam/
│       ├── namespace/           # Namespace definition (GitOps managed)
│       │   ├── namespace.yaml
│       │   └── kustomization.yaml
│       └── operator/            # Keycloak operator resources
│           ├── keycloak-cr.yaml
│           ├── postgres-deployment.yaml
│           └── kustomization.yaml
└── gitops/
    ├── apps/                    # ArgoCD applications
    │   ├── keycloak-namespace-app.yaml
    │   ├── keycloak-operator-app.yaml
    │   └── kustomization.yaml
    └── argocd-core/            # ArgoCD self-management
        ├── argocd-self-host.yaml
        ├── argocd-self-management-rbac.yaml
        ├── default-project.yaml
        ├── kustomization.yaml
        └── namespace.yaml
```

## Separation of Concerns

### GitOps (ArgoCD) Manages:
- **Namespace creation** - via `keycloak-namespace-app`
- **Operator deployment** - via `keycloak-operator-app`
- **PostgreSQL deployment** - included in operator resources
- **Infrastructure components** - stateless, declarative

### Ansible Manages:
- **Secret creation** - Generated passwords, OAuth credentials
- **Realm configuration** - KeycloakRealmImport with sensitive data
- **Configuration with secrets** - Templates with sensitive values

### Why This Separation?

1. **Security**: No secrets (even placeholders) in Git
2. **Simplicity**: No complex patching or ignoreDifferences
3. **Clear ownership**: GitOps for infrastructure, Ansible for configuration
4. **Reproducibility**: Clean deployment from scratch every time

## Deployment Flow

1. **ArgoCD creates namespace** via `keycloak-namespace` app
2. **Ansible creates secrets** directly in the namespace
3. **ArgoCD deploys operator** which uses the pre-existing secrets
4. **Ansible applies realm** configuration with OAuth settings

## Key Principles

- **No `kind: Secret` in Git** - Not even templates
- **Secrets created once** - By Ansible at deployment time
- **Applications reference secrets** - By name only
- **GitOps manages structure** - Namespaces, deployments, services
- **Ansible manages sensitive data** - Passwords, API keys, OAuth credentials
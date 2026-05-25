# Minimize Keycloak authorizations exposed in tokens

## What we observed

When orb.li (the realm's primary user) logs in via filebrowser-gate's
oauth2-proxy, the `X-Forwarded-Groups` header arrives at the upstream
containing:

```
mgroup
role:account:manage-account
role:account:manage-account-links
role:account:view-profile
role:realm-management:view-realm
role:realm-management:view-identity-providers
role:realm-management:manage-identity-providers
role:realm-management:impersonation
role:realm-management:realm-admin
role:realm-management:create-client
role:realm-management:manage-users
role:realm-management:query-realms
role:realm-management:view-authorization
role:realm-management:query-clients
role:realm-management:query-users
role:realm-management:manage-events
role:realm-management:manage-realm
role:realm-management:view-events
role:realm-management:view-users
role:realm-management:view-clients
role:realm-management:manage-authorization
role:realm-management:manage-clients
role:realm-management:query-groups
```

## Why this happens

oauth2-proxy's `keycloak-oidc` provider builds `X-Forwarded-Groups` by
combining:
- The actual `groups` claim (currently `["mgroup"]`).
- The user's realm + client roles from `realm_access.roles` and
  `resource_access.<client>.roles`, prefixed `role:<scope>:<roleName>`.

The orb.li user has a long list of `realm-management` roles because
during initial bootstrap the user was given full realm-admin (so they
can manage Keycloak via UI/kcadm without separate creds). Those roles
shouldn't reach the filebrowser upstream.

## Risk

- Leaks the user's privilege surface to every authenticated upstream.
  If filebrowser (or any future upstream) ever logs or stores the
  header, the realm's auth model is exposed.
- The token itself is larger than necessary, which inflates cookie
  size (encrypted cookie wraps the full token) — at the upper end this
  can hit cookie-size limits in browsers.

## Options to fix (any combination)

1. **Separate the admin identity from the personal identity.** Create
   a `orb.li-admin` user record with realm-management roles for Keycloak
   ops, and keep the federated `orb.li` user clean (only `mgroup`).
   Most idiomatic; matches the "don't sign in as root" principle.

2. **Strip role claims at the client scope level.** Remove the realm
   `roles` client scope from the filebrowser-gate client's
   defaultClientScopes (and/or specifically `role_list` mapper). The
   token would then carry only the `groups` claim, not the roles.

3. **Configure oauth2-proxy to ignore roles.** Investigate whether
   oauth2-proxy's keycloak-oidc provider has a knob to skip the
   role-to-group conversion. Likely requires reading the provider's
   source; not all settings are documented.

4. **Filter at the audience level.** Add a per-client scope that
   explicitly excludes the realm-management role mappings from
   filebrowser-gate's tokens. Most surgical.

Recommended: combination of (1) for proper privilege separation and (2)
for the cleanest token shape. Both can be done in
`keycloak-config/realms/orbb.li.yaml` once we add the relevant client
scope manipulation.

## Open question

- Whether to do this before or after we expose filebrowser publicly. If
  the public exposure has a small initial user list (just you + a
  friend or two), the practical leak risk is low and this can wait.
- If we later add Grafana or other RBAC-aware apps that DO want the
  roles claim, we'd want roles in those clients' tokens but not in
  filebrowser's. Per-client scope assignment makes that easy.

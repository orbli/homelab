# keycloak-config — declarative Keycloak state via in-cluster ArgoCD

This directory holds the **declarative, git-authoritative** portion of the
orbb.li Keycloak realm. It is reconciled into the live Keycloak by
[`adorsys/keycloak-config-cli`](https://github.com/adorsys/keycloak-config-cli)
running as a Kubernetes Job *inside the cluster*, triggered by ArgoCD
whenever this YAML changes.

## Why this exists

`KeycloakRealmImport` (the Ansible-driven realm bootstrap) is destructive on
re-import — it wipes anything not in the file. That's appropriate for
first-time realm creation, but unsuitable for the things that change often
(group memberships, mappers). This directory is the non-destructive,
frequent-change layer that complements the bootstrap.

## Why ArgoCD and not GitHub Actions

The Keycloak admin password is sensitive enough that we don't want it
leaving the cluster. The alternative design — a GitHub Actions workflow
running `keycloak-config-cli` against the public `keycloak-lab.orbb.li`
hostname with the admin password as a GH secret — was rejected because:
- Supply-chain risk: a compromised action (or workflow edit) could
  exfiltrate the admin password.
- External-network exposure: every CI run hits the public Keycloak
  endpoint instead of the internal service.

The in-cluster Job pattern:
- Mounts `keycloak-initial-admin` secret directly from the `iam`
  namespace — the password never serializes to anywhere outside the
  cluster.
- Talks to `keycloak-service.iam.svc.cluster.local:8080` — internal
  service, no public hostname or TLS termination involved.
- Triggered by ArgoCD when this directory changes on master — same
  GitOps trigger model as every other manifest in the repo.

## Ownership boundary

Three actors touch Keycloak. Each owns a disjoint slice:

| Actor | Owns | Frequency |
|---|---|---|
| `KeycloakRealmImport` (Ansible bootstrap) | Realm shell: name, login flows, identity providers, themes, SMTP, password policy | Once per realm lifetime |
| `keycloak-oauth-client-create.yaml.j2` (Ansible per-app) | **Initial** OAuth client secret generation + writing secret to the consuming app's k8s Secret (e.g. `default/remark42-oauth-client`). After first install, this template is dormant. | Once per app install |
| **This directory** (`keycloak-config-cli`, in-cluster Job) | Groups, users, group memberships, client *declarations* (redirect URIs, flows, scopes, mappers — but NOT the secret value), custom client scopes, custom protocol mappers | Every git push |

This is the **hybrid ownership** model. Clients live in YAML for their
shape (redirect URIs, flows, scopes) but their *secret values* still
live in Keycloak + per-app k8s Secrets. `keycloak-config-cli` is
documented to preserve an existing client secret when the `secret`
field is omitted from YAML, which is what we rely on.

The Job sets `IMPORT_MANAGED_CLIENT=no-delete` so a client present in
Keycloak but absent from this YAML is left alone (defends against
accidentally wiping Keycloak built-ins like `account`, `admin-cli`,
`realm-management`).

Do NOT redeclare realm-level bootstrap fields here — `keycloak-config-cli`
reconciles each declared field, so omitting a field is fine but
declaring it half-empty will reset it.

## How to add or remove someone from a group

1. Edit `realms/orbb.li.yaml`.
2. Under `users:`, add or remove the email under the relevant `groups:` list.
3. Commit + push to `master`.
4. ArgoCD detects the diff within its poll interval (~3 min) and runs
   the reconciliation Job. Watch with:

   ```bash
   kubectl -n iam get jobs -w
   kubectl -n iam logs job/keycloak-config-cli -f
   ```

Caveat: new users only exist in Keycloak after their first Google sign-in
unless pre-created here. To pre-create, add the YAML entry as in the file's
existing example (`username: <email>`, `email: <email>`, no credentials —
Google IdP federates by email match on first login).

## Delivery mechanism (where each piece lives)

```
homelab/
├── keycloak-config/
│   ├── README.md                                  ← this file
│   └── realms/
│       └── orbb.li.yaml                           ← the data you edit
├── kubernetes/manifests/keycloak-config-cli/
│   ├── kustomization.yaml                         ← wraps the YAML in a hashed ConfigMap
│   └── job.yaml                                   ← the reconciler (PostSync hook)
├── kubernetes/argocd/keycloak/
│   └── keycloak-config-cli-app.yaml               ← ArgoCD Application pointing at the manifests above
└── kubernetes/manifests/argocd/
    └── argocd-cm-patch.yaml                       ← adds --load-restrictor=LoadRestrictionsNone so
                                                     kustomize can load the YAML across directory boundaries
```

The cross-directory hop (`kustomization.yaml` loads `../../../keycloak-config/realms/orbb.li.yaml`)
is what lets you edit the data file at a human-friendly path while the
ArgoCD-facing manifests stay co-located with all the other k8s manifests.

## Onboarding a new person to mgroup

Two-line YAML edit + push. The friend then signs in via Google and lands
in filebrowser immediately — no profile-review or confirm-link prompts,
because the realm uses a customised First Broker Login flow named
**`first broker login auto-link`** (set up live; see "Realm-level
configuration done out-of-band" below).

1. Edit `realms/orbb.li.yaml`:

   ```yaml
   users:
     - username: orb.li
       email: mail@orbb.li
       enabled: true
       emailVerified: true
       groups: [mgroup]
     - username: alice              # ← new entry; pick any unique username
       email: alice@gmail.com       # ← MUST match what Google will return
       enabled: true
       emailVerified: true
       groups: [mgroup]
   ```

2. Commit + push. ArgoCD sync runs `keycloak-config-cli` → user record
   appears in Keycloak with no credentials and mgroup membership.

3. Send the friend the filebrowser URL. They sign in with Google.
   Keycloak silently links Google's identity to the pre-created user
   record (because email matches and `trustEmail=true` on the Google
   IdP). Token includes `groups: ["mgroup"]`. oauth2-proxy lets them
   through. They see the file listing on first click.

If you forget to pre-create them and they sign in first anyway, they
get a Forbidden page from oauth2-proxy (no mgroup in token). You then
add their email to the YAML, push, and they retry — works. The
auto-link flow doesn't care whether you pre-created or post-created.

## Realm-level configuration done out-of-band

These are mutations to the realm that don't live in this YAML yet.
Documented here so they're visible:

| Configuration | Why | Owner |
|---|---|---|
| First Broker Login flow `first broker login auto-link` exists, and Google IdP's `firstBrokerLoginFlowAlias` points at it | Eliminates Review Profile + Confirm Link + Verify-by-Email prompts for pre-created federated users, enabling the one-click onboarding flow above | Live kcadm change (could be promoted into YAML via `authenticationFlows:` declaration in a future PR) |
| Realm `sslRequired=external` | Allows plain HTTP redirect URIs for RFC1918 LAN addresses (needed for the gate test on `192.168.88.254:14180`) | Realm default (also set in KeycloakRealmImport) |
| Google IdP `trustEmail=true` | Required for auto-link to silently establish the federation link without an email-verification round trip | Realm default (also set in KeycloakRealmImport) |

## Running locally for testing

Useful when you want to validate YAML changes against the live Keycloak
without going through a git push + ArgoCD cycle. Uses kubectl port-forward
to reach the internal Keycloak service:

```bash
kubectl -n iam port-forward svc/keycloak-service 8080:8080 &
PASS=$(kubectl -n iam get secret keycloak-initial-admin -o jsonpath='{.data.password}' | base64 -d)

docker run --rm \
  -v "$(pwd)/realms:/config:ro" \
  --network host \
  -e KEYCLOAK_URL=http://localhost:8080 \
  -e KEYCLOAK_USER=temp-admin \
  -e KEYCLOAK_PASSWORD="$PASS" \
  -e IMPORT_FILES_LOCATIONS='/config/*.yaml' \
  -e IMPORT_MANAGED_GROUP=full \
  -e IMPORT_MANAGED_USER=no-delete \
  -e IMPORT_MANAGED_CLIENT=no-delete \
  -e IMPORT_MANAGED_CLIENT_SCOPE=no-delete \
  adorsys/keycloak-config-cli:6.4.0-26.0.1
```

There is no true dry-run mode in `keycloak-config-cli`; the above run
will apply changes. For a real preview, compare against
`kcadm.sh get realms/orbb.li` output.

## Rotating a client secret

Because secret values are not in this YAML, rotation requires touching
two places in lockstep (Keycloak + the per-app k8s Secret). Until we
introduce Sealed Secrets or similar (see "Future direction"), the flow
is:

```bash
# 1. Generate a new secret value
NEW=$(tr -dc 'a-zA-Z0-9' < /dev/urandom | fold -w 32 | head -n 1)

# 2. Rotate in Keycloak
PASS=$(kubectl -n iam get secret keycloak-initial-admin -o jsonpath='{.data.password}' | base64 -d)
kubectl -n iam exec -i keycloak-0 -- env KC_PASS="$PASS" NEW="$NEW" bash -c '
  /opt/keycloak/bin/kcadm.sh config credentials \
    --server http://localhost:8080 --realm master \
    --user temp-admin --password "$KC_PASS" >/dev/null
  CID=$(/opt/keycloak/bin/kcadm.sh get clients -r orbb.li \
    -q clientId=remark42 --fields id --format csv --noquotes | tail -1 | tr -d "\r\n")
  /opt/keycloak/bin/kcadm.sh update clients/$CID -r orbb.li -s secret="$NEW"
'

# 3. Rotate in the consuming app's k8s Secret
kubectl -n default patch secret remark42-oauth-client \
  --type='json' -p="[{'op': 'replace', 'path': '/data/client-secret', 'value': '$(printf %s "$NEW" | base64 -w0)'}]"

# 4. Restart the app to pick up the new secret
kubectl -n default rollout restart deployment/remark42
```

(Substitute `remark42` / `default/remark42-oauth-client` for whichever
client you're rotating.)

## Future direction

- **Install Sealed Secrets** (`bitnami-labs/sealed-secrets`) so client
  secret values can be encrypted-and-committed to this directory and
  let `keycloak-config-cli` own them too via `$(env:CLIENT_SECRET_X)`
  substitution. Removes the two-step rotation flow above and finally
  retires `keycloak-oauth-client-create.yaml.j2` from Ansible.
- **Replace `temp-admin` with a dedicated service-account client** with
  `realm-admin` role scoped to `orbb.li` only, so the Job no longer
  needs master-realm credentials at all. Useful day when this directory
  manages multiple realms.

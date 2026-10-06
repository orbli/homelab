# Tailnet ACL as code

This directory holds the **declarative, git-authoritative** ACL for
the orbb.li primary tailnet. It is reconciled to Tailscale's
control plane by `.github/workflows/tailscale-sync.yml` on every push
to `master` that touches `tailscale/**`. The tailnet identifier is
not stored in this repo; it lives in the `TS_TAILNET` GH Actions
secret consumed by the workflow.

## Why this exists

Tailscale ACLs were previously edited only through the admin console
at https://login.tailscale.com/admin/acls — no version history beyond
"last edited by whom on what date", no review process, no way to roll
back a regrettable rule change beyond manual restore from memory.
Putting the ACL in git fixes all of that: every change is a reviewable
PR, every change has authorship + a message, every change is reversible
with `git revert`.

## One-time bootstrap

Before the first push of this directory, complete these steps so the
workflow has credentials to talk to Tailscale's API.

### 1. Save your current live ACL

The first successful push WILL OVERWRITE whatever is currently live in
your tailnet. The template in `acl.hujson` is the minimum viable ACL
(you-as-admin + friend-group-restricted-to-filebrowser); if your live
ACL has additional rules, copy them in BEFORE the first push.

To see the current live ACL: https://login.tailscale.com/admin/acls

### 2. Create a Tailscale OAuth client

1. Go to https://login.tailscale.com/admin/settings/oauth
2. Click "Generate OAuth client..."
3. Description: `gitops-pusher`
4. Scopes:
   - `policy_file` (read+write) — the ONLY scope needed. This client
     cannot list devices, remove users, change tailnet settings, etc.
5. Tags: leave empty
6. Click "Generate client"
7. Copy the displayed `Client ID` and `Client Secret` (the secret is
   shown ONCE; save it now or you'll have to regenerate).

### 3. Add the credentials as GitHub Actions secrets

From the homelab repo:

```bash
gh secret set TS_API_CLIENT_ID --body "<client-id>"
gh secret set TS_API_CLIENT_SECRET --body "<client-secret>"
```

Or via the GitHub UI: Repo → Settings → Secrets and variables → Actions.

## Membership model: deny-all until named by email

There are no groups and no `autogroup:member` rules. Every place the
policy asks "who may do this" lists the current members by email:

- `acls` NAS-gate rule (`company-hk3-nas:14180`)
- `acls` exit-node rule (`autogroup:internet:*`, any exit node)
- `ssh` self-SSH rule (`autogroup:self`)
- `nodeAttrs` `funnel`

A freshly invited user therefore matches nothing: their client shows
only their own devices (Tailscale trims the netmap to reachable
peers). Enable **Settings → User management → Manually approve new
users** in the admin console as well, so the invite link alone does
not admit anyone.

### Add a friend to filebrowser access

1. **`keycloak-config/realms/orbb.li.yaml`** — add the friend's email
   to `users:` with `groups: [mgroup]`. (Lets them through oauth2-proxy
   once they auth via Keycloak.)
2. **`tailscale/acl.hujson`** — add the friend's email to the `src` of
   the NAS-gate rule (and, if wanted, the exit-node / self-SSH / funnel
   lists). Push; `gh run watch`.
3. Send the friend a Tailscale invite (admin console:
   Users → Invite) and the URL `http://company-hk3-nas:14180/`.

Both layers are needed: Tailscale gates network access; Keycloak gates
application access.

### Guest on the Sparks only (pattern used for kunbu@kunbu.com)

Three rules, all keyed by email, nothing else touched:

- `acls`: email → `home-hk2-spark1:*`, `home-hk2-spark2:*` (hosts
  aliases, stable tailnet IPs; drop one to limit to a single Spark).
- `ssh`: email → `tag:spark`, `users: ["<unixuser>"]`, `check`. The
  unix user must exist on the box, locked password, NOT in `sudo` or
  `docker`. Only spark1 runs Tailscale SSH today.
- `grants`: email → `autogroup:internet` **via** `tag:k8s`, which among
  exit-advertising nodes is only `home-hk1-cluster-connector`. The
  guest must NOT be in the unpinned exit-node `acls` rule.

`tests` / `sshTests` assert each of these so a later edit cannot
silently widen the guest's reach.

### tag:spark

`home-hk2-spark1/2` are meant to carry `tag:spark` and nothing else.
The tag is never a `src`, so a Spark can be reached but can never
initiate a tailnet connection. Retagging is done in the admin console
(Machines → … → Edit ACL tags); the on-device prefs carry no tags.

## How the sync works

| Event | What runs |
|---|---|
| PR opened/updated touching `tailscale/**` | `acl-test` job — runs `gitops-pusher test`, which dry-runs the policy against Tailscale's validator and reports any drift between this file and the live state. Fails the PR check if syntax errors or drift is detected. |
| Push to `master` touching `tailscale/**` | `acl-apply` job — pushes the file to Tailscale's control plane. Live takes effect within seconds. |
| `workflow_dispatch` (manual) | Same as push — useful for re-syncing if you suspect drift. |

The workflow uses `tailnet: "-"` which tells `gitops-pusher` to operate
on whichever tailnet the OAuth client belongs to. If you ever have
multiple tailnets, this would need to be hardcoded.

## Ownership boundary

| Actor | Owns | Frequency |
|---|---|---|
| **This directory** (`tailscale/acl.hujson`) | The ACL: groups, hosts, acls rules, ssh rules, tests | Every git push that touches the file |
| Tailscale admin console | Tailnet-wide settings: user invitations, device approval, DNS, ACL drafts (don't draft here — drift) | One-shot operations like inviting a friend |
| Tailscale OAuth client (`gitops-pusher`) | Pushing the policy file via API | Triggered by the workflow |

Specifically: do NOT edit the ACL in the admin console after this is
set up. Any console edit will be silently overwritten by the next git
push. If you do need an emergency change, edit the admin console then
IMMEDIATELY mirror the change into this file and push.

## Drift detection

`gitops-pusher test` reports drift if the live ACL differs from this
file (e.g., someone forgot the rule above and edited the console).
Drift shows up in PR check output. To force-resync, push an empty
commit:

```bash
git commit --allow-empty -m "force tailscale ACL resync"
git push
```

## Format reference

The file is HuJSON — JSON with `//` comments + trailing commas +
multi-line strings allowed. Tailscale's docs:
- ACL syntax: https://tailscale.com/kb/1018/acls
- GitOps for ACLs: https://tailscale.com/kb/1207/sync-acls-from-github
- gitops-pusher action: https://github.com/tailscale/gitops-pusher

## Common ACL patterns reference

```hujson
// Grant a single user access to one device:
{"action": "accept", "src": ["bob@gmail.com"], "dst": ["nas:14180"]},

// Grant a group access to multiple ports on a host:
{"action": "accept", "src": ["group:friends"], "dst": ["nas:14180,9090"]},

// Grant access to a CIDR (subnet route):
{"action": "accept", "src": ["group:admins"], "dst": ["10.42.0.0/16:*"]},

// Tag-based: any device tagged tag:server is reachable by group:admins:
{"action": "accept", "src": ["group:admins"], "dst": ["tag:server:*"]},
```

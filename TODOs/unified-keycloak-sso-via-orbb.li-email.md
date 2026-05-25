# Unified Keycloak SSO across Tailscale + filebrowser, gated on @orbb.li emails

## Why

Today, two authentication paths exist with NO shared identity:

1. **Tailscale tailnet membership** — friends authenticate to Tailscale
   directly via Google (using their @gmail.com address). Tailnet ACL
   then grants them network reach to filebrowser-gate.

2. **Keycloak-gated filebrowser access** — friends authenticate to
   Keycloak (which itself federates to Google), and Keycloak realm
   membership / mgroup controls oauth2-proxy → filebrowser access.

Splits mean **two places to add/remove a friend** (Tailscale users
list + Keycloak `users:` YAML). It also means we can't enforce any
custom identity policy on the tailnet layer — Tailscale only sees the
friend's @gmail.com identity, not their Keycloak realm membership.

End state we want:

```
Friend visits anything orbb.li-related (filebrowser-gate, or attempts
to join the tailnet):
  → both auth flows redirect to the SAME Keycloak realm orbb.li
  → friend authenticates via Keycloak's Google IdP (single login UX)
  → both layers (Tailscale, filebrowser) use the same Keycloak user
    record as the source of truth for "who am I + what am I in"
  → adding/removing a friend = one YAML edit in
    keycloak-config/realms/orbb.li.yaml
```

## What it requires

Tailscale's custom OIDC feature is **free on the Free plan** (up to 6
users on the tailnet — see https://tailscale.com/pricing). Setup
gotcha: Tailscale uses **WebFinger** to decide which users go through
the custom OIDC provider. The lookup is per-email:

```
Friend enters their email at Tailscale login.
Tailscale queries:
  https://<email-domain>/.well-known/webfinger?resource=acct:<email>
If the response says "use this OIDC issuer":
  Tailscale redirects auth to that issuer (our Keycloak).
Otherwise:
  Tailscale uses its built-in providers (Google for @gmail.com, etc.)
  — BYPASSING our Keycloak entirely.
```

**Consequence**: only users with emails on a domain we control
(orbb.li) can be routed through our Keycloak. A friend whose email
is `alice@gmail.com` will continue authenticating to Tailscale via
Google directly, never touching Keycloak.

**Implication for friend onboarding**: friends need `@orbb.li` email
addresses. We don't need them to actually USE @orbb.li email as a
mailbox — the address is just an identifier — but the address has to
exist enough that the friend can receive a verification email at it
(forwarded somewhere they can read).

This is where the **email infrastructure** prerequisite comes in.

## Email infrastructure prerequisite

### Goal

Make `friend@orbb.li` addresses work as identifiers that forward to
the friend's real inbox (e.g. their @gmail.com), so friends can
verify the address but don't need a new mailbox to check. AND in the
process, set things up so we can selectively route SOME @orbb.li
addresses to a self-hosted Roundcube webmail if we ever want that.

### Architecture

Cloudflare Email Routing as the routing decision-maker. MX record
for orbb.li points at Cloudflare's mail servers. Per-rule routing:

```
                  Incoming email to *@orbb.li
                           │
                           ▼
            ┌──────────────────────────────────┐
            │ Cloudflare Email Routing          │
            │                                   │
            │  Rules:                           │
            │   alice@orbb.li → alice@gmail.com │
            │   bob@orbb.li   → bob@whatever    │
            │   support@orbb.li → roundcube..   │
            │   billing@orbb.li → roundcube..   │
            │   *@orbb.li     → my-personal..   │← catch-all
            └─────────────┬────────────────────┘
              match by recipient address
                          │
            ┌─────────────┴────────────────────┐
            ▼                                  ▼
   ┌─────────────────────┐          ┌───────────────────────┐
   │ Self-hosted Postfix │          │ Friend's real Gmail   │
   │ (publicly reachable │          │ (or whatever inbox)   │
   │  port 25)           │          │  → they read it       │
   │  → Maildir          │          │    in their UI        │
   │  → Roundcube UI     │          │  → reply works        │
   └─────────────────────┘          └───────────────────────┘
```

Per friend: add one rule `friend@orbb.li → friend@their-real-domain`.
For specific lab-owned addresses we want in Roundcube: rule pointing
at our own Postfix server's MX domain.

### Self-hosted Postfix sub-problem (the harder part)

The Roundcube destination leg needs a publicly-reachable Postfix on
port 25. Two hurdles:

1. **Residential ISPs commonly block inbound port 25.** Workaround:
   rent a $3-5/month VPS with public port 25 unblocked.

2. **The VPS Postfix needs to deliver back to our actual storage.**
   Cleanest pattern using existing infrastructure:

   ```
   Internet → VPS:25 → Postfix on VPS
             → (joined to tailnet via Tailscale, tag:homelab)
             → relays over tailnet to homelab Postfix on :5870
             → delivers to local Maildir
             → Roundcube serves it via webmail UI
   ```

   The `tag:homelab` + `autoApprovers.exitNode` we already configured
   make joining the VPS to the tailnet seamless. The tailnet hop is
   encrypted (WireGuard) so the relay traffic is private.

### Setup steps for Phase 1 (email)

- [ ] Cloudflare Dashboard → orbb.li → Email → enable Email Routing.
      Cloudflare automatically adds the right MX + TXT (SPF) records.
- [ ] Add the catch-all rule: `*@orbb.li → <my-real-email>`.
- [ ] Test by sending email to a random `whatever@orbb.li` and confirm
      it lands in <my-real-email>.
- [ ] (If/when we want Roundcube) provision VPS, install Postfix +
      Tailscale + tag:homelab, configure as MX for a Roundcube
      subdomain. Cloudflare rules for specific lab usernames point at
      that subdomain. Postfix in homelab serves Roundcube on Maildir.

The catch-all step is all that's required for the Keycloak/Tailscale
SSO use case below — Roundcube is bonus.

## Phase 2: Tailscale custom OIDC via Keycloak

Once `*@orbb.li` is a valid forwarding domain, we can set up Tailscale
to recognize `*@orbb.li` users as "go through my Keycloak."

### Setup steps

- [ ] **WebFinger endpoint** on orbb.li. Serve a static JSON file at
      `https://orbb.li/.well-known/webfinger` that returns:

      ```json
      {
        "subject": "acct:${email}",
        "links": [
          {
            "rel": "http://openid.net/specs/connect/1.0/issuer",
            "href": "https://keycloak-lab.orbb.li/realms/orbb.li"
          }
        ]
      }
      ```

      The `${email}` is whatever was queried; the issuer URL must
      exactly match Keycloak's `.well-known/openid-configuration`
      issuer field. Easiest hosting: Cloudflare Workers (script
      generates the JSON dynamically per query), or a static file
      served by the orbli Hugo site at the right path. Validate with
      https://webfinger.net.

- [ ] **Keycloak client for Tailscale**. Add to
      `keycloak-config/realms/orbb.li.yaml` clients section:

      ```yaml
      - clientId: tailscale
        name: 'Tailscale tailnet auth'
        protocol: openid-connect
        publicClient: false
        standardFlowEnabled: true
        redirectUris:
          - 'https://login.tailscale.com/a/oauth_response'
        defaultClientScopes:
          - profile
          - roles
          - email
      ```

      Secret stays in Keycloak (Ansible-owned, per hybrid pattern).

- [ ] **Tailscale admin console** → User management → Add custom
      OIDC provider. Configure:
      - Issuer URL: `https://keycloak-lab.orbb.li/realms/orbb.li`
      - Client ID: `tailscale`
      - Client secret: <from Keycloak>
      - Required scopes: `openid profile email`

- [ ] **Test**: open a browser at `login.tailscale.com`, enter a
      `whatever@orbb.li` email. Should redirect to Keycloak → Google
      IdP → back to Tailscale, signed in.

- [ ] **Friend migration**: existing tailnet users who joined as
      @gmail.com don't auto-migrate. Friends would need to:
      1. Get a `friend@orbb.li` address (we create the Cloudflare
         forward rule).
      2. Re-join the tailnet using the @orbb.li address.
      3. Old @gmail.com tailnet identity gets removed.

## Friend onboarding after both phases land

```
1. Edit one YAML file (keycloak-config/realms/orbb.li.yaml):
   - Add user record { username: friend@orbb.li, email: friend@orbb.li,
     groups: [mgroup] }
   - git push → ArgoCD syncs → Keycloak has the user

2. Add one Cloudflare routing rule:
   - friend@orbb.li → friend's real inbox

3. Send friend the Tailscale invite link.
   Friend enters friend@orbb.li → Tailscale WebFinger lookup →
   redirected to your Keycloak → Google IdP → back to Tailscale,
   signed in, joined your tailnet, automatically in mgroup.

4. Friend visits company-hk3-nas:14180:
   - Tailscale ACL: autogroup:member → company-hk3-nas:14180 ✓
   - oauth2-proxy: redirects to Keycloak (same realm friend just
     logged into) → SSO recognizes existing session → no second
     login prompt → checks mgroup membership → ✓
   - Filebrowser opens.

Total touchpoints: one YAML edit + one Cloudflare rule + one invite.
```

## Open decisions / risks

- **Do we actually want to do this?** The current setup works. The
  win is operational simplicity (single user-management point) and a
  unified audit trail (all access decisions visible in Keycloak logs).
  Cost: setup time, ongoing email infra maintenance. Not urgent.
- **Free plan cap is 6 users.** Today: mail@orbb.li, zura35@gmail.com,
  + room for ~4 friends. If we exceed 6, paid Standard plan starts
  at ~$6/user/month. Not a hard blocker now, but worth knowing.
- **Cloudflare Email Routing reliability.** It's been stable, but
  it's a single dependency for both "friends can receive verification
  emails" and (later) "Roundcube delivery." If it goes down, both
  break. Acceptable for homelab.
- **Email deliverability if we ever SEND from @orbb.li.** Cloudflare
  Email Routing handles inbound only. Outbound (replies from
  Roundcube) needs separate setup: Postfix sends via Gmail as smart
  host, or via a transactional service like Postmark. Adds complexity
  but only needed if Roundcube is actually used interactively.
- **WebFinger as a Cloudflare Worker vs. static file.** Worker is
  more flexible (returns the same answer for any email, but only
  emails Tailscale would care about ever query it). Static file is
  simpler but requires knowing the email format ahead of time.
  Recommend Worker.
- **Existing tailnet user mail@orbb.li would need to re-authenticate
  through Keycloak** after the custom OIDC config lands. One-time
  click; not disruptive.
- **The 100% of-this-stack-uses-Keycloak future state has lockout
  risk**: if Keycloak goes down, BOTH filebrowser AND new
  tailnet sign-ins fail. Existing tailnet sessions survive (don't
  re-validate against Keycloak per packet), so the blast radius is
  bounded to "no new auth until Keycloak is back." Mitigation:
  maintain mail@orbb.li as an admin escape hatch by also keeping
  Tailscale's built-in Google auth path available (which Tailscale
  does by default — built-in providers are an alternative, not
  exclusive). Worth confirming during setup.

## Adjacent ideas this unlocks

- **Grafana, remark42, any future app** can use the same Keycloak
  realm + mgroup as the gate. Same auth model end-to-end.
- **Tailscale SSH "check" mode using Keycloak identity** — when an
  admin SSHs into a tagged host via tailnet, the "check" confirmation
  would reference their Keycloak identity, not raw Tailscale identity.
  Cleaner audit trail for SSH access.
- **Per-app Keycloak roles** could drive fine-grained Tailscale ACLs
  (e.g., "Keycloak role X → tailnet group Y → specific port access").
  Possible but requires careful design.

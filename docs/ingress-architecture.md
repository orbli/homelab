# Ingress architecture: Cloudflare Tunnel (edge) → Traefik (routing)

Since 2026-10-30 every request into the hk1 cluster takes the same path:

```
Internet
  │
  ▼
Cloudflare edge                 TLS termination, DNS, WAF. Universal SSL covers
  │                             *.orbb.li, which is why lab hosts are <app>-lab.
  ▼
cloudflared connectors          Run by the cloudflare-tunnel ingress controller
  │                             (ArgoCD app cloudflare-tunnel-ingress-controller).
  │                             One rule per hostname; ALL rules → traefik.ingress:80.
  │                             Host header preserved; X-Forwarded-Proto: https added.
  ▼
Traefik  (ingress ns)           ArgoCD app `traefik`. ClusterIP only, 2 replicas,
  │                             never the default IngressClass. Routes by Host to the
  │                             app behind each `ingressClassName: traefik` Ingress.
  │                             NO middleware attached today.
  │
  ├── grafana-lab.orbb.li   → observability/prometheus-grafana:80   (Grafana does its own OIDC)
  ├── keycloak-lab.orbb.li  → iam/keycloak-service:http
  ├── registry-lab.orbb.li  → authorship-registry/authorship-registry:80
  ├── remark42-lab.orbb.li  → default/remark42:80
  └── infra-lab.orbb.li     → infra-status/infra-status:80
```

## Why two tiers

The tunnel controller alone (2026-10-28 → 2026-10-30) gave gitops reachability:
one Ingress = tunnel rule + DNS. But cloudflared is a tube, not a reverse proxy.
There was no in-cluster hop that could apply policy (auth, headers, rate limits)
to a request, so anything of that kind would have had to be configured in
Cloudflare's dashboard, outside gitops.

Splitting the two jobs puts each where it belongs:

| Tier | Component | Owns | Declared by |
|---|---|---|---|
| Edge | cloudflare-tunnel ingress controller | DNS, TLS, getting bytes into the cluster | `cloudflare-tunnel` Ingress per hostname, all → `traefik:80` |
| Routing | Traefik | Host routing; the place policy would attach | `traefik` Ingress per app, in the app's folder |

The price is one extra Ingress object per app. The gain is a hop you fully
control that is independent of the edge: the same Traefik could sit behind a
Tailscale Ingress tomorrow and every rule would carry over.

## Where each object lives

```
kubernetes/argocd/traefik/traefik-app.yaml              Helm chart + values (wave 40)
kubernetes/argocd/cloudflared/…                          tunnel controller (wave 45), ingress ns (wave 30)
kubernetes/manifests/cloudflared-namespace/
  public-hostnames/<app>-lab.yaml                        class cloudflare-tunnel → traefik:80
kubernetes/manifests/<app>/ingress.yaml                  class traefik → app Service
```

The tunnel Ingresses are centralised because a Kubernetes Ingress backend must
be a Service in the Ingress's own namespace, and Traefik's Service is in
`ingress`. Most app kustomizations also set a `namespace:` transformer, which
would rewrite any `metadata.namespace: ingress` placed in an app folder.

Keycloak is the one app whose Ingress used to be created by its operator. The
Keycloak CR now has `ingress.enabled: false` and
`kubernetes/manifests/keycloak-operator/keycloak-ingress.yaml` is hand-written.

## Traefik values that matter

- `service.spec.type: ClusterIP`: servicelb is disabled in k3s and nothing but
  cloudflared (in-cluster) ever talks to Traefik.
- `ingressClass.isDefaultClass: false`: the `cloudflare-tunnel` Ingresses must
  never be picked up by Traefik, and the controller's own objects are explicit.
- `ports.web.forwardedHeaders.trustedIPs: [10.42.0.0/16]` (k3s pod CIDR):
  cloudflared sends `X-Forwarded-Proto: https` and `CF-Connecting-IP`. Trusting
  them is what makes apps see `https` and the real client IP. Without it,
  Keycloak (`proxy.headers: xforwarded`) and Grafana build `http://` redirects.
- `ports.websecure.expose.default: false`: TLS is Cloudflare's; nothing on 443.
- `accessLog.enabled: true`: one line per request at the single entry point.
- `ServerSideApply=true` on the ArgoCD app: Traefik's CRDs are too large for
  the client-side last-applied annotation.

## Migration notes (how the cutover was done safely)

The tunnel controller's reconcile loop
(`pkg/controller/ingress-controller.go`) returns early for any Ingress that is
not of its class. That means **changing an Ingress's class in place leaves the
controller's finalizer on it and does not re-sync the tunnel rules.** On
deletion, by contrast, it recomputes rules and DNS from the remaining
controlled Ingresses and then removes its finalizer.

So the cutover was delete-and-recreate, in one sync:

1. New `cloudflare-tunnel` Ingresses created in `ingress` (same hostnames,
   backend Traefik). ArgoCD applies creates before prunes.
2. Old per-app `cloudflare-tunnel` Ingresses pruned (they were replaced by
   differently named `<app>-traefik` objects of class `traefik`, so ArgoCD
   deletes rather than updates). Each deletion triggers a reconcile that still
   finds the hostname in step 1's objects, so the CNAME is kept and only the
   origin changes.
3. Keycloak's operator-created Ingress is deleted by the operator when
   `ingress.enabled` flips to false, going through the same clean path.

## Deliberately not done

- **No middleware.** Traefik is a routing hop only. A ForwardAuth middleware to
  OAuth2 Proxy + Keycloak is a one-Middleware-object addition later, attached
  per app with `traefik.ingress.kubernetes.io/router.middlewares`.
- **Grafana keeps its own OIDC.** Grafana's group→role mapping
  (`role_attribute_path`) is an OAuth-only feature. Its header-trust mode
  (`auth.proxy`) has no role header, so putting Grafana behind a gate and
  disabling its OIDC would flatten every user to the default role. Apps that
  authenticate well on their own should keep doing so; a future gate is for
  apps that cannot.
- **No Traefik dashboard on the tunnel.** If ever wanted, expose it on the
  `tailscale` IngressClass.

# ingress namespace: Cloudflare Tunnel as a Kubernetes Ingress

Tunnel `home-hk1-cluster-tunnel` (hk1) is driven by the
[cloudflare-tunnel-ingress-controller](https://github.com/STRRL/cloudflare-tunnel-ingress-controller)
(ArgoCD app `cloudflare-tunnel-ingress-controller`, Helm chart from helm.strrl.dev).

**One Ingress per app, in the app's own kustomize folder, is the whole definition
of a public hostname.** The controller writes the tunnel rule to Cloudflare and
creates the proxied CNAME `<app>-lab.orbb.li` (plus an ownership TXT
`_ctic_managed.<app>-lab`). DNSControl ignores those names (`dns/dnsconfig.js`).

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: myapp
spec:
  ingressClassName: cloudflare-tunnel
  rules:
  - host: myapp-lab.orbb.li
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: myapp
            port:
              number: 80
```

Annotations for HTTPS origins and the like:
`cloudflare-tunnel-ingress-controller.strrl.dev/backend-protocol: https`,
`.../no-tls-verify: "true"`, `.../http-host-header: ...`. Full list:
https://tunnel.strrl.dev/reference/ingress-annotations/

Deleting the Ingress removes the tunnel rule and the DNS records (finalizer).

## Secret (out-of-band, never in git)

The controller reads one Secret in this namespace:

```
kubectl -n ingress create secret generic cloudflare-tunnel-ingress-controller \
  --from-literal=api-token="$CF_TOKEN" \
  --from-literal=account-id="<cloudflare account id>" \
  --from-literal=tunnel-name=home-hk1-cluster-tunnel
```

`CF_TOKEN` is the shared Cloudflare API token (`dns/.env`): Zone → Zone Read,
Zone → DNS Edit on orbb.li, Account → Cloudflare Tunnel Edit. The account id
is the `AccountTag` inside the legacy `tunnel-credentials` Secret.

## How it behaves

- On every Ingress event it lists all `cloudflare-tunnel` Ingresses and
  replaces the tunnel's whole rule list (plus a `http_status:404` catch-all).
  It never writes until at least one such Ingress exists.
- Origins are built as `<svc>.<ns>.svc.home-hk1-cluster.orbb.li:<port>`
  (`clusterDomain` value); named Service ports are resolved.
- It runs its own cloudflared connector pods (`controlled-cloudflared-connector`,
  2 replicas) with a tunnel token it fetches via the API. The legacy
  `cloudflared-deployment` app (ConfigMap + Deployment) is retired once the
  controller is serving.

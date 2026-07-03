# infra-status — go-live runbook

Public, sanitized live-status page for the estate (à la wtako.net), served
outbound-only through the cloudflared tunnel. Backend already runs in-cluster
(ns `infra-status`); these steps expose it and ship the Hugo `/infra` page.

Everything below is the ONLY remaining work — build, manifests, egress, Hugo
page and the `--metrics` flag are already done and validated. Ordered so nothing
ships broken (cluster first, verify, then the public site).

## 1. DNS — create the hostname (needs Cloudflare access)

`infra-lab.orbb.li` does not resolve yet; `-lab` hosts use per-host CNAMEs, not a
wildcard. SSL is already covered by the `*.orbb.li` cert.

```bash
# Option A — cloudflared CLI (creates the proxied CNAME to the tunnel):
cloudflared tunnel route dns home-hk1-cluster-tunnel infra-lab.orbb.li
# Option B — Cloudflare dashboard: DNS → add CNAME
#   infra-lab → <tunnel-id>.cfargotunnel.com , Proxied (orange cloud)
```

## 2. Push homelab + register the new ArgoCD apps

There is no app-of-apps, so new Application CRs need a one-time apply.

```bash
cd homelab
git add kubernetes/argocd/infra-status kubernetes/argocd/networking-egress \
        kubernetes/manifests/infra-status kubernetes/manifests/networking-egress \
        kubernetes/manifests/cloudflared-deployment/configmap.yaml docs/infra-status-go-live.md
git commit -m "infra-status: public sanitized estate page served via tunnel"
git push
kubectl apply -f kubernetes/argocd/networking-egress/
kubectl apply -f kubernetes/argocd/infra-status/
```

ArgoCD then syncs: the 3 egress Services, the collector Deployment/Service/RBAC,
and the updated cloudflared ConfigMap (route added before the 404 fallback).

## 3. Roll cloudflared to pick up the route

cloudflared does NOT hot-reload a mounted-ConfigMap change.

```bash
kubectl -n ingress rollout restart deployment/cloudflared
kubectl -n ingress rollout status deployment/cloudflared
```

## 4. Verify the backend is public

```bash
curl -s https://infra-lab.orbb.li/infra.json | head -c 400   # expect the JSON
```

## 5. Ship the Hugo page

```bash
cd orbli
git add hugo.toml content/infra.md layouts/_default/infra.html
git commit -m "add /infra live status page"
git push          # GitHub Actions builds Hugo 0.143.1 -> Pages -> orbb.li/infra
```

## 6. (Optional) Activate the tok/s panel

`--metrics` is already staged in `~/glm52-serve.sh` on spark1 but only takes
effect on a GLM relaunch (~13-min reload; disruptive to live inference — pick
your moment). Until then the throughput tile shows "awaiting restart".

```bash
ssh home-hk2-spark1 'bash ~/glm52-serve.sh'   # when you can spare the reload
```

## Rollback

```bash
# page:      revert the orbli commit, re-push (Pages rebuilds)
# route:     git revert the configmap change, push, rollout restart cloudflared
# collector: kubectl delete -f kubernetes/argocd/infra-status/   (+ networking-egress)
# egress:    kubectl delete svc glm-spark2 nomad-hk2 -n networking
```

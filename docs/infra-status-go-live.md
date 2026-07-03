# infra-status — go-live runbook

Public, sanitized live-status page for the estate (à la wtako.net), served
outbound-only through the cloudflared tunnel. Backend already runs in-cluster
(ns `infra-status`); these steps expose it and ship the Hugo `/infra` page.

Everything below is the ONLY remaining work — build, manifests, egress, Hugo
page and the `--metrics` flag are already done and validated. Ordered so nothing
ships broken (cluster first, verify, then the public site).

## 1. Cloudflare Zero Trust — add the Public Hostname (needs Cloudflare access)

The tunnel is REMOTELY-managed: the local ConfigMap ingress is documentation
only (proven — a locally-added route did not appear in the runtime `version=N`
config). Routing AND DNS are driven from the dashboard:

> Zero Trust → Networks → Tunnels → `home-hk1-cluster-tunnel` → Public Hostnames
> → **Add a public hostname**
> - Subdomain: `infra-lab`  Domain: `orbb.li`
> - Service type: `HTTP`
> - URL: `infra-status.infra-status.svc.home-hk1-cluster.orbb.li:80`
> - Additional application settings → TLS → **No TLS Verify: ON**
> - **Save** (this also auto-creates the DNS CNAME — no separate DNS step).

SSL is already covered by the existing `*.orbb.li` cert.

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

## 3. (Not needed for routing) cloudflared rollout

Routing is driven by the dashboard (step 1), not the ConfigMap, so no rollout is
required to serve `infra-lab`. Roll only if you want the pods to re-read the
doc-only ConfigMap:

```bash
kubectl -n ingress rollout restart deployment/cloudflared
```

## 4. Verify the backend is public (after step 1's Save)

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

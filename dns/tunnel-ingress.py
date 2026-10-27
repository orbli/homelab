#!/usr/bin/env python3
"""Sync a remotely-managed cloudflared tunnel's ingress rules from the repo.

Source of truth: the `ingress:` block (and `tunnel:` name) inside
kubernetes/manifests/cloudflared-deployment/configmap.yaml. cloudflared ignores
that block at runtime because the tunnel is remotely managed; this script is
what makes it real, by writing the same rules to the Cloudflare API.

    tunnel-ingress.py plan   # show the diff between the file and the live tunnel
    tunnel-ingress.py apply  # plan, then PUT the file's rules if they differ

Needs CF_TOKEN with Account -> Cloudflare Tunnel -> Edit (plus Zone -> DNS ->
Read for the DNS cross-check). Account and tunnel id are derived from the
hostnames' zone and the tunnel name, so nothing else is configured
(CF_ACCOUNT_ID overrides the zone-derived account). Only stdlib + PyYAML.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

import yaml

API = "https://api.cloudflare.com/client/v4"
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIGMAP = os.path.join(HERE, "..", "kubernetes", "manifests", "cloudflared-deployment", "configmap.yaml")


def api(method, path, body=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {os.environ['CF_TOKEN']}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    if not d.get("success"):
        sys.exit(f"{method} {path} failed: {d.get('errors')}")
    return d["result"]


def desired():
    cm = yaml.safe_load(open(CONFIGMAP))
    cfg = yaml.safe_load(cm["data"]["config.yaml"])
    ingress = cfg["ingress"]
    if not ingress or "hostname" in ingress[-1] or "service" not in ingress[-1]:
        sys.exit("ingress must end with a catch-all rule (service only, no hostname)")
    names = [r["hostname"] for r in ingress[:-1]]
    if len(names) != len(set(names)):
        sys.exit(f"duplicate hostnames in ingress: {names}")
    return cfg["tunnel"], ingress


def normalise(rules):
    """Drop empty values the API adds (e.g. originRequest.httpHostHeader: "") so
    a file that omits them compares equal to the live config."""
    out = []
    for r in rules:
        r = {k: v for k, v in r.items() if v not in ("", None, {}, [])}
        if "originRequest" in r:
            r["originRequest"] = {k: v for k, v in r["originRequest"].items() if v not in ("", None, {}, [])}
            if not r["originRequest"]:
                del r["originRequest"]
        out.append(r)
    return out


def zone_for(hostname, zone_list):
    return next((z for z in zone_list if hostname == z["name"] or hostname.endswith("." + z["name"])), None)


def lookup(tunnel_name, hostnames, zone_list):
    """The tunnel lives in the account that owns the hostnames' zone(s). Zone
    objects carry their account id and the token can read zones, so no
    account-level 'list accounts' permission or extra secret is needed."""
    acc = os.environ.get("CF_ACCOUNT_ID")
    if not acc:
        ids = {zone_for(h, zone_list)["account"]["id"] for h in hostnames if zone_for(h, zone_list)}
        if len(ids) != 1:
            sys.exit(f"hostnames map to {len(ids)} accounts via their zones; set CF_ACCOUNT_ID")
        acc = ids.pop()
    tunnels = api("GET", f"/accounts/{acc}/cfd_tunnel?is_deleted=false&name={urllib.parse.quote(tunnel_name)}")
    if len(tunnels) != 1:
        sys.exit(f"found {len(tunnels)} tunnels named {tunnel_name!r}")
    return acc, tunnels[0]["id"]


def dns_check(hostnames, tunnel_id, zone_list):
    """Warn for every hostname without a proxied CNAME to this tunnel (that record
    is managed separately, in dns/dnsconfig.js)."""
    target = f"{tunnel_id}.cfargotunnel.com"
    for h in hostnames:
        zone = zone_for(h, zone_list)
        if zone is None:
            print(f"  warn: {h}: no zone for this hostname readable by the token")
            continue
        recs = api("GET", f"/zones/{zone['id']}/dns_records?name={h}")
        ok = any(r["type"] == "CNAME" and r["content"] == target and r.get("proxied") for r in recs)
        if not ok:
            print(f"  warn: {h}: no proxied CNAME -> {target} (add it to dns/dnsconfig.js)")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "plan"
    if mode not in ("plan", "apply"):
        sys.exit(__doc__)
    tunnel_name, ingress = desired()
    hostnames = [r["hostname"] for r in ingress if "hostname" in r]
    zone_list = api("GET", "/zones?per_page=50")
    acc, tid = lookup(tunnel_name, hostnames, zone_list)
    live = api("GET", f"/accounts/{acc}/cfd_tunnel/{tid}/configurations")
    live_cfg = live.get("config") or {}
    want, have = normalise(ingress), normalise(live_cfg.get("ingress", []))
    print(f"tunnel {tunnel_name} ({tid}), live config version {live.get('version')}")

    by_host = lambda rules: {r.get("hostname", "(catch-all)"): r for r in rules}
    w, h = by_host(want), by_host(have)
    changes = 0
    for name in list(w) + [n for n in h if n not in w]:
        if name not in h:
            print(f"  + {name} -> {w[name]['service']}"); changes += 1
        elif name not in w:
            print(f"  - {name} (was -> {h[name]['service']})"); changes += 1
        elif w[name] != h[name]:
            print(f"  ~ {name}: {json.dumps(h[name])} -> {json.dumps(w[name])}"); changes += 1
    if [r.get("hostname") for r in want] != [r.get("hostname") for r in have] and not changes:
        print("  ~ rule order differs"); changes += 1
    dns_check(hostnames, tid, zone_list)

    if not changes:
        print("no changes")
        return
    print(f"{changes} change(s)")
    if mode == "apply":
        new_cfg = dict(live_cfg)          # keep warp-routing and anything else untouched
        new_cfg["ingress"] = ingress
        res = api("PUT", f"/accounts/{acc}/cfd_tunnel/{tid}/configurations", {"config": new_cfg})
        print(f"applied: live config version {res.get('version')}")


if __name__ == "__main__":
    main()

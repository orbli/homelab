# Nomad jobs (oracle-apt1-vm1)

Job specs for the single-node Nomad agent on **oracle-apt1-vm1** (Oracle Cloud,
Japan, tailnet `tag:exit-node`). This directory is the source of truth; the
copies under `/home/ubuntu/` on the VM are deploy artifacts.

Unlike `kubernetes/` (ArgoCD) and `tailscale/` (GitHub Actions sync), there is
**no automated reconciliation** for these yet — deploys are manual:

```bash
scp nomad/simple-proxy.nomad ubuntu@oracle-apt1-vm1:~/
ssh ubuntu@oracle-apt1-vm1 \
  'NOMAD_ADDR=http://100.92.143.95:4646 nomad job run -var-file=$HOME/nomad-local.vars ~/simple-proxy.nomad'
```

**Secrets convention** (same as `TS_TAILNET` for the tailscale sync): the
tailnet MagicDNS suffix never appears in this repo. Jobs that need it declare
`variable "tailnet_domain"` and read it from `~/nomad-local.vars` on the VM
(untracked, mode 0600):

```hcl
# ~/nomad-local.vars on oracle-apt1-vm1
tailnet_domain = "tailXXXX.ts.net"
```

Gotchas:
- The Nomad API binds to the **tailscale IP**, not localhost — `NOMAD_ADDR` is
  required (it's in the VM's `.bashrc`, but non-interactive SSH doesn't source it).
- Container workloads there need explicit `dns_servers`/`dns_search_domains` to
  resolve tailnet MagicDNS names — Docker cannot use the host's systemd-resolved
  stub and falls back to Oracle's metadata DNS otherwise (see simple-proxy.nomad).

## Jobs

| Job | Purpose |
|---|---|
| `simple-proxy.nomad` | HTTP proxy on `:8888` (tailnet-wide reachable). Resolves both internet and tailnet names. |

Note: the `https-proxy-poc` container on the same VM is a manually-run docker
container, NOT Nomad-managed (and not tracked here).

# dns/ — orbb.li zone as code

orbb.li lives on **Cloudflare** (`nadia`/`pablo` nameservers). This folder tracks it with
**[DNSControl](https://dnscontrol.org)** (`~/.local/bin/dnscontrol`), same pattern as
`mcgt/dns` but with its own token: that one is scoped to mcgt.co.uk and belongs to a
different business, so it is never reused here.

## One-time setup

1. Cloudflare dashboard → My Profile → **API Tokens** → Create Token → *Custom token*:
   - Include zone: **orbb.li** only
   - Permissions: **Zone/Zone/Read**, **Zone/DNS/Edit**
2. `cp .env.example .env` and paste the token into `CF_TOKEN=` (`.env` is git-ignored;
   `creds.json` reads it via `$CF_TOKEN`, so nothing secret is committed).
3. `./dnsctl.sh import` → dumps the live zone to `imported.js`. Merge its records into
   `dnsconfig.js`, set `ZONE_IMPORTED = true`, then `./dnsctl.sh preview` must show **no
   changes** before anything else is edited.

## Day-to-day

```bash
./dnsctl.sh preview   # ALWAYS before push: records missing from dnsconfig.js get deleted
./dnsctl.sh push
```

## Gotchas

- Lab hosts are `<app>-lab.orbb.li` (dash) because the free-plan Universal SSL only covers
  one wildcard level. See `../CLAUDE.md`.
- The cloudflared tunnel is remotely managed: its Public Hostnames create CNAME records in
  the dashboard. After adding one there, run `preview` and copy the new record into
  `dnsconfig.js`, or the next push removes it.
- `DefaultTTL(1)` = Cloudflare "Auto"; without it every record shows a phantom TTL diff.

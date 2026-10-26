# dns/ — orbb.li zone as code

orbb.li lives on **Cloudflare** (`nadia`/`pablo` nameservers). This folder tracks it with
**[DNSControl](https://dnscontrol.org)** (`~/.local/bin/dnscontrol`), same pattern as
`mcgt/dns`. Since 2026-10-26 the same Cloudflare token covers both zones (owner's choice);
`dns/.env` here is a copy of `mcgt/dns/.env`.

## One-time setup

1. Cloudflare dashboard → My Profile → **API Tokens** → Create Token → *Custom token*:
   - Include zone: **orbb.li** only
   - Permissions: **Zone/Zone/Read**, **Zone/DNS/Edit**
2. `cp .env.example .env` and paste the token into `CF_TOKEN=` (`.env` is git-ignored;
   `creds.json` reads it via `$CF_TOKEN`, so nothing secret is committed).
3. The live zone was imported into `dnsconfig.js` on 2026-10-26 (`preview` showed 0
   corrections). `dnscontrol get-zones` printed nothing for this zone, so the import was
   done from the Cloudflare API record list by hand; if `./dnsctl.sh import` is ever needed
   again, expect to fall back to `GET /zones/<id>/dns_records`.

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

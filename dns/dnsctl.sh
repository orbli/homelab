#!/usr/bin/env bash
# orbb.li DNS as code (DNSControl + Cloudflare). Token comes from dns/.env (git-ignored).
#   ./dnsctl.sh import    dump the LIVE zone into imported.js (first step, once)
#   ./dnsctl.sh preview   diff dnsconfig.js against Cloudflare (always before push)
#   ./dnsctl.sh push      apply dnsconfig.js
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
: "${CF_TOKEN:?Set CF_TOKEN in dns/.env (see README.md)}"
case "${1:-}" in
  preview|push) exec dnscontrol "$1" ;;
  import)       exec dnscontrol get-zones --format=djs --out=imported.js cloudflare - orbb.li ;;
  *) echo "usage: $0 {import|preview|push}" >&2; exit 1 ;;
esac

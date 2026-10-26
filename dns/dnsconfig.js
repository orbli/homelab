// orbb.li DNS — DNSControl, provider Cloudflare. Run via ./dnsctl.sh {import|preview|push}.
//
// SAFETY: DNSControl treats this file as the FULL zone. Any live record missing here is
// DELETED on push. The zone has not been imported yet, so this file deliberately refuses to
// run until someone runs `./dnsctl.sh import`, merges imported.js into the D() block below,
// and flips ZONE_IMPORTED to true.
var ZONE_IMPORTED = false;
if (!ZONE_IMPORTED) {
  throw new Error("orbb.li not imported yet: run ./dnsctl.sh import, merge imported.js here, set ZONE_IMPORTED = true");
}

var REG_NONE = NewRegistrar("none");
var DSP_CLOUDFLARE = NewDnsProvider("cloudflare");

D("orbb.li", REG_NONE, DnsProvider(DSP_CLOUDFLARE),
  DefaultTTL(1), // 1 = Cloudflare "Auto" TTL

  // ── existing records: paste from imported.js ─────────────────────────────

  // ── ada.orbb.li → authorship registry (AWS CloudFront in front of a Lambda URL) ──
  // DNS-only (grey cloud): CloudFront terminates TLS with its own ACM certificate, and
  // Cloudflare's wildcard cert covers ada.orbb.li anyway if it is ever proxied.
  // Values come from `aws acm describe-certificate` (validation) and the CDK output CdnDomain.
  // CNAME("_<token>.ada", "_<token>.acm-validations.aws."),   // ACM DNS validation
  // CNAME("ada", "<distribution-id>.cloudfront.net."),
);

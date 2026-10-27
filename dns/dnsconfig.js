// orbb.li DNS — DNSControl, provider Cloudflare. Run via ./dnsctl.sh {preview|push}.
//
// This file is the FULL zone (imported from the live zone via the Cloudflare API on
// 2026-10-26; `dnscontrol get-zones` printed nothing, so the import was done by hand).
// Any live record missing here is DELETED on push. ALWAYS `preview` before `push`.
//
// Convention: lab services are `<app>-lab.orbb.li` (dash, see ../CLAUDE.md). The cloudflared
// tunnel is remotely managed; a new Public Hostname in Zero Trust creates a CNAME in the
// dashboard, which must then be copied here or the next push removes it.

var REG_NONE = NewRegistrar("none");
var DSP_CLOUDFLARE = NewDnsProvider("cloudflare");

var TUNNEL_HK1 = "ad4bf3f5-d7ef-472e-ac96-334611c0005b.cfargotunnel.com.";

D("orbb.li", REG_NONE, DnsProvider(DSP_CLOUDFLARE),
  DefaultTTL(1), // 1 = Cloudflare "Auto" TTL

  // ── apex orbb.li → GitHub Pages (orbli Hugo site) ────────────────────────
  A("@", "185.199.108.153"),
  A("@", "185.199.109.153"),
  A("@", "185.199.110.153"),
  A("@", "185.199.111.153"),
  TXT("_github-pages-challenge-orbli", "d476e89c3d4098400d991b4a35626b"),

  // ── Google Workspace mail + verifications ────────────────────────────────
  MX("@", 1, "aspmx.l.google.com.", TTL(3600)),
  MX("@", 5, "alt1.aspmx.l.google.com.", TTL(3600)),
  MX("@", 5, "alt2.aspmx.l.google.com.", TTL(3600)),
  MX("@", 10, "alt3.aspmx.l.google.com.", TTL(3600)),
  MX("@", 10, "alt4.aspmx.l.google.com.", TTL(3600)),
  TXT("@", "google-site-verification=UBw1WaLgGr83WhlLSLpoRLpihbMd7fikAwLb1nMSg8o"),
  TXT("@", "plaid-inc-domain-verification-w6pgfd=2gyZmJc4aKqP6UN2T1nKchWxW"),

  // ── granbluev2 leaderboard (Firebase Hosting; must stay DNS-only) ────────
  A("gbf", "151.101.1.195"),
  A("gbf", "151.101.65.195"),
  A("granblue", "151.101.1.195"),
  A("granblue", "151.101.65.195"),

  // ── aws.orbb.li delegated to a Route 53 hosted zone ──────────────────────
  NS("aws", "ns-1399.awsdns-46.org."),
  NS("aws", "ns-1988.awsdns-56.co.uk."),
  NS("aws", "ns-358.awsdns-44.com."),
  NS("aws", "ns-784.awsdns-34.net."),

  // ── homelab via cloudflared tunnel hk1 (proxied) ─────────────────────────
  // <app>-lab CNAMEs are NOT declared here: the cloudflare-tunnel ingress
  // controller in the cluster creates them (plus a _ctic_managed.<host> TXT
  // ownership record) from each app's Ingress object. These IGNOREs make
  // DNSControl leave those records alone; everything else in the zone stays
  // managed here. To publish a new lab app, add an Ingress with
  // ingressClassName cloudflare-tunnel in its kustomize folder, nothing here.
  IGNORE("*-lab", "CNAME"),
  IGNORE("_ctic_managed.*-lab", "TXT"),

  // ── Tailscale: domain ownership verification for the orbb.li tailnet ─────
  TXT("_tailscale-challenge", "v2=QP824tjGBRKqHTaCp1dxRNatBJUuncC1AuZJCAj9FTBa"),

  // ── ada.orbb.li → authorship registry (AWS CloudFront in front of a Lambda URL) ──
  // DNS-only: CloudFront terminates TLS with its own ACM certificate.
  // ACM certificate: arn:aws:acm:us-east-1:599415174730:certificate/de785670-47b9-4674-933a-b2808eabf608
  CNAME("_b70adbca68243a60ff7b8d5bdd4ab2b5.ada", "_4fdcdab4582102e1fa0879b03b0b1e0f.wzccmgtwzk.acm-validations.aws."), // ACM DNS validation, keep as long as the cert exists (auto-renewal)
  // CNAME("ada", "<CdnDomain output>."),  // added after the first cdk deploy with -c domainName=ada.orbb.li
);

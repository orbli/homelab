# Tailnet MagicDNS suffix (tailXXXX.ts.net). Deliberately NOT defaulted here —
# like TS_TAILNET in the tailscale sync workflow, the tailnet identifier stays
# out of the repo. Pass at deploy time:
#   nomad job run -var-file=$HOME/nomad-local.vars simple-proxy.nomad
variable "tailnet_domain" {
  type        = string
  description = "Tailnet MagicDNS search suffix for container DNS"
}

job "simple-proxy" {
  region      = "jp"
  datacenters = ["o-apt1"]
  type        = "service"

  group "proxy-group" {
    count = 1

    network {
      port "proxy" {
        static = 8888
        to     = 8888
      }
    }

    task "simple-proxy" {
      driver = "docker"

      config {
        image = "xieyanbo/simple-proxy"
        ports = ["proxy"]

        # Force IPv4 networking
        network_mode = "bridge"

        # Resolve tailnet MagicDNS names (e.g. home-hk2-spark1) from inside
        # the container. Docker can't use the host's 127.0.0.53 resolved stub,
        # so without this it falls back to the cloud metadata resolver, which
        # knows nothing about *.ts.net. 100.100.100.100 = tailscaled's DNS
        # (also forwards non-tailnet queries upstream); 1.1.1.1 = fallback so
        # internet proxying survives a tailscaled restart. The search domain
        # is required: quad-100 answers FQDNs only, not bare hostnames
        # (verified empirically 2026-07-02).
        dns_servers        = ["100.100.100.100", "1.1.1.1"]
        dns_search_domains = [var.tailnet_domain]
      }

      resources {
        cpu    = 100
        memory = 64
      }

      # Auto-restart on failure (equivalent to --restart=always)
      restart {
        attempts = 10
        interval = "5m"
        delay    = "15s"
        mode     = "delay"
      }
    }
  }
}

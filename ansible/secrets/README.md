# Secrets Directory

This directory contains sensitive files that should not be committed to version control.

## Cloudflare Tunnel (ingress controller)

No credentials file lives here any more. The tunnel is driven by the
cloudflare-tunnel ingress controller, which needs one Kubernetes Secret created
by hand (API token, account id, tunnel name); the exact command is in
`kubernetes/manifests/cloudflared-namespace/README.md`. The controller fetches
the tunnel's connector token itself via the API.

## Security Notes

- All files in this directory (except README.md and .gitignore) are ignored by git
- Never commit credential files to version control
- Ensure proper file permissions (600) for credential files 
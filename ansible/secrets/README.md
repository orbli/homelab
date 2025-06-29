# Secrets Directory

This directory contains sensitive files that should not be committed to version control.

## Cloudflared Tunnel Credentials

To set up cloudflared tunnel credentials:

1. **Create a Cloudflare tunnel** (if you haven't already):
   ```bash
   cloudflared tunnel create <tunnel-name>
   ```

2. **Copy the credentials file** to this directory:
   ```bash
   cp ~/.cloudflared/<tunnel-id>.json ./tunnel-credentials.json
   ```

3. **Update the tunnel name** in `02-cloudflared.yaml` to match your actual tunnel name (replace `example-tunnel`)

4. **Run the secrets playbook first**:
   ```bash
   ansible-playbook 02-install/02-cloudflared-secrets.yaml
   ```

5. **Then run the main cloudflared playbook**:
   ```bash
   ansible-playbook 02-install/02-cloudflared.yaml
   ```

## Security Notes

- All files in this directory (except README.md and .gitignore) are ignored by git
- Never commit credential files to version control
- Ensure proper file permissions (600) for credential files 
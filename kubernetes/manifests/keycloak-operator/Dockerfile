# Custom Keycloak image with our login themes baked in.
#
# Built and pushed by .github/workflows/build-keycloak.yml on any change
# under themes/ or this Dockerfile. The resulting image is consumed by
# keycloak-cr.yaml (spec.image) so the operator rolls the pod onto a new
# digest whenever the theme content changes.
#
# Why bake instead of mount? Two reasons:
#   1. Mounting 36 theme files via ConfigMap+subPath is verbose and brittle
#      with binary assets (PNGs need binaryData round-trips).
#   2. Cloudflare CDN caches Keycloak's theme resources for 30 days based
#      on a URL whose hash component is per-build, not per-content. Baking
#      the theme into an image changes the Keycloak build hash at startup
#      time for any non-trivial change, busting both the CDN cache and any
#      browser-side cache automatically.
FROM quay.io/keycloak/keycloak:26.3.3

# COPY runs as root in the build stage; the upstream image sets USER 1000
# at the end, so the running pod has read-only access regardless of file
# ownership inside /opt/keycloak/themes.
COPY themes/ /opt/keycloak/themes/

# Deployment work

Before deploying from this checkout, read `.local/deployments/README.md` if it
exists. It contains installation-specific commands, release scope, persistence,
rollback, and verification notes. This local directory is intentionally ignored;
do not copy its hosts, credentials, state, or installation configuration into
reusable template files.

Do not infer the production Compose project, overrides, current image, or runtime
backend from template defaults. Inspect the deployed configuration first, without
printing secrets. Follow package-level instructions for the code being changed.

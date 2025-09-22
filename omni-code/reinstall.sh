pipx uninstall omni-code
export FURY_AUTH=REDACTED_FURY_TOKEN
pipx install . --pip-args="--extra-index-url https://${FURY_AUTH}:@pypi.fury.io/ericmichael/"

# GUI Protocol Governance

The OmniAgents repository is the canonical source for the versioned GUI JSON-RPC protocol used by maintained clients, including Omni Desktop. The OpenRPC document, JSON Schema, generated models, inventory, and manifest under `protocol/openrpc/` are generated from `omniagents.rpc.protocol_catalog.OPERATIONS` and the protocol generator.

## Version 1 Compatibility

Version 1 is additive-only. Once an operation is stable, changes must preserve existing clients and servers:

- Do not remove or rename a stable operation, change its direction or kind, alter correlation metadata, or remove a structured error.
- Do not add a required client-to-server parameter or make an optional parameter required.
- Do not remove a field, narrow a type or enum, or make a client-to-server object reject properties it previously accepted.
- New operations and optional request fields may be added. Request input types and unions may widen because servers continue accepting every old payload. Existing result and notification output types and unions must not change because old receivers compile against them. Enum values may widen only under the v1 rule that receivers tolerate unknown enum values.
- In JSON Schema, an omitted `type`, `anyOf`, or `oneOf` is unconstrained. Adding one of those constraints to an existing request input is narrowing and therefore breaking; removing a request constraint is additive. Adding, removing, or changing an existing output constraint is breaking.
- Do not change the documented meaning of an existing value. Passing the structural compatibility check does not make a semantic break acceptable.

Breaking changes require a new protocol major version. The compatibility checker enforces the structural subset of these rules against the immutable v1 baseline.

## Experimental Operations

New operations normally enter the catalog with `x-omni-stability: experimental`. Experimental operations are not covered by the stable v1 compatibility promise and must not be assumed to exist merely because a peer speaks GUI protocol v1. Preserve existing experimental wire names; do not rename them into an `experimental.*` namespace during v1.

Until explicit capability negotiation is available, experimental operations must not be required for stable client behavior. Callers must retain a stable fallback and tolerate `-32601` or an unknown notification. After capability negotiation lands, send or invoke an experimental operation only after the peer advertises its named capability.

Promotion to `stable` requires:

1. Implementations and focused tests in OmniAgents and Omni Desktop.
2. A documented capability name, or a tracked capability-negotiation dependency, plus safe fallback behavior during the adoption window.
3. Agreement that the operation name, direction, kind, correlation, payload, and semantics are ready for the additive-only guarantee.
4. Approval from an OmniAgents maintainer and an active Omni Desktop reviewer.

Keep the capability guard while supported Desktop releases without the operation remain active. Promotion changes the compatibility commitment; it does not prove that every deployed peer has upgraded.

## Deprecation and Removal

Deprecation is a compatibility period, not permission to remove an operation from v1.

1. Mark the operation with `deprecated: true` in generated OpenRPC metadata and add `x-omni-deprecation` metadata identifying the deprecation version, replacement, and earliest removal major.
2. Document the replacement and keep both paths functional for at least one OmniAgents minor release and one Omni Desktop release train after the deprecation ships.
3. Preserve the deprecated operation in all remaining v1 releases. Remove it only in the next protocol major version, after both adoption windows have elapsed.

The same timeline applies to deprecated fields and enum values. Capability negotiation may steer upgraded peers to the replacement but cannot shorten the compatibility window.

## Baselines and Major Versions

`protocol/openrpc/baseline/1.0.0/` is immutable release evidence. Never regenerate, reformat, or repair it in place when the generator or toolchain changes. A digest failure must be investigated against the originally released artifacts rather than fixed by changing the baseline.

To introduce a new major version:

1. Obtain design approval from the OmniAgents maintainer and active Omni Desktop reviewer before landing a breaking schema change.
2. Add major-version-specific catalog, generated artifact, manifest, compatibility, and consumer paths so v1 remains reproducible during migration.
3. Generate and review the new candidate, update both maintained OmniAgents clients, and land the corresponding Omni Desktop capability and migration support.
4. At release, copy the approved canonical OpenRPC document, schema, and manifest into a new versioned baseline directory such as `protocol/openrpc/baseline/2.0.0/`. Verify its manifest digest against the released files.
5. Point the new major's CI compatibility check at that snapshot. Treat the snapshot as immutable after release.

Do not create or move a baseline merely to make compatibility CI pass. Baselines represent released contracts, not development candidates.

## Regeneration and Verification

From the OmniAgents repository root, install the pinned Python and web UI tooling:

```bash
python -m pip install -e '.[dev,server]'
npm ci --prefix omniagents/backends/web/ui
```

Regenerate all canonical and generated artifacts after editing the protocol catalog or generator:

```bash
python -m scripts.protocol.generate_gui_protocol
```

Run the same checks enforced by the repository test workflow:

```bash
python -m scripts.protocol.generate_gui_protocol --check
python -m scripts.protocol.check_gui_protocol_compatibility --baseline-root protocol/openrpc/baseline/1.0.0 --candidate-root protocol/openrpc
python -m pytest tests/unit/rpc/test_gui_protocol_schema.py tests/unit/rpc/test_gui_protocol_compatibility.py
```

Commit source changes and regenerated outputs together. Do not hand-edit generated files.

## Omni Desktop Sync and Provenance

Omni Desktop consumes the checked-in TypeScript artifact produced by OmniAgents. Sync only from a clean, reviewed OmniAgents commit for which generation and compatibility CI passed; do not copy a local uncommitted artifact.

From the Omni Desktop repository, with the canonical OmniAgents checkout available at `../omniagents`, run:

```bash
npm run protocol:sync -- \
  --source-ts ../omniagents/omniagents/backends/web/ui/src/protocol/generated/gui-v1.ts \
  --source-manifest ../omniagents/protocol/openrpc/manifest.json \
  --source-commit "$(git -C ../omniagents rev-parse HEAD)"
npm run protocol:check
```

The sync updates `src/generated/omniagents-gui-v1/gui-v1.ts` and `src/generated/omniagents-gui-v1/provenance.json`. The provenance record must retain the OmniAgents repository and commit, source artifact and manifest paths, protocol and generator versions, toolchain versions, canonical manifest digest, and generated TypeScript digest.

Review the generated diff and provenance together. The Omni Desktop pull request must link the source OmniAgents change, identify the exact source commit, pass `npm run protocol:check`, and include any client implementation or capability changes required by the protocol addition.

## Reviewer Ownership

Every protocol change requires approval from both:

- An OmniAgents maintainer, responsible for the canonical catalog, runtime behavior, generated artifacts, compatibility classification, and baseline handling.
- An active Omni Desktop reviewer, responsible for Desktop capability behavior, sync provenance, compatibility across the supported Desktop train, and migration readiness.

Both reviewers are required for experimental promotion, deprecation, baseline creation, and any major-version proposal. Changes must not be self-approved on behalf of the other repository.

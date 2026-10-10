# ADR-0003: Non-destructive draft, explicit deploy, simulation isolation

**Status:** Accepted, v1.

**Decision:** Engineering edits (draft) must not mutate running gateway. A validated immutable revision is compiled/staged, then explicitly deployed with audit, compare-and-swap revision and rollback. Simulation values are opt-in runtime-side samples with origin/TTL; block controls by default.

**Why:** preserve industrial continuity and traceability, permit offline work when no IED/meter is present, avoid simulator-driven physical actuation.

**Consequence:** UI preview is not a protocol integration test; command permissions and test-tag/quality propagation need protocol-specific integration evidence before release.

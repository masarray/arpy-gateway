# ADR-0002: Stable per-instance signal identity and separate mapping edges

**Status:** Accepted, v1.

**Decision:** Protocol Instance, Protocol Signal and Mapping are different immutable contract entities. One signal can fan out to many protocol destinations; adapter-specific addresses are not IDs. Default fan-in collision, data-path cycle and unsolicited physical control are invalid.

**Why:** arbitrary sorting/filtering and many-to-many gateway topologies must never change what a signal means. Device protocols have incompatible field models and address scopes. GUI per-protocol editors can be independently developed.

**Consequence:** flat tables containing `source_*` and `target_*` are projections only. P0 v0.1 GUI prototype store must be migrated/replaced; cross-protocol references never inferred by label or row index.

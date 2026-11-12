# Archive

Historical material: research, decisions and measurements that shaped hark but no longer describe it. Kept for
the reasoning; for how hark works now, start at the [README](../../README.md) and
[docs/architecture.md](../architecture.md).

| Path | What it was |
|---|---|
| [00-context.md](00-context.md) | the project's first brief (a Rust client, NeMo servers, edit-list cleanup: none of it built that way) |
| [research/](research/) | context capture, context ranking (the recipe `context/rank.py` implements), cleanup model research, competitor studies |
| [decisions/ADR-002-superwhisper-ideas.md](decisions/ADR-002-superwhisper-ideas.md) | which Superwhisper-for-Linux ideas were adopted (several were later removed) |
| [protocol/](protocol/) | the three protocol proposals behind [ADR-001](../protocol/ADR-001-protocol.md); hark.v1 is proposal-a amended ([CHANGES.md](../protocol/CHANGES.md)) |
| [bench/2026-11-11-cleanup.md](bench/2026-11-11-cleanup.md) | Qwen3 edit-list cleanup; engine removed |
| [bench/2026-11-11-cleanup-s1.md](bench/2026-11-11-cleanup-s1.md) | S1-mini aligned into edits; engine removed, S1-mini kept as the model |

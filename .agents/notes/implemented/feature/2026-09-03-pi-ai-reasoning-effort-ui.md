# Agent Note: Thinking-intensity control on pi-ai model rows

Status: implemented

English | [中文](2026-09-03-pi-ai-reasoning-effort-ui.zh.md)

## Problem

[[2026-08-08-pi-ai-per-model-reasoning-declarations]] shipped per-model `reasoningEfforts` "with zero UI change": the composer's effort pane lit up from a declared profile, but the declaration itself could only be written in `settings.yaml`. A user with a custom (hand-declared) model had no way to state that it thinks, or at what intensity, from the settings page — the reported request.

## Decision

Each model row in `ModelListEditor` — the editor every pi-ai route's models use — gains a thinking-intensity checkbox group inside the row's existing disclosure, beside the capacity fields. It offers the common four levels (minimal / low / medium / high), each labeled in both languages, with a hint stating that no checked level means no thinking is sent.

The draft-state contract keeps three meanings distinct, exactly as the profile field does:

- **Absent** — the row carries no `reasoningEfforts` until the user touches the group, so a catalog-inherited model's declared behavior survives an unrelated edit untouched.
- **`{ off: null, <level>: <level>, … }`** — checking the first level writes the dict with `off` as the valueless "supported, send nothing" entry and each checked level mapping to its own name (the identity spelling, matching how the levels are offered). Checking more appends.
- **`false`** — unchecking the last level writes the "this model does not think" spelling.

The level vocabulary is a local constant that mirrors `THINKING_LEVELS` in `llm-pi-ai`'s catalog (the schema's source of truth); the client package deliberately does not depend on the adapter package. `xhigh` and `max` are not offered — a Known-Limitations bullet in the package README records that toggling any checkbox rewrites the dict from the offered four, dropping a hand-declared `xhigh`/`max` entry.

The shared `validateDeepSeekModels` gate (which already serves both family editors) gains a `modelReasoningEffortsInvalid` failure mirroring the adapter's resolution rules: `false` or a dict; keys within the level vocabulary; every declared level a non-empty wire spelling, with `null` only for `off`; at least one level beyond `off`. Strictness here matches the adapter rather than the UI's four — a hand-edited YAML value the adapter accepts must not be unsaveable by the page.

## Alternatives considered

**A provider-scoped thinking-intensity control.** Rejected for the reason the previous headers documented when the control was absent: effort is a per-model capability, models under one provider disagree about it, and a provider-scoped value would be wrong for some of them. The create card's and editor's header comments now point at the per-row control instead of stating an absence.

**Offer the full seven-level vocabulary.** Rejected: `xhigh`/`max` are rare gateway dialects, the row stays compact at four, and the profile field keeps accepting them — the README records the trade.

**Reuse the DeepSeek editor's capacity `patch` for the toggles.** `patch`'s value type was widened to accept the level dict and `false` (its drop-`undefined`/drop-`''` filter already treats both as keep-values), but the toggles get their own `toggleLevel` — the dict-rebuild and the last-uncheck-writes-`false` rule are level logic the capacity path should not know about.

## Consequences

- The gap [[2026-08-08-pi-ai-per-model-reasoning-declarations]] deferred is closed: declaring a custom model's thinking intensity is now a settings-page interaction, and the composer effort pane follows through the same seam that note established (pinned by the existing `declared-reasoning` web scenario).
- Form tests pin the three draft states and the validator's accepted/refused shapes; resolver-level vocabulary changes remain owned by the adapter's own tests via the drift gates.

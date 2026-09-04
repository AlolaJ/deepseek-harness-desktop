# Agent Note: Default `supportsFinishReason` off for unknown openai-completions models

Status: implemented

English | [中文](2026-09-03-pi-ai-compat-stream-finish-default.zh.md)

## Problem

A user's OpenAI-compatible gateway produced `Stream ended without finish_reason` on every response with a custom model; the same model worked in other agents. The retry policy retried the five permitted attempts and failed the turn — behavior that only makes sense for a transient fault, yet the failure was deterministic.

The mechanism: pi-ai's `openai-completions` stream tracks `hasFinishReason` across chunks and throws that error when the stream ends cleanly — content delivered, `[DONE]` received — without any chunk carrying a truthy `finish_reason`. Gateways that forward an upstream without composing one (or that never emit the field at all) trip it on every response. The harness classifies the wording as `TRANSPORT` (see [[2026-07-22-pi-ai-transport-truncation-classification]], which admitted this wording into the truncation family), so a deterministic gateway shape was being retried as if it were a mid-stream drop.

`compat.supportsFinishReason: false` was already the escape hatch — it tells pi-ai to infer the terminal reason instead of requiring the field — but it was settings-only, and nothing about an unknown gateway's URL hints that the switch is needed.

## Decision

`resolveModelCompat` now defaults `supportsFinishReason: false` for an `openai-completions` model that is **catalog-unknown** (`base === undefined`) or **protocol-repointed** (`base.api !== api`) when neither the route nor the model entry configures the field. A configured value — route or model, either polarity — always wins, and an installed-catalog model keeps its declared/detected behavior untouched.

The insertion sits before the "no configured compat → empty" early return, so a model with no other compat still materializes `{ compat: { supportsFinishReason: false } }`. The leniency mirrors the official DeepSeek adapter's own behavior across the seam, where a missing terminal reason maps to `stop`.

## Alternatives considered

**Stop classifying the wording as `TRANSPORT`.** Rejected: a genuine mid-response truncation also ends the stream without a `finish_reason`, and that case must stay retryable. The classifier's match is correct for what it can observe; what was wrong was feeding it a deterministic shape.

**Default `false` for every `openai-completions` model, catalog-known included.** Rejected: named vendors' entries carry pi-ai's declared/detected compat, and overriding declared behavior silently widens the adapter's tolerance beyond what the catalog authorizes. The two exclusion conditions (catalog-unknown, repointed) are exactly the cases where nobody has stated the stream shape.

**Patch pi-ai upstream.** Not available at the pinned version; the resolution-time default achieves the same effect inside the harness and keeps the escape hatch (`true`) for a deployment that wants strictness back.

## Consequences

- A deterministic no-`finish_reason` gateway streams to completion on the first attempt; the five-retry failure disappears for the reported case.
- A genuine mid-response drop still raises an SDK error whose wording reaches `classifyPiAiError`'s `TRANSPORT` match, so the retry path is reserved for actual truncation (and for a route that opted into strictness with `supportsFinishReason: true`).
- Resolver tests for hand-declared `openai-completions` models now carry the default field; wire tests pin both polarities (lenient default completes; configured strictness still fails the turn as an error finish).

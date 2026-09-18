/** Per-request thinking levels offered on the pi-ai catalog editor's model rows. */

import type { ReactNode } from 'react'
import type { DeepSeekModelDraft } from './DeepSeekModelsEditor.tsx'
import type { ModelsKey } from './locales.ts'
import styles from './ModelsSection.module.css'

/**
 * The per-request "thinking intensity" levels this surface offers, and the
 * wire spelling dispatch sends for each (they are the same name).
 *
 * Mirrors `THINKING_LEVELS` in packages/llm/llm-pi-ai/src/catalog.ts, which
 * is the schema's source of truth; the settings surface deliberately does not
 * depend on the adapter package, so the couple of levels it offers are spelled
 * here. `off` is never offered as a checkbox: every declared dict carries it as
 * the valueless "support thinking, send nothing" entry (`{ off: null, … }`),
 * so unchecking every level writes `reasoningEfforts: false` — "this model
 * does not think" — and checking any level rewrites the dict including `off`.
 */
const REASONING_LEVELS = [
  { level: 'minimal', labelKey: 'modelReasoningLevelMinimal' as const },
  { level: 'low', labelKey: 'modelReasoningLevelLow' as const },
  { level: 'medium', labelKey: 'modelReasoningLevelMedium' as const },
  { level: 'high', labelKey: 'modelReasoningLevelHigh' as const },
]

/** One offered thinking level, spelled as dispatch sends it. */
type ReasoningLevel = (typeof REASONING_LEVELS)[number]['level']

/** The value a row's level toggles store: off, or the declared map. */
export type ReasoningEffortsDraft = false | Record<string, string | null>

/** The levels a row currently declares as on; an absent or `false` value is none. */
function declaredLevels(model: DeepSeekModelDraft): Set<ReasoningLevel> {
  const efforts = model['reasoningEfforts'] as ReasoningEffortsDraft | undefined
  if (efforts === false || typeof efforts !== 'object' || efforts === null) return new Set()
  return new Set(
    REASONING_LEVELS.filter(({ level }) => typeof efforts[level] === 'string').map(({ level }) => level),
  )
}

/**
 * Spell a level selection as the adapter's `reasoningEfforts` value.
 * @param levels - the levels the row should declare as on.
 * @returns `false` for none (the model does not think), else the declared dict
 * with `off` carried as the always-null "supported, send nothing" entry.
 */
export function reasoningEffortsFor(levels: ReadonlySet<ReasoningLevel>): ReasoningEffortsDraft {
  if (levels.size === 0) return false
  return Object.fromEntries([
    ['off', null] as const,
    ...REASONING_LEVELS.filter(({ level }) => levels.has(level)).map(({ level }) => [level, level] as const),
  ])
}

/** Props of {@link ModelReasoningLevels}. */
interface ModelReasoningLevelsProps {
  /** Effective model row, including fields outside the curated editor. */
  model: DeepSeekModelDraft
  /** One-based row position for the accessible group label. */
  position: number
  /** Prevent changes while read-only or saving. */
  disabled: boolean
  /** Section copy. */
  t: (key: ModelsKey) => string
  /** Replace this row, preserving unrelated configuration. */
  onChange: (model: DeepSeekModelDraft) => void
}

/**
 * Edit the thinking levels one model is offered. Before the user interacts a
 * row carries no `reasoningEfforts` at all — catalog-inherited models keep
 * their declared behavior untouched. Checking the first level writes
 * `{ off: null, <level>: <level> }`; checking more appends; unchecking the last
 * level writes `false` (the model does not think); `off` survives as the
 * always-null "supported, send nothing" entry until the row is cleared.
 * @param props - model declaration and row replacement action.
 * @returns the labeled level checkboxes with their inheritance hint.
 */
export function ModelReasoningLevels({ model, position, disabled, t, onChange }: ModelReasoningLevelsProps): ReactNode {
  const declared = declaredLevels(model)
  return (
    <fieldset className={styles['modelLevels']} aria-label={`${t('modelReasoningEfforts')} ${String(position)}`}>
      <legend className={styles['modelFieldLabel']}>{t('modelReasoningEfforts')}</legend>
      <div className={styles['modelLevelRow']}>
        {REASONING_LEVELS.map(({ level, labelKey }) => (
          <label key={level} className={styles['modelLevel']}>
            <input
              type="checkbox"
              checked={declared.has(level)}
              aria-label={`${t('modelReasoningEfforts')} ${level} ${String(position)}`}
              disabled={disabled}
              onChange={(event) => {
                const next = new Set(declared)
                if (event.target.checked) next.add(level)
                else next.delete(level)
                onChange({ ...model, reasoningEfforts: reasoningEffortsFor(next) })
              }}
            />
            <span>{t(labelKey)}</span>
          </label>
        ))}
      </div>
      <span className={styles['modelLevelHint']}>{t('modelReasoningEffortsHint')}</span>
    </fieldset>
  )
}

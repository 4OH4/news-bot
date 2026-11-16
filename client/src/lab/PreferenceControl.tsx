export type PreferenceState = "hide" | "fewer" | "show" | "more";

export const STATES: { value: PreferenceState; icon: string; word: string }[] = [
  { value: "hide", icon: "⊘", word: "Hide" },
  { value: "fewer", icon: "−", word: "Fewer" },
  { value: "show", icon: "✓", word: "Show" },
  { value: "more", icon: "+", word: "More" },
];

/** The icon for a state, for rows that show only the current choice. */
export function iconFor(v: PreferenceState): string {
  return STATES.find((s) => s.value === v)?.icon ?? "";
}

/**
 * The four-way Hide / Fewer / Show / More control: a radio group where the arrow keys move
 * the choice, with 44px touch targets. The accessible name of each radio is its word, so the
 * words can shrink to icons in a narrow layout without losing it.
 */
export function PreferenceControl({
  value,
  onChange,
  label,
}: {
  value: PreferenceState;
  onChange: (v: PreferenceState) => void;
  label: string;
}) {
  const onKey = (e: KeyboardEvent) => {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (step === 0) return;
    e.preventDefault();
    const i = STATES.findIndex((s) => s.value === value);
    const j = Math.min(STATES.length - 1, Math.max(0, i + step));
    const next = STATES[j];
    if (!next || j === i) return;
    onChange(next.value);
    const buttons = (e.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('[role="radio"]');
    buttons[j]?.focus();
  };
  return (
    <div class="pref-control" role="radiogroup" aria-label={label} onKeyDown={onKey}>
      {STATES.map((s) => (
        <button
          key={s.value}
          type="button"
          role="radio"
          aria-checked={s.value === value}
          aria-label={s.word}
          tabIndex={s.value === value ? 0 : -1}
          class={`pref-opt pref-${s.value}`}
          onClick={() => onChange(s.value)}
        >
          <span aria-hidden="true" class="pref-icon">
            {s.icon}
          </span>
          <span aria-hidden="true" class="pref-word">
            {s.word}
          </span>
        </button>
      ))}
    </div>
  );
}

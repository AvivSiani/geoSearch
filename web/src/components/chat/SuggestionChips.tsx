import { he } from '../../i18n/he';

/** Example questions on the greeting. Clicking one sends it as a user message. */
export function SuggestionChips({
  onPick,
  disabled,
  suggestions = he.chat.examples,
}: {
  onPick(text: string): void;
  disabled?: boolean;
  suggestions?: readonly string[];
}) {
  return (
    <div className="suggestions" role="group" aria-label={he.chat.examplesAria}>
      {suggestions.map((s) => (
        <button
          key={s}
          type="button"
          className="suggestion"
          onClick={() => onPick(s)}
          disabled={disabled}
          title={disabled ? he.chat.needArea : undefined}
        >
          {s}
        </button>
      ))}
    </div>
  );
}

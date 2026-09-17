import { cardLabel, editionClass, visualModifiers } from "./cardPresentation";

export function CardName({
  label,
  effects,
  faceDown = false,
}: {
  label: string;
  effects: string[];
  faceDown?: boolean;
}) {
  return (
    <span
      className={`card-name ${editionClass(effects, faceDown)}`}
      aria-label={cardLabel(label, effects, faceDown)}
    >
      {faceDown ? "Hidden" : label}
    </span>
  );
}

export function CardModifiers({
  effects,
  faceDown = false,
}: {
  effects: string[];
  faceDown?: boolean;
}) {
  const modifiers = visualModifiers(effects, faceDown);
  if (!modifiers.length) return null;
  return (
    <span className="card-modifiers" aria-label="Card modifiers">
      {modifiers.map((m) => (
        <span
          key={m.kind}
          className={`modifier-badge ${m.tone}`}
          title={m.label}
        >
          {m.kind === "seal" && (
            <span className="seal-stamp" aria-hidden="true">
              ●
            </span>
          )}
          {m.kind === "eternal" && <span aria-hidden="true">∞ </span>}
          {m.label}
        </span>
      ))}
    </span>
  );
}

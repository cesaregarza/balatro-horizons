/** Display only modifiers explicitly present in the public record. */
const editions: Record<string, [string, string]> = {
  FOIL: ["F", "Foil"],
  HOLO: ["H", "Holographic"],
  HOLOGRAPHIC: ["H", "Holographic"],
  POLYCHROME: ["P", "Polychrome"],
  NEGATIVE: ["N", "Negative"],
};
const enhancements = new Set([
  "BONUS",
  "MULT",
  "WILD",
  "GLASS",
  "STEEL",
  "STONE",
  "GOLD",
  "LUCKY",
]);
const seals = new Set(["RED", "BLUE", "GOLD", "PURPLE"]);

export type CardModifier = {
  kind: string;
  short: string;
  label: string;
  tone: string;
};

export function visualModifiers(
  effects: string[] = [],
  faceDown = false,
): CardModifier[] {
  if (faceDown) return [];
  const found = new Map<string, CardModifier>();
  const add = (kind: string, short: string, label: string, tone = kind) =>
    found.set(kind, { kind, short, label, tone });
  for (const effect of effects) {
    const match =
      /^(edition|seal|enhancement|eternal|perishable|rental):\s*(.+)$/i.exec(
        effect.trim(),
      );
    if (!match) {
      if (effect.trim().toLowerCase() === "debuffed")
        add("debuffed", "D", "Debuffed");
      continue;
    }
    const key = match[1].toLowerCase(),
      value = match[2].trim();
    if (
      key === "edition" &&
      !["BASE", "NONE", "DEFAULT"].includes(value.toUpperCase())
    ) {
      const edition = editions[value.toUpperCase()];
      add(
        key,
        edition?.[0] || value,
        edition?.[1] || `Edition: ${value}`,
        edition ? `edition-${edition[1].toLowerCase()}` : "edition-unknown",
      );
    }
    if (key === "seal" && seals.has(value.toUpperCase())) {
      const name = value[0].toUpperCase() + value.slice(1).toLowerCase();
      add(key, `${name} seal`, `${name} seal`, `seal-${name.toLowerCase()}`);
    }
    if (key === "enhancement" && enhancements.has(value.toUpperCase())) {
      const name = value[0].toUpperCase() + value.slice(1).toLowerCase();
      add(key, name, name, `enhancement-${name.toLowerCase()}`);
    }
    if (key === "eternal" && value.toLowerCase() === "true")
      add(key, "∞", "Eternal");
    if (key === "rental" && value.toLowerCase() === "true")
      add(key, "R", "Rental");
    if (key === "perishable" && /^\d+$/.test(value))
      add(key, `X${value}`, `Perishable (${value} rounds left)`);
    else if (key === "perishable" && value.toLowerCase() === "true")
      add(key, "X", "Perishable");
  }
  return [
    "edition",
    "enhancement",
    "seal",
    "eternal",
    "perishable",
    "rental",
    "debuffed",
  ].flatMap((key) => (found.has(key) ? [found.get(key)!] : []));
}

export function cardModifiers(effects: string[] = []): [string, string][] {
  return visualModifiers(effects).map((m) => [m.short, m.label]);
}

export function editionClass(effects: string[] = [], faceDown = false) {
  return (
    visualModifiers(effects, faceDown).find((m) => m.kind === "edition")
      ?.tone || ""
  );
}

export function cardClasses(effects: string[] = [], faceDown = false) {
  return visualModifiers(effects, faceDown)
    .filter((m) => ["edition", "enhancement", "debuffed"].includes(m.kind))
    .map((m) => `card-${m.tone}`)
    .join(" ");
}

export function cardLabel(
  label: string,
  effects: string[] = [],
  faceDown = false,
) {
  if (faceDown) return "Hidden";
  const markers = cardModifiers(effects).map(([short]) => short);
  return label + (markers.length ? ` (${markers.join(" · ")})` : "");
}

export function modifierDescription(effects: string[] = [], faceDown = false) {
  return faceDown
    ? undefined
    : cardModifiers(effects)
        .map(([, name]) => name)
        .join(" · ") || undefined;
}

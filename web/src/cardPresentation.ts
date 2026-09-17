/** Display only modifiers explicitly present in the public record. */
const editions: Record<string, [string, string]> = {
  FOIL: ["F", "Foil"],
  HOLO: ["H", "Holographic"],
  HOLOGRAPHIC: ["H", "Holographic"],
  POLYCHROME: ["P", "Polychrome"],
  NEGATIVE: ["N", "Negative"],
};

export function cardModifiers(effects: string[] = []) {
  const found = new Map<string, [string, string]>();
  for (const effect of effects) {
    const match = /^(edition|eternal|perishable|rental):\s*(.+)$/i.exec(
      effect.trim(),
    );
    if (!match) {
      if (effect.trim().toLowerCase() === "debuffed")
        found.set("debuffed", ["D", "Debuffed"]);
      continue;
    }
    const key = match[1].toLowerCase(),
      value = match[2].trim();
    if (
      key === "edition" &&
      !["BASE", "NONE", "DEFAULT"].includes(value.toUpperCase())
    )
      found.set(
        key,
        editions[value.toUpperCase()] || [value, `Edition: ${value}`],
      );
    if (key === "eternal" && value.toLowerCase() === "true")
      found.set(key, ["∞", "Eternal"]);
    if (key === "rental" && value.toLowerCase() === "true")
      found.set(key, ["R", "Rental"]);
    if (key === "perishable" && /^\d+$/.test(value))
      found.set(key, [`X${value}`, `Perishable (${value} rounds left)`]);
    else if (key === "perishable" && value.toLowerCase() === "true")
      found.set(key, ["X", "Perishable"]);
  }
  return ["edition", "eternal", "perishable", "rental", "debuffed"].flatMap(
    (key) => (found.has(key) ? [found.get(key)!] : []),
  );
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

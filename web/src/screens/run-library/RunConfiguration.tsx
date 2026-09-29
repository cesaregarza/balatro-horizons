import options from "../../../../src/balatro_horizons/run_options.json";

export const gameChoiceLabel = (value: string) => value.charAt(0) + value.slice(1).toLowerCase();

export function RunConfiguration({ deck, stake, onDeck, onStake, disabled }: {
  deck: string;
  stake: string;
  onDeck: (value: string) => void;
  onStake: (value: string) => void;
  disabled: boolean;
}) {
  return <div className="form-row">
    <label>Deck<select aria-label="Deck" value={deck} onChange={(event) => onDeck(event.target.value)} disabled={disabled}>
      {options.decks.map((value) => <option key={value} value={value}>{gameChoiceLabel(value)} Deck</option>)}
    </select></label>
    <label>Stake (difficulty)<select aria-label="Stake (difficulty)" value={stake} onChange={(event) => onStake(event.target.value)} disabled={disabled}>
      {options.stakes.map((value, index) => <option key={value} value={value}>{gameChoiceLabel(value)} · {index + 1} of 8</option>)}
    </select></label>
  </div>;
}

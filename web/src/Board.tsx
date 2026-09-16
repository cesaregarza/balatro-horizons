import { useState } from "react";
import type { Observation, Action, Card } from "./api";
const suits: Record<string, string> = {
  Hearts: "♥",
  Diamonds: "♦",
  Clubs: "♣",
  Spades: "♠",
};
function CardTile({
  card,
  selected,
  onClick,
}: {
  card: Card;
  selected?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      className={
        "playing-card " +
        (card.face_down ? "concealed " : "") +
        (selected ? "selected " : "") +
        (["Hearts", "Diamonds"].includes(card.suit || "") ? "red" : "")
      }
      onClick={onClick}
      aria-pressed={selected}
      title={card.effects.join("\n")}
    >
      <strong>{card.face_down ? "?" : card.rank || "✦"}</strong>
      <span>
        {card.face_down ? "Hidden" : suits[card.suit || ""] || card.label}
      </span>
      <small>{card.face_down ? "Unknown identity" : card.label}</small>
    </button>
  );
}
export function Board({
  observation: o,
  onAction,
}: {
  observation: Observation;
  onAction?: (a: Action) => void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const available = (type: string) => o.available_action_types.includes(type);
  const canReorder = (area: string) =>
    available("reorder") && o.action_constraints.reorder?.areas.includes(area);
  const act = (action: Action) => {
    onAction?.(action);
    setSelected([]);
  };
  const select = (id: string) =>
    setSelected((ids) =>
      ids.includes(id) ? ids.filter((i) => i !== id) : [...ids, id],
    );
  const r = o.state.resources;
  return (
    <div className="board">
      <div className="stats">
        {[
          ["Ante", o.state.progress.ante],
          ["Blind", o.state.progress.blind],
          ["Money", r.money === null ? "Unknown" : "$" + r.money],
          ["Hands", r.hands],
          ["Discards", r.discards],
          ["Chips", r.chips],
          ["Target", r.target],
        ].map(([k, v]) => (
          <div key={k}>
            <small>{k}</small>
            <strong>{v ?? "Unknown"}</strong>
          </div>
        ))}
      </div>
      <div className="area-title">
        <h3>Hand</h3>
        <span>{o.phase.replaceAll("_", " ").toLowerCase()}</span>
      </div>
      <div className="cards">
        {o.state.hand.map((card) => (
          <div key={card.id}>
            <CardTile
              card={card}
              selected={selected.includes(card.id)}
              onClick={onAction ? () => select(card.id) : undefined}
            />
            {onAction &&
              canReorder("hand") &&
              o.state.hand.indexOf(card) > 0 && (
                <button
                  aria-label={"Move " + card.label + " left"}
                  onClick={() => {
                    const ids = o.state.hand.map((c) => c.id),
                      i = ids.indexOf(card.id);
                    [ids[i - 1], ids[i]] = [ids[i], ids[i - 1]];
                    act({ type: "reorder", area: "hand", ordered_ids: ids });
                  }}
                >
                  ← Move left
                </button>
              )}
          </div>
        ))}
        {!o.state.hand.length && (
          <p className="muted">No hand visible at this decision.</p>
        )}
      </div>
      {onAction && (
        <div className="actions">
          {["play_hand", "discard"].filter(available).map((type) => (
            <button
              key={type}
              disabled={!selected.length}
              onClick={() => act({ type, card_ids: selected })}
            >
              {type === "play_hand" ? "Play selected" : "Discard selected"}
            </button>
          ))}
        </div>
      )}
      {(["jokers", "consumables"] as const).map((area) => (
        <section key={area}>
          <h3>{area === "jokers" ? "Jokers" : "Consumables"}</h3>
          <div className="owned">
            {o.state[area].map((c, i) => (
              <article key={c.id}>
                <strong>{c.label}</strong>
                <p>{c.effects.join(" · ")}</p>
                {Object.keys(c.counters).length > 0 && (
                  <small>
                    {Object.entries(c.counters)
                      .map(([k, v]) => `${k}: ${v}`)
                      .join(" · ")}
                  </small>
                )}
                {onAction && (
                  <div className="actions">
                    {c.sellable !== false && available("sell") && (
                      <button
                        onClick={() => act({ type: "sell", owned_id: c.id })}
                      >
                        Sell
                      </button>
                    )}
                    {c.usable && available("use_consumable") && (
                      <button
                        onClick={() =>
                          act({
                            type: "use_consumable",
                            consumable_id: c.id,
                            target_ids: selected,
                          })
                        }
                      >
                        Use
                      </button>
                    )}
                    {canReorder(area) && i > 0 && (
                      <button
                        aria-label={"Move " + c.label + " left"}
                        onClick={() => {
                          const ids = o.state[area].map((c) => c.id);
                          [ids[i - 1], ids[i]] = [ids[i], ids[i - 1]];
                          act({ type: "reorder", area, ordered_ids: ids });
                        }}
                      >
                        ←
                      </button>
                    )}
                  </div>
                )}
              </article>
            ))}
            {!o.state[area].length && <p className="muted">None</p>}
          </div>
        </section>
      ))}
      {onAction && canReorder("hand") && o.state.hand.length > 1 && (
        <button
          onClick={() =>
            act({
              type: "reorder",
              area: "hand",
              ordered_ids: o.state.hand.map((c) => c.id).reverse(),
            })
          }
        >
          Reverse hand order
        </button>
      )}
      {o.state.offers.length > 0 && (
        <section>
          <h3>{available("choose_pack") ? "Pack choices" : "Shop"}</h3>
          <div className="owned offers">
            {o.state.offers.map((offer) => (
              <article key={offer.id}>
                <small>{offer.kind}</small>
                <h4>{offer.label}</h4>
                <p>{offer.effects.join(" · ")}</p>
                <b>${offer.price}</b>
                {onAction && (
                  <div className="actions">
                    {available("buy") && offer.acquire_allowed && (
                      <button
                        onClick={() => act({ type: "buy", offer_id: offer.id })}
                      >
                        Buy
                      </button>
                    )}
                    {available("buy") && offer.buy_and_use_allowed && (
                      <button
                        onClick={() =>
                          act({
                            type: "buy",
                            offer_id: offer.id,
                            mode: "buy_and_use",
                            target_ids: selected,
                          })
                        }
                      >
                        Buy & use
                      </button>
                    )}
                    {available("choose_pack") && offer.acquire_allowed && (
                      <button
                        onClick={() =>
                          act({
                            type: "choose_pack",
                            offer_id: offer.id,
                            target_ids: selected,
                          })
                        }
                      >
                        Choose
                      </button>
                    )}
                  </div>
                )}
              </article>
            ))}
          </div>
        </section>
      )}
      <details>
        <summary>Blind previews & persistent effects</summary>
        {o.state.revealed_blinds.map((b) => (
          <p key={b.id}>
            <b>{b.label}</b> · {b.target} chips · {b.effects.join(" · ")}
            {b.status && <small> · {b.status.toLowerCase()}</small>}
            {b.disabled && <strong> · Effects disabled</strong>}
            {b.skip_reward && (
              <span>
                <br />
                Offered on skip: <b>{b.skip_reward.label}</b> ·{" "}
                {b.skip_reward.effects.join(" · ")}
              </span>
            )}
          </p>
        ))}
        {(["owned_vouchers", "pending_tags"] as const).map((area) =>
          o.state[area] != null ? (
            <section
              key={area}
              aria-label={
                area === "owned_vouchers" ? "Owned vouchers" : "Pending tags"
              }
            >
              <h4>
                {area === "owned_vouchers" ? "Owned vouchers" : "Pending tags"}
              </h4>
              {o.state[area]!.map((effect, index) => (
                <p key={index}>
                  <b>{effect.label}</b> · {effect.effects.join(" · ")}
                </p>
              ))}
              {!o.state[area]!.length && <p className="muted">None</p>}
            </section>
          ) : null,
        )}
        {!!o.state.persistent_effects.length && (
          <p>{o.state.persistent_effects.join(" · ")}</p>
        )}
        {o.state.owned_vouchers == null &&
          o.state.pending_tags == null &&
          !o.state.persistent_effects.length && (
            <p>No persistent effects recorded.</p>
          )}
      </details>
      <details>
        <summary>Hand levels</summary>
        {Object.entries(o.state.hand_levels).map(([k, v]) => (
          <p key={k}>
            {k}: {v}
          </p>
        ))}
      </details>
      {onAction && (
        <div className="actions">
          {["select_blind", "skip_blind"].filter(available).map((type) => (
            <button
              key={type}
              onClick={() =>
                act({ type, blind_id: o.state.revealed_blinds[0].id })
              }
            >
              {type === "select_blind" ? "Play blind" : "Skip blind"}
            </button>
          ))}
          {["cash_out", "leave_shop", "reroll_shop", "reroll_boss", "skip_pack"]
            .filter(available)
            .map((type) => (
              <button key={type} onClick={() => act({ type })}>
                {type.replaceAll("_", " ")}
              </button>
            ))}
        </div>
      )}
    </div>
  );
}

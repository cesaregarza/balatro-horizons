import { Board } from "../Board";
import { humanAction } from "../api/client";

export function HumanControl({ human, setHuman, run }: any) {
  return <><h1>Human control</h1><p className="muted">Your actions use the same public information and validation as model actions.</p>{human?.waiting ? <Board key={human.context.observation.observation_id} observation={human.context.observation} onAction={(action) => run(async () => { await humanAction({ kind: "action", envelope: { observation_id: human.context.observation.observation_id, action } }); setHuman(null); })} /> : <section className="empty"><span>♧</span><h3>No human decision is waiting.</h3><p>Start a human run or take over a certified branch.</p></section>}</>;
}

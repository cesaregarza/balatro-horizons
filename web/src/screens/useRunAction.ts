import { useState } from "react";

export function useRunAction(run: (task: () => Promise<void>) => Promise<void>) {
  const [, setBusy] = useState(false);
  return async (task: () => Promise<void>) => {
    setBusy(true);
    try {
      await run(task);
    } finally {
      setBusy(false);
    }
  };
}

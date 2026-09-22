import { expect, type Page } from "@playwright/test";

export async function awaitIdleWorker(page: Page, operatorToken?: string) {
  const token =
    operatorToken ??
    (await (await page.request.get("/api/bootstrap")).json()).operator_token;
  const headers = { "X-BH-Operator": token };
  await expect
    .poll(async () => {
      const response = await page.request.get("/api/operator/status", {
        headers,
      });
      expect(response.ok(), await response.text()).toBe(true);
      const status = await response.json();
      return { running: status.running, active_episode: status.active_episode };
    })
    .toEqual({ running: false, active_episode: null });
}

#!/usr/bin/env node
// Review only: this script never launches the game, branches, or calls a provider.
import { chromium, expect } from '../node_modules/@playwright/test/index.mjs';
import { readFile, writeFile } from 'node:fs/promises';
import { isIP } from 'node:net';
const usage = 'Usage: verify_browser_native.mjs EPISODE_ID [BRANCH_ID] | --release | --explore EPISODE_ID [DECISION_ID]\nExplorer mode reads recorded decisions on desktop and phone; it records retrospective exposure. BH_WORKBENCH_URL defaults to http://127.0.0.1:8765.';
if (process.argv[2] === '--help') {
  console.log(usage);
  process.exit(0);
}
if (process.argv[2] === '--explore') {
  const eid = process.argv[3];
  const decision = Number(process.argv[4] ?? '0');
  if (!/^[a-f0-9]{32}$/.test(eid || '') || !/^\d+$/.test(process.argv[4] ?? '0') || !Number.isSafeInteger(decision) || decision < 0 || process.argv.length > 5) throw new Error(usage);
  const origin = process.env.BH_WORKBENCH_URL || 'http://127.0.0.1:8765';
  const artifacts = new URL('../../reports/verification/', import.meta.url);
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1512, height: 1100 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/**', route => {
      const request = route.request();
      if (/\/api\/(runs|branches|verify|stop)(\/|$)/.test(new URL(request.url()).pathname) && request.method() !== 'GET') {
        errors.push('Explorer attempted a game mutation');
        return route.abort();
      }
      return route.continue();
    });
    await page.goto(`${origin}/#explore/${eid}/${decision}`);
    const list = page.getByRole('region', { name: 'Recorded choices' });
    const detail = page.getByRole('region', { name: 'Decision details' });
    await expect(list).toBeVisible();
    const total = await list.locator('button').count();
    expect(total).toBeGreaterThan(0);
    const selected = list.locator('button').filter({ has: page.getByText(`#${decision + 1}`, { exact: true }) });
    await selected.click();
    await expect(detail.getByRole('button', { name: 'Annotate this decision' })).toBeVisible();
    await page.screenshot({ path: new URL('native-explorer-desktop.png', artifacts).pathname, fullPage: true });
    await page.getByRole('combobox', { name: 'Action filter', exact: true }).selectOption('uncommitted');
    const uncommitted = await list.locator('button').count();
    if (uncommitted) await expect(detail.getByRole('button', { name: 'After decision', exact: true })).toBeDisabled();
    await page.getByRole('button', { name: 'Clear filters' }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(list).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: new URL('native-explorer-mobile.png', artifacts).pathname, fullPage: true });
    await selected.click();
    await expect(detail.getByRole('button', { name: 'Annotate this decision' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.getByRole('button', { name: 'Back to choices' }).click();
    await expect(selected).toBeFocused();
    expect(errors).toEqual([]);
    const result = { status: 'passed', episode_id: eid, decision, total_requests: total, uncommitted_requests: uncommitted,
      checks: ['retrospective navigation', 'uncommitted filter', 'desktop detail', 'phone detail and return', 'no horizontal overflow', 'no browser errors or game mutation'] };
    await writeFile(new URL('native-explorer.json', artifacts), JSON.stringify(result, null, 2) + '\n');
    console.log(JSON.stringify(result));
  } finally { await browser.close(); }
  process.exit(0);
}
const release = process.argv[2] === '--release'
  ? JSON.parse(await readFile(new URL('../../reports/verification/native-release.json', import.meta.url), 'utf8'))
  : null;
const episode = release?.parent_episode_id || process.argv[2];
const branch = release?.branch.episode_id || process.argv[3];
if (!/^[a-f0-9]{32}$/.test(episode || '') || (branch && !/^[a-f0-9]{32}$/.test(branch))) {
  throw new Error('Usage: node web/scripts/verify_browser_native.mjs EPISODE_ID [BRANCH_ID] or --release');
}
const root = new URL('../../reports/verification/', import.meta.url);
const mobile = process.env.BH_MOBILE === '1';
const artifactSuffix = mobile ? '-mobile' : '';
const origin = process.env.BH_WORKBENCH_URL || 'http://127.0.0.1:8765';
const resolvedIp = process.env.BH_WORKBENCH_IP;
if (resolvedIp && !isIP(resolvedIp)) throw new Error('BH_WORKBENCH_IP must be an IP address');
const browser = await chromium.launch({ args: resolvedIp ? ['--host-resolver-rules=MAP ' + new URL(origin).hostname + ' ' + resolvedIp] : [] });
const page = await browser.newPage({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1050 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
try {
  await page.goto(origin);
  if (mobile) expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const row = page.getByRole('row').filter({ hasText: episode.slice(0, 10) });
  await expect(row).toContainText('Native');
  await row.getByRole('button', { name: 'Review →' }).click();
  await expect(page.getByRole('heading', { name: 'What was knowable here?' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Recorded decision' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Run ended' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Reveal agent action', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Recorded decision' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'After the action' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Reveal consequences', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'After the action' })).toBeVisible();
  await page.getByRole('button', { name: 'Next decision', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Recorded decision' })).toHaveCount(0);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: new URL('native-review' + artifactSuffix + '.png', root).pathname, fullPage: true });
  if (branch) {
    await page.getByRole('button', { name: 'Runs', exact: true }).click();
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await page.getByRole('row').filter({ hasText: branch.slice(0, 10) })
      .getByRole('button', { name: 'Compare outcomes', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Original run', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Branch', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Revealed trajectory' })).toHaveCount(2);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: new URL('native-comparison' + artifactSuffix + '.png', root).pathname, fullPage: true });
  }
  expect(errors).toEqual([]);
  const result = { status: 'passed', origin, mobile, dns_override: Boolean(resolvedIp), episode_id: episode, branch_id: branch || null,
    checks: ['native library provenance', 'prospective withholding', 'progressive reveal',
      'native hand rendering', ...(branch ? ['original/branch comparison'] : [])], browser_errors: errors };
  await writeFile(new URL('native-browser' + artifactSuffix + '.json', root), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} finally { await browser.close(); }

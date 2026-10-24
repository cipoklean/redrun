// One off mobile check: renders the two screens at phone width and reports overflow.
// Usage: node scripts/mobile-shots.mjs [base-url] [output-prefix]
import { chromium } from "playwright";

const base = process.argv[2] ?? "http://127.0.0.1:8001";
const prefix = process.argv[3] ?? "/tmp/mobile";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });

await page.goto(`${base}/`, { waitUntil: "networkidle" });
await page.waitForSelector("text=Sample cases");
await page.screenshot({ path: `${prefix}-investigate.png`, fullPage: true });

await page.getByRole("button", { name: "Run sample Dependency bump" }).click();
await page.waitForURL(/\/run\/[0-9a-f]{12}/, { timeout: 30_000 });
await page.waitForSelector("text=next step", { timeout: 60_000 });
await page.screenshot({ path: `${prefix}-verdict.png`, fullPage: true });

const box = await page.evaluate(() => ({
  scrollWidth: document.documentElement.scrollWidth,
  clientWidth: document.documentElement.clientWidth,
}));
console.log(`viewport ${box.clientWidth}px, content ${box.scrollWidth}px`);
await browser.close();

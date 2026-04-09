import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { chromium } from "playwright";
import { PNG } from "pngjs";
import pixelmatch from "pixelmatch";

const OUT_DIR = "documentation/artifacts/van_phase2";
const PORT = 4173;
const BASE = `http://127.0.0.1:${PORT}`;

mkdirSync(OUT_DIR, { recursive: true });

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function run() {
  const server = spawn("python3", ["-m", "http.server", String(PORT), "--directory", "gui"], {
    stdio: "inherit",
  });

  try {
    await sleep(1200);

    const browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });

    await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
    await page.screenshot({ path: `${OUT_DIR}/legacy.png`, fullPage: true });

    await page.goto(`${BASE}/?ui=van`, { waitUntil: "networkidle" });
    await page.screenshot({ path: `${OUT_DIR}/van.png`, fullPage: true });

    await browser.close();

    const legacy = PNG.sync.read(readFileSync(`${OUT_DIR}/legacy.png`));
    const van = PNG.sync.read(readFileSync(`${OUT_DIR}/van.png`));
    const width = Math.min(legacy.width, van.width);
    const height = Math.min(legacy.height, van.height);

    const diff = new PNG({ width, height });
    const diffPixels = pixelmatch(
      legacy.data,
      van.data,
      diff.data,
      width,
      height,
      { threshold: 0.1 },
    );
    writeFileSync(`${OUT_DIR}/legacy_vs_van_diff.png`, PNG.sync.write(diff));

    const metrics = {
      width,
      height,
      diff_pixels: diffPixels,
      diff_ratio: Number((diffPixels / (width * height)).toFixed(6)),
      generated_at_utc: new Date().toISOString(),
    };
    writeFileSync(`${OUT_DIR}/diff_metrics.json`, JSON.stringify(metrics, null, 2));
    console.log("[van-ui-diff]", metrics);
  } finally {
    server.kill("SIGTERM");
  }
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});

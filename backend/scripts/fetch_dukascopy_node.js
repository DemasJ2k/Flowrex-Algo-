/**
 * Fast Dukascopy data fetcher using dukascopy-node.
 * Downloads M5/H1/H4/D1 OHLCV for all 5 symbols.
 *
 * Usage:
 *   node fetch_dukascopy_node.js US30 2500
 *   node fetch_dukascopy_node.js all 2500
 */
const { getHistoricalRates } = require("dukascopy-node");
const fs = require("fs");
const path = require("path");

const HIST_DIR = path.resolve(__dirname, "..", "..", "History Data", "data");

const SYMBOL_MAP = {
  US30: "usa30idxusd",
  BTCUSD: "btcusd",
  XAUUSD: "xauusd",
  ES: "usa500idxusd",
  NAS100: "usatechidxusd",
};

const TIMEFRAMES = ["m5", "h1", "h4", "d1"];
const TF_LABELS = { m5: "M5", h1: "H1", h4: "H4", d1: "D1" };
const MAX_BARS = 500000;

async function fetchSymbol(symbol, tf, days) {
  const instrument = SYMBOL_MAP[symbol];
  if (!instrument) {
    console.log(`  Unknown symbol: ${symbol}`);
    return null;
  }

  const end = new Date();
  const start = new Date();
  start.setDate(start.getDate() - days);

  const label = TF_LABELS[tf] || tf;
  console.log(
    `  Fetching ${symbol} (${instrument}) ${label} from ${start.toISOString().slice(0, 10)} to ${end.toISOString().slice(0, 10)}...`
  );

  try {
    const data = await getHistoricalRates({
      instrument,
      dates: {
        from: start.toISOString(),
        to: end.toISOString(),
      },
      timeframe: tf,
      format: "json",
      priceType: "bid",
    });

    if (!data || data.length === 0) {
      console.log(`  No data returned for ${symbol} ${label}`);
      return null;
    }

    // Cap bars
    let rows = data;
    if (rows.length > MAX_BARS) {
      console.log(`  Capping from ${rows.length.toLocaleString()} to ${MAX_BARS.toLocaleString()} bars`);
      rows = rows.slice(-MAX_BARS);
    }

    console.log(`  Got ${rows.length.toLocaleString()} bars`);
    return rows;
  } catch (err) {
    console.log(`  ERROR: ${err.message}`);
    return null;
  }
}

function saveCSV(symbol, tf, rows) {
  const label = TF_LABELS[tf] || tf;
  const outDir = path.join(HIST_DIR, symbol);
  fs.mkdirSync(outDir, { recursive: true });

  const outPath = path.join(outDir, `${symbol}_${label}.csv`);

  // Convert to CSV rows: time,open,high,low,close,volume
  const csvRows = rows.map((r) => {
    const time = Math.floor(new Date(r.timestamp).getTime() / 1000);
    return `${time},${r.open},${r.high},${r.low},${r.close},${r.volume}`;
  });

  // Read existing file and merge
  let existingMap = new Map();
  if (fs.existsSync(outPath)) {
    const existing = fs.readFileSync(outPath, "utf-8").trim().split("\n");
    const header = existing[0];
    for (let i = 1; i < existing.length; i++) {
      const parts = existing[i].split(",");
      existingMap.set(parts[0], existing[i]);
    }
    console.log(`  Existing: ${existingMap.size.toLocaleString()} rows`);
  }

  // Add new rows (overwrite on conflict)
  for (const row of csvRows) {
    const time = row.split(",")[0];
    existingMap.set(time, row);
  }

  // Sort by time and write
  const sorted = [...existingMap.entries()]
    .sort((a, b) => parseInt(a[0]) - parseInt(b[0]))
    .map(([, v]) => v);

  const output = "time,open,high,low,close,volume\n" + sorted.join("\n") + "\n";
  fs.writeFileSync(outPath, output);
  console.log(`  Saved: ${outPath} (${sorted.length.toLocaleString()} total rows)`);
}

async function main() {
  const args = process.argv.slice(2);
  const symbolArg = (args[0] || "US30").toUpperCase();
  const days = parseInt(args[1] || "2500");

  const symbols =
    symbolArg === "ALL" ? Object.keys(SYMBOL_MAP) : [symbolArg];

  console.log("=".repeat(60));
  console.log("  Dukascopy Data Fetcher (Node.js — fast)");
  console.log(`  Symbols: ${symbols.join(", ")}`);
  console.log(`  Days: ${days}`);
  console.log("=".repeat(60));

  for (const sym of symbols) {
    console.log(`\n--- ${sym} ---`);
    for (const tf of TIMEFRAMES) {
      const data = await fetchSymbol(sym, tf, days);
      if (data && data.length > 0) {
        saveCSV(sym, tf, data);
      }
    }
  }

  console.log("\n" + "=".repeat(60));
  console.log("  Download complete!");
  console.log("=".repeat(60));
}

main().catch(console.error);

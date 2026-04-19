/**
 * Fast Dukascopy data fetcher using dukascopy-node.
 * Downloads M5/H1/H4/D1 OHLCV for all symbols.
 *
 * Usage:
 *   node fetch_dukascopy_node.js US30 2500
 *   node fetch_dukascopy_node.js all 2500
 *   node fetch_dukascopy_node.js ETHUSD 2500
 */
const { getHistoricalRates } = require("dukascopy-node");
const fs = require("fs");
const path = require("path");

const HIST_DIR = path.resolve(__dirname, "..", "..", "History Data", "data");

const SYMBOL_MAP = {
  US30:    "usa30idxusd",
  BTCUSD:  "btcusd",
  XAUUSD:  "xauusd",
  ES:      "usa500idxusd",
  NAS100:  "usatechidxusd",
  ETHUSD:  "ethusd",
  XAGUSD:  "xagusd",
  AUS200:  "ausidxaud",
};

const TIMEFRAMES = ["m5", "h1", "h4", "d1"];
const TF_LABELS = { m5: "M5", h1: "H1", h4: "H4", d1: "D1" };
const MAX_BARS = 500000;

// Sleep helper for retry backoff
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// M5 chunking: Dukascopy rejects huge M5 date ranges. Split into 6-month windows.
const M5_CHUNK_DAYS = 180;

async function fetchChunk(instrument, tf, from, to, attempt = 1) {
  try {
    const data = await getHistoricalRates({
      instrument,
      dates: { from: from.toISOString(), to: to.toISOString() },
      timeframe: tf,
      format: "json",
      priceType: "bid",
    });
    return data || [];
  } catch (err) {
    if (attempt < 3) {
      const backoff = 1000 * Math.pow(2, attempt - 1); // 1s, 2s, 4s
      console.log(`  ⚠ attempt ${attempt} failed (${err.message}), retrying in ${backoff}ms...`);
      await sleep(backoff);
      return fetchChunk(instrument, tf, from, to, attempt + 1);
    }
    throw err;
  }
}

async function fetchSymbol(symbol, tf, days, sinceTs = null) {
  const instrument = SYMBOL_MAP[symbol];
  if (!instrument) {
    console.log(`  Unknown symbol: ${symbol}`);
    return null;
  }

  const end = new Date();
  let start = new Date();
  start.setDate(start.getDate() - days);

  // Incremental mode: if --since was provided and is more recent than
  // the days-window start, fetch only from that point. Reduces a 14-chunk
  // M5 download to 1-2 chunks for most realistic catch-ups.
  if (sinceTs) {
    const sinceDate = new Date(sinceTs * 1000);
    if (sinceDate > start) start = sinceDate;
    if (sinceDate >= end) {
      console.log(`  ${symbol} ${tf}: --since ${sinceDate.toISOString()} is >= now, nothing to fetch`);
      return [];
    }
  }

  const label = TF_LABELS[tf] || tf;
  console.log(
    `  Fetching ${symbol} (${instrument}) ${label} from ${start.toISOString().slice(0, 10)} to ${end.toISOString().slice(0, 10)}...`
  );

  try {
    let allData = [];

    // M5 fetches for long ranges are flaky — chunk into 6-month windows
    if (tf === "m5" && days > M5_CHUNK_DAYS) {
      let chunkStart = new Date(start);
      let chunkNum = 0;
      const totalChunks = Math.ceil(days / M5_CHUNK_DAYS);
      while (chunkStart < end) {
        chunkNum++;
        const chunkEnd = new Date(chunkStart);
        chunkEnd.setDate(chunkEnd.getDate() + M5_CHUNK_DAYS);
        if (chunkEnd > end) chunkEnd.setTime(end.getTime());
        process.stdout.write(`    chunk ${chunkNum}/${totalChunks} (${chunkStart.toISOString().slice(0, 10)} → ${chunkEnd.toISOString().slice(0, 10)})... `);
        try {
          const chunk = await fetchChunk(instrument, tf, chunkStart, chunkEnd);
          process.stdout.write(`${chunk.length} bars\n`);
          allData = allData.concat(chunk);
        } catch (err) {
          process.stdout.write(`FAILED (${err.message})\n`);
          // Fatal for M5: propagate the failure so caller knows
          console.log(`  ERROR: M5 chunk fetch failed after retries: ${err.message}`);
          return null;
        }
        chunkStart = chunkEnd;
      }
    } else {
      allData = await fetchChunk(instrument, tf, start, end);
    }

    if (!allData || allData.length === 0) {
      console.log(`  No data returned for ${symbol} ${label}`);
      return null;
    }

    // Cap bars
    let rows = allData;
    if (rows.length > MAX_BARS) {
      console.log(`  Capping from ${rows.length.toLocaleString()} to ${MAX_BARS.toLocaleString()} bars`);
      rows = rows.slice(-MAX_BARS);
    }

    console.log(`  ✓ Got ${rows.length.toLocaleString()} bars`);
    return rows;
  } catch (err) {
    console.log(`  ERROR: ${err.message}`);
    return null;
  }
}

function saveCSV(symbol, tf, rows, outDirOverride = null) {
  const label = TF_LABELS[tf] || tf;
  // If outDirOverride is set (e.g. backtest tempdir), write to it directly.
  // Otherwise write to the persistent History Data layout.
  const outDir = outDirOverride
    ? outDirOverride
    : path.join(HIST_DIR, symbol);
  fs.mkdirSync(outDir, { recursive: true });

  const outPath = path.join(outDir, `${symbol}_${label}.csv`);

  // Convert to CSV rows: time,open,high,low,close,volume
  const csvRows = rows.map((r) => {
    const time = Math.floor(new Date(r.timestamp).getTime() / 1000);
    return `${time},${r.open},${r.high},${r.low},${r.close},${r.volume}`;
  });

  // Read existing file and merge — only keep rows with valid Unix int timestamps
  let existingMap = new Map();
  if (fs.existsSync(outPath)) {
    const existing = fs.readFileSync(outPath, "utf-8").trim().split("\n");
    const header = existing[0];
    const headerCols = header.split(",");
    const timeIdx = headerCols.indexOf("time");
    const tsEventIdx = headerCols.indexOf("ts_event");

    for (let i = 1; i < existing.length; i++) {
      const parts = existing[i].split(",");
      let timeVal;

      if (timeIdx >= 0) {
        // Parse time column — could be Unix int or date string
        const raw = parts[timeIdx];
        const asInt = parseInt(raw);
        if (!isNaN(asInt) && asInt > 1e9) {
          timeVal = asInt;
        } else {
          // String date — parse to Unix seconds
          const parsed = new Date(raw).getTime();
          if (!isNaN(parsed)) timeVal = Math.floor(parsed / 1000);
        }
      } else if (tsEventIdx >= 0) {
        // Old format: ts_event column
        const parsed = new Date(parts[tsEventIdx]).getTime();
        if (!isNaN(parsed)) timeVal = Math.floor(parsed / 1000);
      }

      if (timeVal && timeVal > 0) {
        // Reconstruct row in canonical order: time,open,high,low,close,volume
        const openIdx = headerCols.indexOf("open");
        const highIdx = headerCols.indexOf("high");
        const lowIdx = headerCols.indexOf("low");
        const closeIdx = headerCols.indexOf("close");
        const volIdx = headerCols.indexOf("volume");
        if (openIdx >= 0 && highIdx >= 0 && lowIdx >= 0 && closeIdx >= 0) {
          const vol = volIdx >= 0 ? parts[volIdx] : "0";
          const normalized = `${timeVal},${parts[openIdx]},${parts[highIdx]},${parts[lowIdx]},${parts[closeIdx]},${vol}`;
          existingMap.set(String(timeVal), normalized);
        }
      }
    }
    console.log(`  Existing: ${existingMap.size.toLocaleString()} rows (normalized)`);
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
  // Back-compat positional args: <symbol> <days> [outDir]
  // Plus keyword flags: --since=<unix_ts> (incremental fetches)
  //
  // Example (full):   node fetch_dukascopy_node.js US30 2500
  // Example (inc.):   node fetch_dukascopy_node.js US30 2500 /tmp/out --since=1745000000
  const rawArgs = process.argv.slice(2);
  const positional = [];
  let sinceTs = null;
  for (const a of rawArgs) {
    if (a.startsWith("--since=")) {
      const v = parseInt(a.slice("--since=".length));
      if (!isNaN(v) && v > 1e9) sinceTs = v;
    } else {
      positional.push(a);
    }
  }
  const symbolArg = (positional[0] || "US30").toUpperCase();
  const days = parseInt(positional[1] || "2500");
  // Optional: fetch to a specific directory (used by backtest data fetcher)
  const outDirOverride = positional[2] || null;

  const symbols =
    symbolArg === "ALL" ? Object.keys(SYMBOL_MAP) : [symbolArg];

  console.log("=".repeat(60));
  console.log("  Dukascopy Data Fetcher (Node.js — fast)");
  console.log(`  Symbols: ${symbols.join(", ")}`);
  console.log(`  Days: ${days}`);
  if (sinceTs) {
    console.log(`  Since: ${new Date(sinceTs * 1000).toISOString()} (incremental mode)`);
  }
  if (outDirOverride) console.log(`  Output dir: ${outDirOverride}`);
  console.log("=".repeat(60));

  // Per-symbol-timeframe result tracking
  const report = {}; // { symbol: { m5: 'ok'|'fail'|'skip', h1: ..., ... } }

  for (const sym of symbols) {
    console.log(`\n--- ${sym} ---`);
    report[sym] = {};
    for (const tf of TIMEFRAMES) {
      try {
        const data = await fetchSymbol(sym, tf, days, sinceTs);
        if (data && data.length > 0) {
          saveCSV(sym, tf, data, outDirOverride);
          report[sym][tf] = { status: "ok", rows: data.length };
        } else if (sinceTs && data !== null) {
          // Incremental mode returning 0 bars = already up-to-date, not a failure.
          report[sym][tf] = { status: "ok", rows: 0 };
        } else {
          report[sym][tf] = { status: "fail", rows: 0 };
        }
      } catch (err) {
        console.log(`  EXCEPTION: ${err.message}`);
        report[sym][tf] = { status: "fail", rows: 0, error: err.message };
      }
    }
  }

  // ── Summary report ───────────────────────────────────────────────
  console.log("\n" + "=".repeat(60));
  console.log("  FETCH SUMMARY");
  console.log("=".repeat(60));
  let hasCriticalFailure = false;
  for (const sym of symbols) {
    const r = report[sym] || {};
    const parts = TIMEFRAMES.map(function (tf) {
      const entry = r[tf] || {};
      const status = entry.status || "unknown";
      const mark = status === "ok" ? "✓" : "✗";
      return TF_LABELS[tf] + mark;
    });
    const line = "  " + sym.padEnd(10) + " " + parts.join("  ");
    console.log(line);
    // M5 is the critical timeframe for training — fail the whole run if any M5 fetch failed
    const m5Entry = r.m5 || {};
    if (m5Entry.status !== "ok") {
      hasCriticalFailure = true;
    }
  }

  if (hasCriticalFailure) {
    console.log("\n❌ One or more M5 fetches failed. Training will be blocked until resolved.");
    process.exit(2);
  } else {
    console.log("\n✓ All M5 fetches succeeded.");
  }
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});

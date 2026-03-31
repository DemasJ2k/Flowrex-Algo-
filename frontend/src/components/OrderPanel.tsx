"use client";

import { useState } from "react";
import Modal from "@/components/ui/Modal";
import api from "@/lib/api";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";

export default function OrderPanel({
  open,
  onClose,
  defaultSymbol = "XAUUSD",
}: {
  open: boolean;
  onClose: () => void;
  defaultSymbol?: string;
}) {
  const [symbol, setSymbol] = useState(defaultSymbol);
  const [direction, setDirection] = useState("BUY");
  const [size, setSize] = useState("0.01");
  const [orderType, setOrderType] = useState("MARKET");
  const [price, setPrice] = useState("");
  const [sl, setSl] = useState("");
  const [tp, setTp] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{ success: boolean; message: string } | null>(null);

  const handleSubmit = async () => {
    setLoading(true);
    setResult(null);
    try {
      const body: Record<string, unknown> = {
        symbol,
        direction,
        size: parseFloat(size),
        order_type: orderType,
      };
      if (orderType === "LIMIT" && price) body.price = parseFloat(price);
      if (sl) body.sl = parseFloat(sl);
      if (tp) body.tp = parseFloat(tp);

      const res = await api.post("/api/broker/order", body);
      setResult(res.data);
      if (res.data.success) {
        toast.success(`Order placed: ${direction} ${symbol} (${res.data.order_id})`);
        setTimeout(onClose, 1500);
      } else {
        toast.error(res.data.message || "Order failed");
      }
    } catch (e) {
      const msg = getErrorMessage(e);
      setResult({ success: false, message: msg });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Place Order" width="max-w-sm">
      <div className="space-y-3">
        <div>
          <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Symbol</label>
          <input
            value={symbol} onChange={(e) => setSymbol(e.target.value)}
            className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
            style={{ borderColor: "var(--border)" }}
          />
        </div>

        <div className="flex gap-2">
          {["BUY", "SELL"].map((d) => (
            <button
              key={d}
              onClick={() => setDirection(d)}
              className={`flex-1 py-2 text-sm font-medium rounded-lg border transition-colors ${
                direction === d
                  ? d === "BUY" ? "bg-emerald-600 border-emerald-600 text-white" : "bg-red-600 border-red-600 text-white"
                  : "hover:bg-white/5"
              }`}
              style={{ borderColor: direction === d ? undefined : "var(--border)" }}
            >
              {d}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Size (lots)</label>
            <input type="number" step="0.01" min="0.01" value={size} onChange={(e) => setSize(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)" }} />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Type</label>
            <select value={orderType} onChange={(e) => setOrderType(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)", background: "var(--card)" }}>
              <option value="MARKET">Market</option>
              <option value="LIMIT">Limit</option>
            </select>
          </div>
        </div>

        {orderType === "LIMIT" && (
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Price</label>
            <input type="number" step="0.01" value={price} onChange={(e) => setPrice(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)" }} />
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Stop Loss</label>
            <input type="number" step="0.01" value={sl} onChange={(e) => setSl(e.target.value)} placeholder="Optional"
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)" }} />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: "var(--muted)" }}>Take Profit</label>
            <input type="number" step="0.01" value={tp} onChange={(e) => setTp(e.target.value)} placeholder="Optional"
              className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500"
              style={{ borderColor: "var(--border)" }} />
          </div>
        </div>

        {result && (
          <p className={`text-sm ${result.success ? "text-emerald-400" : "text-red-400"}`}>{result.message}</p>
        )}

        <button onClick={handleSubmit} disabled={loading}
          className={`w-full py-2.5 text-sm font-medium rounded-lg text-white disabled:opacity-50 transition-colors ${
            direction === "BUY" ? "bg-emerald-600 hover:bg-emerald-500" : "bg-red-600 hover:bg-red-500"
          }`}>
          {loading ? "Placing..." : `${direction} ${symbol}`}
        </button>
      </div>
    </Modal>
  );
}

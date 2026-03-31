"use client";

import { useState, useRef, useEffect } from "react";
import { ChevronDown, Search } from "lucide-react";

export default function SearchableSelect({
  options,
  value,
  onChange,
  placeholder = "Select...",
  className = "",
}: {
  options: string[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [highlighted, setHighlighted] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = options.filter((o) => o.toLowerCase().includes(search.toLowerCase()));

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        setSearch("");
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlighted((h) => Math.min(h + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlighted((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter" && filtered[highlighted]) {
      onChange(filtered[highlighted]);
      setOpen(false);
      setSearch("");
    } else if (e.key === "Escape") {
      setOpen(false);
      setSearch("");
    }
  };

  return (
    <div ref={containerRef} className={"relative " + className}>
      <button
        onClick={() => { setOpen(!open); setTimeout(() => inputRef.current?.focus(), 50); }}
        className="flex items-center justify-between w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none gap-2"
        style={{ borderColor: open ? "var(--accent)" : "var(--border)", background: "var(--card)" }}
      >
        <span className="truncate">{value || placeholder}</span>
        <ChevronDown size={14} className="flex-shrink-0" style={{ color: "var(--muted)" }} />
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-1 rounded-lg border shadow-xl overflow-hidden"
          style={{ background: "var(--card)", borderColor: "var(--border)", minWidth: "180px", zIndex: 9999 }}>
          <div className="flex items-center gap-2 px-3 py-2 border-b" style={{ borderColor: "var(--border)" }}>
            <Search size={14} style={{ color: "var(--muted)" }} />
            <input
              ref={inputRef}
              type="text"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setHighlighted(0); }}
              onKeyDown={handleKeyDown}
              className="flex-1 bg-transparent text-sm outline-none"
              placeholder="Search..."
            />
          </div>
          <div className="max-h-64 overflow-y-auto">
            {filtered.length === 0 ? (
              <div className="px-3 py-2 text-xs" style={{ color: "var(--muted)" }}>No results</div>
            ) : (
              filtered.map((option, i) => (
                <button
                  key={option}
                  onClick={() => { onChange(option); setOpen(false); setSearch(""); }}
                  className={"w-full text-left px-3 py-2.5 text-sm transition-colors " + (i === highlighted ? "bg-blue-500/10 text-blue-400" : "hover:bg-white/5")}
                  style={{ color: option === value ? "var(--accent)" : undefined }}
                >
                  {option}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

"use client";
import { useState } from "react";

export default function Tooltip({ children, content }: { children: React.ReactNode; content: string }) {
  const [show, setShow] = useState(false);
  return (
    <span className="relative inline-block" onMouseEnter={() => setShow(true)} onMouseLeave={() => setShow(false)}>
      {children}
      {show && (
        <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2 py-1 text-xs rounded whitespace-nowrap z-50"
          style={{ background: "var(--card)", border: "1px solid var(--border)", color: "var(--foreground)" }}>
          {content}
        </span>
      )}
    </span>
  );
}

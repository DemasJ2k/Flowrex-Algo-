"use client";

import { ReactNode, useEffect, useRef, useId } from "react";
import { X } from "lucide-react";

export default function Modal({
  open,
  onClose,
  title,
  children,
  width = "max-w-lg",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  width?: string;
}) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const previousFocus = useRef<HTMLElement | null>(null);

  // Body scroll lock + Escape key + focus management
  useEffect(() => {
    if (!open) return;

    document.body.style.overflow = "hidden";
    previousFocus.current = document.activeElement as HTMLElement;

    // Move focus into the dialog
    requestAnimationFrame(() => {
      const el = dialogRef.current;
      if (!el) return;
      const focusable = el.querySelector<HTMLElement>(
        "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])"
      );
      (focusable || el).focus();
    });

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      // Focus trap on Tab
      if (e.key !== "Tab" || !dialogRef.current) return;
      const focusables = dialogRef.current.querySelectorAll<HTMLElement>(
        "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);

    return () => {
      document.body.style.overflow = "";
      document.removeEventListener("keydown", onKey);
      // Restore focus to the element that opened the modal
      if (previousFocus.current && typeof previousFocus.current.focus === "function") {
        try {
          previousFocus.current.focus();
        } catch {
          // no-op
        }
      }
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop — clicking it closes the modal. Marked aria-hidden so it
          isn't announced as content. */}
      <div
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`relative ${width} w-full mx-4 rounded-xl border p-6 shadow-2xl max-h-[90vh] overflow-y-auto outline-none`}
        style={{ background: "var(--card)", borderColor: "var(--border)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 id={titleId} className="text-lg font-semibold">{title}</h2>
          <button
            onClick={onClose}
            aria-label="Close dialog"
            className="p-2 rounded hover:bg-white/10 transition-colors"
          >
            <X size={18} style={{ color: "var(--muted)" }} aria-hidden="true" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

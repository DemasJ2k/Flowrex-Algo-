"use client";

import Modal from "@/components/ui/Modal";

export default function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title = "Confirm",
  message = "Are you sure?",
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  variant = "danger",
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title?: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "danger" | "warning" | "default";
}) {
  const btnColor = variant === "danger"
    ? "bg-red-600 hover:bg-red-500"
    : variant === "warning"
    ? "bg-amber-600 hover:bg-amber-500"
    : "bg-blue-600 hover:bg-blue-500";

  return (
    <Modal open={open} onClose={onClose} title={title} width="max-w-sm">
      <p className="text-sm mb-5" style={{ color: "var(--muted)" }}>{message}</p>
      <div className="flex justify-end gap-2">
        <button onClick={onClose} className="px-4 py-2 text-sm rounded-lg border hover:bg-white/5" style={{ borderColor: "var(--border)" }}>
          {cancelLabel}
        </button>
        <button onClick={() => { onConfirm(); onClose(); }} className={"px-4 py-2 text-sm font-medium rounded-lg text-white " + btnColor}>
          {confirmLabel}
        </button>
      </div>
    </Modal>
  );
}

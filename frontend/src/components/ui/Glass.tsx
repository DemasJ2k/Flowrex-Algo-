"use client";

import { HTMLAttributes, forwardRef } from "react";
import { cn } from "@/lib/utils";

interface GlassProps extends HTMLAttributes<HTMLDivElement> {
  hoverable?: boolean;
  padding?: "none" | "sm" | "md" | "lg";
}

const paddingMap = {
  none: "",
  sm: "p-3",
  md: "p-4 md:p-5",
  lg: "p-5 md:p-6",
};

/**
 * Glass morphism card — the default container for grouped content.
 * Uses .glass (from globals.css): blurred background, gradient border,
 * ambient shadow. Set `hoverable` for a subtle lift on hover.
 */
const Glass = forwardRef<HTMLDivElement, GlassProps>(function Glass(
  { className, hoverable, padding = "md", children, ...rest },
  ref
) {
  return (
    <div
      ref={ref}
      className={cn(
        "glass",
        hoverable && "glass-hover",
        paddingMap[padding],
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
});

export default Glass;

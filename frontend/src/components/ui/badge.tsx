import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--ring)] select-none",
  {
    variants: {
      variant: {
        default: "bg-[var(--primary)] text-[var(--primary-foreground)]",
        secondary: "bg-[var(--secondary)] text-[var(--secondary-foreground)]",
        outline: "border border-[var(--border)] text-[var(--foreground)]",
        success: "bg-[var(--success-subtle)] text-[var(--success)] border border-[var(--success)]/20",
        warning: "bg-[var(--warning-subtle)] text-[var(--warning)] border border-[var(--warning)]/20",
        destructive: "bg-[var(--destructive-subtle)] text-[var(--destructive)] border border-[var(--destructive)]/20",
        info: "bg-[var(--info-subtle)] text-[var(--info)] border border-[var(--info)]/20",
        muted: "bg-[var(--muted)] text-[var(--muted-foreground)]",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };

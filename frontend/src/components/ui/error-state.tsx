import * as React from "react";
import { AlertCircle, RotateCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export interface ErrorStateProps extends React.HTMLAttributes<HTMLDivElement> {
  title?: string;
  message: string;
  retryLabel?: string;
  onRetry?: () => void;
}

export function ErrorState({
  title = "Something went wrong",
  message,
  retryLabel = "Try Again",
  onRetry,
  className,
  ...props
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center rounded-xl border border-[var(--destructive)]/20 bg-[var(--destructive-subtle)] p-8 text-center text-[var(--foreground)]",
        className
      )}
      {...props}
    >
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--destructive)]/10 text-[var(--destructive)] mb-3">
        <AlertCircle className="h-6 w-6" />
      </div>
      <h3 className="text-sm font-semibold text-[var(--destructive)]">{title}</h3>
      <p className="mt-1 text-xs text-[var(--muted-foreground)] max-w-sm">{message}</p>
      {onRetry && (
        <Button
          onClick={onRetry}
          size="sm"
          variant="outline"
          className="mt-4 border-[var(--destructive)]/30 hover:bg-[var(--destructive)]/10 text-[var(--destructive)]"
        >
          <RotateCcw className="w-3.5 h-3.5 mr-1.5" />
          {retryLabel}
        </Button>
      )}
    </div>
  );
}

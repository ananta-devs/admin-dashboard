import * as React from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface ChartSkeletonProps {
  className?: string;
  height?: number | string;
  hasLegend?: boolean;
}

export function ChartSkeleton({
  className,
  height = 300,
  hasLegend = true,
}: ChartSkeletonProps) {
  const containerStyle = typeof height === "number" ? { height: `${height}px` } : { height };

  return (
    <div
      role="status"
      aria-label="Loading chart"
      className={cn("flex flex-col justify-between p-4 space-y-4", className)}
      style={containerStyle}
    >
      {/* Top Header / Legend skeleton */}
      <div className="flex items-center justify-between">
        <Skeleton className="h-4 w-32" />
        {hasLegend && (
          <div className="flex items-center space-x-2">
            <Skeleton className="h-3 w-12" />
            <Skeleton className="h-3 w-12" />
            <Skeleton className="h-3 w-12" />
          </div>
        )}
      </div>

      {/* Main visualization grid mockup */}
      <div className="flex-1 flex items-end gap-3 pt-6 pb-2 px-2">
        <Skeleton className="h-[40%] flex-1 rounded-t" />
        <Skeleton className="h-[65%] flex-1 rounded-t" />
        <Skeleton className="h-[85%] flex-1 rounded-t" />
        <Skeleton className="h-[50%] flex-1 rounded-t" />
        <Skeleton className="h-[95%] flex-1 rounded-t" />
        <Skeleton className="h-[70%] flex-1 rounded-t" />
        <Skeleton className="h-[30%] flex-1 rounded-t" />
        <Skeleton className="h-[60%] flex-1 rounded-t" />
      </div>

      {/* Bottom axis markers */}
      <div className="flex items-center justify-between pt-1 border-t border-[var(--border)]">
        <Skeleton className="h-2.5 w-8" />
        <Skeleton className="h-2.5 w-8" />
        <Skeleton className="h-2.5 w-8" />
        <Skeleton className="h-2.5 w-8" />
      </div>
    </div>
  );
}


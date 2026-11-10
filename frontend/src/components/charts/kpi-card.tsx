import * as React from "react";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { formatMetricValue, safeNumber } from "./chart-options-builder";
import type { FormatterToken, TabularDataPayload } from "./chart-types";

interface KpiCardProps {
  title: string;
  dataPayload?: TabularDataPayload | null;
  metricColumn?: string;
  dimensionColumn?: string;
  formatter?: FormatterToken;
  target?: number;
  comparisonLabel?: string;
  className?: string;
}

export function KpiCard({
  title,
  dataPayload,
  metricColumn,
  formatter,
  target,
  comparisonLabel = "vs target",
  className,
}: KpiCardProps) {
  const { primaryValue, deltaPercent, isPositive, isNeutral, sparklineSvg } = React.useMemo(() => {
    const rows = dataPayload?.data || [];
    let pVal: number | null = null;
    const history: number[] = [];

    if (rows.length > 0) {
      const colKey =
        metricColumn ||
        (dataPayload?.columns &&
          dataPayload.columns.find((c) => c !== "id" && c !== "name" && c !== "category")) ||
        Object.keys(rows[0])[0];

      pVal = safeNumber(rows[rows.length - 1][colKey]);

      rows.forEach((r) => {
        const num = safeNumber(r[colKey]);
        if (num !== null) {
          history.push(num);
        }
      });
    }

    let delta: number | null = null;
    let positive = false;
    let neutral = true;

    if (pVal !== null && target !== undefined && target !== null && target !== 0) {
      delta = ((pVal - target) / Math.abs(target)) * 100;
      positive = delta > 0;
      neutral = delta === 0;
    } else if (pVal !== null && history.length > 1) {
      const prev = history[0];
      if (prev !== 0) {
        delta = ((pVal - prev) / Math.abs(prev)) * 100;
        positive = delta > 0;
        neutral = delta === 0;
      }
    }

    let svgPath: string | null = null;
    if (history.length >= 2) {
      const min = Math.min(...history);
      const max = Math.max(...history);
      const range = max - min || 1;
      const width = 120;
      const height = 36;
      const pad = 4;

      const points = history.map((val, idx) => {
        const x = pad + (idx / (history.length - 1)) * (width - 2 * pad);
        const y = height - pad - ((val - min) / range) * (height - 2 * pad);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      });

      svgPath = points.join(" ");
    }

    return {
      primaryValue: pVal,
      deltaPercent: delta,
      isPositive: positive,
      isNeutral: neutral,
      sparklineSvg: svgPath,
    };
  }, [dataPayload, metricColumn, target]);

  return (
    <Card className={cn("overflow-hidden border border-[var(--border)] bg-[var(--surface)]", className)}>
      <CardContent className="p-5 flex flex-col justify-between h-full">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-[var(--muted-foreground)] uppercase tracking-wider">
            {title}
          </span>
          {deltaPercent !== null && (
            <div
              className={cn(
                "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold",
                isNeutral
                  ? "bg-[var(--muted)] text-[var(--muted-foreground)]"
                  : isPositive
                  ? "bg-[var(--success-subtle,#F0FDF4)] text-[var(--success,#15803D)]"
                  : "bg-[var(--destructive-subtle,#FEF2F2)] text-[var(--destructive,#B91C1C)]"
              )}
            >
              {isNeutral ? (
                <Minus className="w-3 h-3" />
              ) : isPositive ? (
                <TrendingUp className="w-3 h-3" />
              ) : (
                <TrendingDown className="w-3 h-3" />
              )}
              <span>{Math.abs(deltaPercent).toFixed(1)}%</span>
            </div>
          )}
        </div>

        <div className="mt-3 flex items-baseline justify-between gap-4">
          <div>
            <div className="text-2xl sm:text-3xl font-bold tracking-tight text-[var(--foreground)]">
              {formatMetricValue(primaryValue, formatter)}
            </div>
            {target !== undefined && target !== null && (
              <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                {comparisonLabel} ({formatMetricValue(target, formatter)})
              </p>
            )}
          </div>

          {sparklineSvg && (
            <div className="w-28 h-9 shrink-0">
              <svg className="w-full h-full overflow-visible" viewBox="0 0 120 36">
                <polyline
                  fill="none"
                  stroke={isPositive ? "var(--success, #15803D)" : "var(--primary, #635BFF)"}
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  points={sparklineSvg}
                />
              </svg>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

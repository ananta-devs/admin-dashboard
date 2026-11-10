"use client";

import * as React from "react";
import { AlertCircle } from "lucide-react";
import { formatCellRef, parseCellRef } from "@/lib/spreadsheet-types";

interface FormulaBarProps {
  activeRow: number;
  activeCol: number;
  value: string;
  computedValue?: string | number | boolean | null;
  errorCode?: string | null;
  onChange: (val: string) => void;
  onCommit: () => void;
  onCancel: () => void;
  onSelectCell: (row: number, col: number) => void;
  disabled?: boolean;
}

export function FormulaBar({
  activeRow,
  activeCol,
  value,
  computedValue,
  errorCode,
  onChange,
  onCommit,
  onCancel,
  onSelectCell,
  disabled = false,
}: FormulaBarProps) {
  const cellRefStr = formatCellRef(activeRow, activeCol);
  const [nameBoxInput, setNameBoxInput] = React.useState<string | null>(null);
  const [isEditingName, setIsEditingName] = React.useState(false);

  const displayedNameBox = isEditingName && nameBoxInput !== null ? nameBoxInput : cellRefStr;

  const handleNameBoxKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const coord = parseCellRef(displayedNameBox);
      onSelectCell(coord.row, coord.col);
      setIsEditingName(false);
      setNameBoxInput(null);
    } else if (e.key === "Escape") {
      setIsEditingName(false);
      setNameBoxInput(null);
    }
  };

  const handleInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      onCommit();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onCancel();
    }
  };

  return (
    <div
      role="region"
      aria-label="Formula bar"
      className="flex items-center gap-1.5 px-3 py-1.5 bg-[var(--surface)] border-b border-[var(--border)] text-xs select-none"
    >
      {/* Name Box (Active Cell Reference) */}
      <div className="relative flex items-center">
        <input
          type="text"
          aria-label="Active cell address"
          value={displayedNameBox}
          onFocus={() => {
            setIsEditingName(true);
            setNameBoxInput(cellRefStr);
          }}
          onBlur={() => {
            setIsEditingName(false);
            setNameBoxInput(null);
          }}
          onChange={(e) => setNameBoxInput(e.target.value.toUpperCase())}
          onKeyDown={handleNameBoxKeyDown}
          disabled={disabled}
          className="w-16 h-7 px-2 font-mono font-medium text-center text-xs bg-[var(--background)] border border-[var(--border)] rounded text-[var(--foreground)] focus:outline-none focus:ring-1 focus:ring-[var(--ring)] transition-colors"
        />
      </div>

      {/* Function FX Glyph */}
      <div
        className="flex items-center justify-center w-7 h-7 text-[var(--muted-foreground)] font-mono font-bold italic"
        title="Formula input"
        aria-hidden="true"
      >
        <span className="text-[13px] tracking-tighter">fx</span>
      </div>

      {/* Formula & Raw Value Input */}
      <div className="relative flex-1 flex items-center">
        <input
          type="text"
          aria-label="Cell formula or value"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleInputKeyDown}
          placeholder="Enter a value or formula (=SUM, =AVERAGE, =IF...)"
          disabled={disabled}
          className="w-full h-7 px-2.5 font-mono text-xs bg-[var(--background)] border border-[var(--border)] rounded text-[var(--foreground)] placeholder:text-[var(--muted-foreground)] focus:outline-none focus:ring-1 focus:ring-[var(--ring)] transition-colors"
        />
        {errorCode && (
          <div
            className="absolute right-2 flex items-center gap-1 text-[var(--danger,#ef4444)] text-[11px] font-mono font-semibold"
            title={`Formula evaluation error: ${errorCode}`}
          >
            <AlertCircle className="w-3.5 h-3.5" />
            <span>{errorCode}</span>
          </div>
        )}
      </div>

      {/* Computed Value Preview (when cell contains a formula) */}
      {value.startsWith("=") && computedValue !== undefined && !errorCode && (
        <div
          className="hidden sm:flex items-center px-2 h-7 bg-[var(--surface-raised)] border border-[var(--border)] rounded font-mono text-[11px] text-[var(--muted-foreground)]"
          title="Computed formula result"
        >
          <span className="mr-1 opacity-70">Result:</span>
          <span className="font-semibold text-[var(--foreground)]">
            {computedValue !== null ? String(computedValue) : "—"}
          </span>
        </div>
      )}
    </div>
  );
}

"use client";

import * as React from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  CellData,
  CellCoord,
  formatCellRef,
  colToLetter,
} from "@/lib/spreadsheet-types";

interface VirtualGridProps {
  rowCount: number;
  columnCount: number;
  cells: Record<string, CellData>;
  activeCell: CellCoord;
  isEditing: boolean;
  editValue: string;
  onSelectCell: (coord: CellCoord) => void;
  onStartEdit: (initialChar?: string) => void;
  onEditChange: (val: string) => void;
  onCommitEdit: () => void;
  onCancelEdit: () => void;
  onBatchUpdate: (updates: { cell_ref: string; value: string | number | null }[]) => void;
  onUndo: () => void;
  onRedo: () => void;
  onVisibleRangeChange?: (startRow: number, endRow: number) => void;
}

const ROW_HEIGHT = 26;
const HEADER_HEIGHT = 26;
const ROW_HEADER_WIDTH = 48;
const COL_WIDTH = 92;

export function VirtualGrid({
  rowCount,
  columnCount,
  cells,
  activeCell,
  isEditing,
  editValue,
  onSelectCell,
  onStartEdit,
  onEditChange,
  onCommitEdit,
  onCancelEdit,
  onBatchUpdate,
  onUndo,
  onRedo,
  onVisibleRangeChange,
}: VirtualGridProps) {
  const parentRef = React.useRef<HTMLDivElement>(null);
  const inlineInputRef = React.useRef<HTMLInputElement>(null);

  // TanStack Virtualizer for 50,000 rows
  // eslint-disable-next-line react-hooks/incompatible-library
  const rowVirtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 20,
  });

  const virtualRows = rowVirtualizer.getVirtualItems();

  // Notify parent of visible row range to trigger windowed fetching
  React.useEffect(() => {
    if (virtualRows.length > 0 && onVisibleRangeChange) {
      const start = virtualRows[0].index;
      const end = virtualRows[virtualRows.length - 1].index;
      onVisibleRangeChange(start, end);
    }
  }, [virtualRows, onVisibleRangeChange]);

  // Focus inline input when entering edit mode
  React.useEffect(() => {
    if (isEditing && inlineInputRef.current) {
      inlineInputRef.current.focus();
    }
  }, [isEditing]);

  // Ensure active cell is scrolled into view when moving via keyboard
  React.useEffect(() => {
    if (!parentRef.current) return;
    const scrollElem = parentRef.current;
    const targetTop = activeCell.row * ROW_HEIGHT + HEADER_HEIGHT;
    const targetBottom = targetTop + ROW_HEIGHT;
    const targetLeft = activeCell.col * COL_WIDTH + ROW_HEADER_WIDTH;
    const targetRight = targetLeft + COL_WIDTH;

    if (targetTop < scrollElem.scrollTop + HEADER_HEIGHT) {
      scrollElem.scrollTop = targetTop - HEADER_HEIGHT;
    } else if (targetBottom > scrollElem.scrollTop + scrollElem.clientHeight) {
      scrollElem.scrollTop = targetBottom - scrollElem.clientHeight;
    }

    if (targetLeft < scrollElem.scrollLeft + ROW_HEADER_WIDTH) {
      scrollElem.scrollLeft = targetLeft - ROW_HEADER_WIDTH;
    } else if (targetRight > scrollElem.scrollLeft + scrollElem.clientWidth) {
      scrollElem.scrollLeft = targetRight - scrollElem.clientWidth;
    }
  }, [activeCell]);

  // Keyboard navigation & clipboard shortcuts
  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Undo / Redo
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
      e.preventDefault();
      if (e.shiftKey) {
        onRedo();
      } else {
        onUndo();
      }
      return;
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") {
      e.preventDefault();
      onRedo();
      return;
    }

    // Copy active cell
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c" && !isEditing) {
      e.preventDefault();
      const ref = formatCellRef(activeCell.row, activeCell.col);
      const cell = cells[ref];
      const copyVal = cell?.formula || (cell?.raw !== null && cell?.raw !== undefined ? String(cell.raw) : "");
      navigator.clipboard.writeText(copyVal);
      return;
    }

    // Paste from clipboard into active cell / range
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "v" && !isEditing) {
      e.preventDefault();
      navigator.clipboard.readText().then((text) => {
        if (!text) return;
        const lines = text.split(/\r?\n/).filter((l) => l.length > 0);
        const updates: { cell_ref: string; value: string | number | null }[] = [];

        lines.forEach((line, rOffset) => {
          const cols = line.split("\t");
          cols.forEach((colVal, cOffset) => {
            const r = activeCell.row + rOffset;
            const c = activeCell.col + cOffset;
            if (r < rowCount && c < columnCount) {
              const cellRef = formatCellRef(r, c);
              let val: string | number | null = colVal.trim();
              if (val === "") {
                val = null;
              } else if (!val.startsWith("=") && !isNaN(Number(val))) {
                val = Number(val);
              }
              updates.push({ cell_ref: cellRef, value: val });
            }
          });
        });

        if (updates.length > 0) {
          onBatchUpdate(updates);
        }
      });
      return;
    }

    if (isEditing) {
      if (e.key === "Enter") {
        e.preventDefault();
        onCommitEdit();
        onSelectCell({
          row: Math.min(rowCount - 1, activeCell.row + (e.shiftKey ? -1 : 1)),
          col: activeCell.col,
        });
      } else if (e.key === "Tab") {
        e.preventDefault();
        onCommitEdit();
        onSelectCell({
          row: activeCell.row,
          col: Math.min(columnCount - 1, Math.max(0, activeCell.col + (e.shiftKey ? -1 : 1))),
        });
      } else if (e.key === "Escape") {
        e.preventDefault();
        onCancelEdit();
      }
      return;
    }

    // Non-editing navigation
    if (e.key === "ArrowUp") {
      e.preventDefault();
      onSelectCell({ row: Math.max(0, activeCell.row - 1), col: activeCell.col });
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      onSelectCell({ row: Math.min(rowCount - 1, activeCell.row + 1), col: activeCell.col });
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      onSelectCell({ row: activeCell.row, col: Math.max(0, activeCell.col - 1) });
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      onSelectCell({ row: activeCell.row, col: Math.min(columnCount - 1, activeCell.col + 1) });
    } else if (e.key === "Enter") {
      e.preventDefault();
      onStartEdit();
    } else if (e.key === "F2") {
      e.preventDefault();
      onStartEdit();
    } else if (e.key === "Tab") {
      e.preventDefault();
      onSelectCell({
        row: activeCell.row,
        col: Math.min(columnCount - 1, Math.max(0, activeCell.col + (e.shiftKey ? -1 : 1))),
      });
    } else if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      const ref = formatCellRef(activeCell.row, activeCell.col);
      onBatchUpdate([{ cell_ref: ref, value: null }]);
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      // Direct typing initiates editing
      onStartEdit(e.key);
    }
  };

  const columns = Array.from({ length: columnCount }, (_, i) => ({
    index: i,
    label: colToLetter(i),
  }));

  const totalGridWidth = ROW_HEADER_WIDTH + columnCount * COL_WIDTH;

  return (
    <div
      ref={parentRef}
      tabIndex={0}
      role="grid"
      aria-label="Spreadsheet Grid"
      aria-rowcount={rowCount}
      aria-colcount={columnCount}
      onKeyDown={handleKeyDown}
      className="relative flex-1 w-full h-full overflow-auto bg-[var(--background)] font-mono text-xs select-none focus:outline-none"
    >
      <div
        style={{
          height: `${rowVirtualizer.getTotalSize() + HEADER_HEIGHT}px`,
          width: `${totalGridWidth}px`,
          position: "relative",
        }}
      >
        {/* Sticky Corner Header */}
        <div
          style={{
            position: "sticky",
            top: 0,
            left: 0,
            zIndex: 30,
            width: `${ROW_HEADER_WIDTH}px`,
            height: `${HEADER_HEIGHT}px`,
          }}
          className="bg-[var(--surface-raised)] border-b border-r border-[var(--border)] flex items-center justify-center text-[var(--muted-foreground)] text-[10px] font-sans font-bold"
        >
          #
        </div>

        {/* Sticky Column Headers (A, B, C, ...) */}
        <div
          role="row"
          style={{
            position: "sticky",
            top: 0,
            left: `${ROW_HEADER_WIDTH}px`,
            zIndex: 20,
            height: `${HEADER_HEIGHT}px`,
            width: `${columnCount * COL_WIDTH}px`,
            display: "flex",
          }}
          className="bg-[var(--surface)] border-b border-[var(--border)]"
        >
          {columns.map((col) => {
            const isColActive = activeCell.col === col.index;
            return (
              <div
                key={col.index}
                role="columnheader"
                aria-colindex={col.index + 1}
                style={{ width: `${COL_WIDTH}px` }}
                className={`h-full flex items-center justify-center border-r border-[var(--border)] text-[11px] font-semibold transition-colors ${
                  isColActive
                    ? "bg-[var(--surface-raised)] text-[var(--foreground)] font-bold"
                    : "text-[var(--muted-foreground)]"
                }`}
              >
                {col.label}
              </div>
            );
          })}
        </div>

        {/* Virtualized Rows */}
        {virtualRows.map((virtualRow) => {
          const rowIndex = virtualRow.index;
          const isRowActive = activeCell.row === rowIndex;

          return (
            <div
              key={rowIndex}
              role="row"
              aria-rowindex={rowIndex + 1}
              style={{
                position: "absolute",
                top: `${virtualRow.start + HEADER_HEIGHT}px`,
                left: 0,
                height: `${virtualRow.size}px`,
                width: `${totalGridWidth}px`,
                display: "flex",
              }}
            >
              {/* Sticky Row Number Header (1, 2, 3...) */}
              <div
                role="rowheader"
                style={{
                  position: "sticky",
                  left: 0,
                  zIndex: 10,
                  width: `${ROW_HEADER_WIDTH}px`,
                }}
                className={`h-full flex items-center justify-center border-b border-r border-[var(--border)] text-[10px] font-medium transition-colors ${
                  isRowActive
                    ? "bg-[var(--surface-raised)] text-[var(--foreground)] font-bold"
                    : "bg-[var(--surface)] text-[var(--muted-foreground)]"
                }`}
              >
                {rowIndex + 1}
              </div>

              {/* Row Cells */}
              {columns.map((col) => {
                const colIndex = col.index;
                const cellRef = formatCellRef(rowIndex, colIndex);
                const cellData = cells[cellRef];
                const isSelected = activeCell.row === rowIndex && activeCell.col === colIndex;
                const hasError = !!cellData?.error_code;

                // Render value: display computed if present, else raw
                let displayVal = "";
                if (cellData) {
                  if (hasError) {
                    displayVal = cellData.error_code || "#ERROR!";
                  } else if (cellData.computed !== null && cellData.computed !== undefined) {
                    displayVal = String(cellData.computed);
                  } else if (cellData.raw !== null && cellData.raw !== undefined) {
                    displayVal = String(cellData.raw);
                  }
                }

                const isNumber = cellData?.type === "number" || (!hasError && !isNaN(Number(displayVal)) && displayVal.trim() !== "");

                return (
                  <div
                    key={colIndex}
                    role="gridcell"
                    aria-rowindex={rowIndex + 1}
                    aria-colindex={colIndex + 1}
                    aria-selected={isSelected}
                    data-cell-ref={cellRef}
                    onClick={() => {
                      if (!isSelected) {
                        if (isEditing) onCommitEdit();
                        onSelectCell({ row: rowIndex, col: colIndex });
                      }
                    }}
                    onDoubleClick={() => {
                      onSelectCell({ row: rowIndex, col: colIndex });
                      onStartEdit();
                    }}
                    style={{
                      width: `${COL_WIDTH}px`,
                    }}
                    className={`relative h-full px-2 flex items-center border-b border-r border-[var(--border)] overflow-hidden cursor-default transition-colors ${
                      isSelected
                        ? "outline-2 outline-[var(--primary)] -outline-offset-2 z-10 bg-[var(--surface-raised)]/40"
                        : "hover:bg-[var(--surface-raised)]/20"
                    } ${
                      hasError
                        ? "text-[var(--danger,#ef4444)] font-semibold bg-red-500/10"
                        : "text-[var(--foreground)]"
                    }`}
                  >
                    {isSelected && isEditing ? (
                      <input
                        ref={inlineInputRef}
                        type="text"
                        value={editValue}
                        onChange={(e) => onEditChange(e.target.value)}
                        onBlur={onCommitEdit}
                        className="w-full h-full bg-[var(--background)] px-1 font-mono text-xs text-[var(--foreground)] focus:outline-none"
                      />
                    ) : (
                      <span
                        className={`w-full truncate ${
                          isNumber ? "text-right" : "text-left"
                        }`}
                        title={cellData?.formula ? `${cellRef}: ${cellData.formula} => ${displayVal}` : displayVal}
                      >
                        {displayVal}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}


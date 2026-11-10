"use client";

import * as React from "react";
import { Plus, Edit2, FileSpreadsheet } from "lucide-react";
import { SheetRead } from "@/lib/spreadsheet-types";

interface SheetTabsProps {
  sheets: SheetRead[];
  activeSheetId: string | null;
  onSelectSheet: (sheetId: string) => void;
  onAddSheet: () => void;
  onRenameSheet: (sheetId: string, newName: string) => void;
  disabled?: boolean;
}

export function SheetTabs({
  sheets,
  activeSheetId,
  onSelectSheet,
  onAddSheet,
  onRenameSheet,
  disabled = false,
}: SheetTabsProps) {
  const [editingSheetId, setEditingSheetId] = React.useState<string | null>(null);
  const [editingName, setEditingName] = React.useState("");

  const handleStartRename = (sheet: SheetRead) => {
    if (disabled) return;
    setEditingSheetId(sheet.id);
    setEditingName(sheet.name);
  };

  const handleCommitRename = (sheetId: string) => {
    if (editingName.trim()) {
      onRenameSheet(sheetId, editingName.trim());
    }
    setEditingSheetId(null);
  };

  return (
    <div
      role="tablist"
      aria-label="Workbook sheets"
      className="flex items-center gap-1 px-2 py-1 bg-[var(--surface)] border-t border-[var(--border)] overflow-x-auto select-none scrollbar-thin text-xs"
    >
      {sheets.map((sheet) => {
        const isActive = sheet.id === activeSheetId;
        const isEditing = sheet.id === editingSheetId;

        return (
          <div
            key={sheet.id}
            role="tab"
            aria-selected={isActive}
            aria-controls={`sheet-panel-${sheet.id}`}
            id={`sheet-tab-${sheet.id}`}
            tabIndex={isActive ? 0 : -1}
            onClick={() => !isEditing && onSelectSheet(sheet.id)}
            onDoubleClick={() => handleStartRename(sheet)}
            className={`group relative flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium cursor-pointer transition-colors ${
              isActive
                ? "bg-[var(--surface-raised)] text-[var(--foreground)] border border-[var(--border)] shadow-xs"
                : "text-[var(--muted-foreground)] hover:text-[var(--foreground)] hover:bg-[var(--surface-raised)]/60"
            }`}
          >
            <FileSpreadsheet className="w-3.5 h-3.5 opacity-70 shrink-0" />

            {isEditing ? (
              <input
                type="text"
                autoFocus
                value={editingName}
                onChange={(e) => setEditingName(e.target.value)}
                onBlur={() => handleCommitRename(sheet.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleCommitRename(sheet.id);
                  if (e.key === "Escape") setEditingSheetId(null);
                }}
                className="h-5 px-1 font-sans text-xs bg-[var(--background)] border border-[var(--border)] rounded text-[var(--foreground)] focus:outline-none focus:ring-1 focus:ring-[var(--ring)]"
              />
            ) : (
              <span className="truncate max-w-[120px]">{sheet.name}</span>
            )}

            {!isEditing && isActive && !disabled && (
              <button
                type="button"
                aria-label={`Rename ${sheet.name}`}
                onClick={(e) => {
                  e.stopPropagation();
                  handleStartRename(sheet);
                }}
                className="opacity-0 group-hover:opacity-100 p-0.5 text-[var(--muted-foreground)] hover:text-[var(--foreground)] rounded transition-opacity"
              >
                <Edit2 className="w-2.5 h-2.5" />
              </button>
            )}
          </div>
        );
      })}

      {/* Add Sheet Button */}
      <button
        type="button"
        aria-label="Add new sheet"
        onClick={onAddSheet}
        disabled={disabled}
        className="flex items-center justify-center w-6 h-6 ml-1 text-[var(--muted-foreground)] hover:text-[var(--foreground)] hover:bg-[var(--surface-raised)] rounded transition-colors disabled:opacity-50"
      >
        <Plus className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}


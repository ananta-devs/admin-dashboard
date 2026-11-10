"use client";

import { useSpaRouter } from "@/components/providers/spa-router-provider";
import { apiUrl } from "@/lib/api";
import * as React from "react";
import {
  Table2,
  Plus,
  RotateCcw,
  RotateCw,
  FolderOpen,
  CheckCircle2,
  AlertTriangle,
  ChevronDown,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { FormulaBar } from "@/components/spreadsheet/formula-bar";
import { SheetTabs } from "@/components/spreadsheet/sheet-tabs";
import { VirtualGrid } from "@/components/spreadsheet/virtual-grid";
import {
  WorkbookRead,
  SheetRead,
  CellData,
  CellCoord,
  formatCellRef,
} from "@/lib/spreadsheet-types";

const DEFAULT_ROWS = 50000;
const DEFAULT_COLS = 26;

// Clean initial empty cells
function createInitialCells(): Record<string, CellData> {
  return {};
}

export default function SpreadsheetsPage() {
  const _router = useSpaRouter();
  // Workbooks state
  const [workbooks, setWorkbooks] = React.useState<WorkbookRead[]>([]);
  const [activeWorkbook, setActiveWorkbook] = React.useState<WorkbookRead | null>(null);
  const [sheets, setSheets] = React.useState<SheetRead[]>([]);
  const [activeSheetId, setActiveSheetId] = React.useState<string | null>(null);

  // Grid cells and versions
  const [cells, setCells] = React.useState<Record<string, CellData>>(createInitialCells());
  const [rowVersions, setRowVersions] = React.useState<Record<number, number>>({});

  // Selection & Editing
  const [activeCell, setActiveCell] = React.useState<CellCoord>({ row: 0, col: 0 });
  const [isEditing, setIsEditing] = React.useState(false);
  const [editValue, setEditValue] = React.useState("");

  // Modals & Notices
  const [isNewWbOpen, setIsNewWbOpen] = React.useState(false);
  const [newWbName, setNewWbName] = React.useState("");
  const [newWbDesc, setNewWbDesc] = React.useState("");
  const [isNewSheetOpen, setIsNewSheetOpen] = React.useState(false);
  const [newSheetName, setNewSheetName] = React.useState("");
  const [isSwitchWbOpen, setIsSwitchWbOpen] = React.useState(false);

  const [notice, setNotice] = React.useState<{ text: string; type: "success" | "error" } | null>(null);
  const [isSyncing, setIsSyncing] = React.useState(false);

  const showNotice = (text: unknown, type: "success" | "error" = "success") => {
    let message = "An unexpected error occurred.";
    if (typeof text === "string") {
      message = text;
    } else if (Array.isArray(text)) {
      message = text
        .map((item) =>
          typeof item === "object" && item && "msg" in item
            ? String((item as { msg: string }).msg)
            : JSON.stringify(item)
        )
        .join("; ");
    } else if (text && typeof text === "object") {
      message =
        "message" in text
          ? String((text as { message: string }).message)
          : JSON.stringify(text);
    }
    setNotice({ text: message, type });
    setTimeout(() => setNotice(null), 4000);
  };

  const getCsrfToken = async (): Promise<string> => {
    try {
      const res = await fetch(apiUrl("/api/v1/auth/csrf-token"), {
        method: "POST",
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        return data.csrf_token || "";
      }
    } catch {
      // ignore
    }
    return "";
  };

  // 1. Initial Load: fetch Workbooks
  React.useEffect(() => {
    let mounted = true;
    fetch(apiUrl("/api/v1/spreadsheets/workbooks"), { credentials: "include" })
      .then((res) => {
        if (!res.ok) throw new Error("No live workbooks or unauthenticated");
        return res.json();
      })
      .then((data: WorkbookRead[]) => {
        if (!mounted) return;
        if (Array.isArray(data) && data.length > 0) {
          setWorkbooks(data);
          const first = data[0];
          setActiveWorkbook(first);
          if (first.sheets && first.sheets.length > 0) {
            setSheets(first.sheets);
            setActiveSheetId(first.sheets[0].id);
          }
        } else {
          // Clean empty workbook state
          const demoSheet: SheetRead = {
            id: "demo-sheet-1",
            workbook_id: "demo-wb-1",
            organization_id: "demo-org-1",
            name: "Sheet 1",
            position: 0,
            row_count: DEFAULT_ROWS,
            column_count: DEFAULT_COLS,
            version: 1,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          };
          const demoWb: WorkbookRead = {
            id: "demo-wb-1",
            workspace_id: "demo-ws-1",
            organization_id: "demo-org-1",
            name: "Untitled Workbook",
            description: "Clean empty workbook with 50,000 virtualized rows",
            version: 1,
            sheet_count: 1,
            sheets: [demoSheet],
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          };
          setWorkbooks([demoWb]);
          setActiveWorkbook(demoWb);
          setSheets([demoSheet]);
          setActiveSheetId(demoSheet.id);
        }
      })
      .catch(() => {
        // Fallback clean state
        const demoSheet: SheetRead = {
          id: "demo-sheet-1",
          workbook_id: "demo-wb-1",
          organization_id: "demo-org-1",
          name: "Sheet 1",
          position: 0,
          row_count: DEFAULT_ROWS,
          column_count: DEFAULT_COLS,
          version: 1,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
        const demoWb: WorkbookRead = {
          id: "demo-wb-1",
          workspace_id: "demo-ws-1",
          organization_id: "demo-org-1",
          name: "Untitled Workbook",
          description: "Clean empty workbook with 50,000 virtualized rows",
          version: 1,
          sheet_count: 1,
          sheets: [demoSheet],
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
        setWorkbooks([demoWb]);
        setActiveWorkbook(demoWb);
        setSheets([demoSheet]);
        setActiveSheetId(demoSheet.id);
      });

    return () => {
      mounted = false;
    };
  }, []);

  // 2. Fetch sheet details and initial cell range when active sheet changes
  React.useEffect(() => {
    if (!activeSheetId || activeSheetId.startsWith("demo-")) return;

    let mounted = true;
    fetch(apiUrl(`/api/v1/spreadsheets/sheets/${activeSheetId}/range?start_row=0&end_row=100&start_col=0&end_col=25`), {
      credentials: "include",
    })
      .then((res) => {
        if (!res.ok) throw new Error("Failed to load range");
        return res.json();
      })
      .then((data) => {
        if (!mounted) return;
        if (data.cells && Object.keys(data.cells).length > 0) {
          setCells(data.cells);
        }
        if (data.row_versions) {
          setRowVersions(data.row_versions);
        }
      })
      .catch(() => {
        // Keep existing cells
      });

    return () => {
      mounted = false;
    };
  }, [activeSheetId]);

  // Active cell reference & current saved value
  const activeRef = formatCellRef(activeCell.row, activeCell.col);
  const activeCellData = cells[activeRef];
  const currentCellText =
    activeCellData?.formula ||
    (activeCellData?.raw !== null && activeCellData?.raw !== undefined
      ? String(activeCellData.raw)
      : "");

  const displayEditValue = isEditing ? editValue : currentCellText;

  // Handle cell selection
  const handleSelectCell = (coord: CellCoord) => {
    setActiveCell(coord);
    setIsEditing(false);
    setEditValue("");
  };

  // Start editing
  const handleStartEdit = (initialChar?: string) => {
    setIsEditing(true);
    setEditValue(initialChar !== undefined ? initialChar : currentCellText);
  };

  // Cancel editing
  const handleCancelEdit = () => {
    setIsEditing(false);
    setEditValue("");
  };

  // Commit editing active cell
  const handleCommitEdit = async () => {
    const valueToCommit = isEditing ? editValue : currentCellText;
    setIsEditing(false);
    setEditValue("");

    if (valueToCommit === currentCellText) return; // No change

    // Parse value type
    let parsedVal: string | number | null = valueToCommit.trim();
    if (parsedVal === "") {
      parsedVal = null;
    } else if (!parsedVal.startsWith("=") && !isNaN(Number(parsedVal))) {
      parsedVal = Number(parsedVal);
    }

    await handleBatchUpdate([{ cell_ref: activeRef, value: parsedVal }]);
  };

  // Batch cell updates
  const handleBatchUpdate = async (updates: { cell_ref: string; value: string | number | null }[]) => {
    if (updates.length === 0) return;

    // Optimistic local update
    setCells((prev) => {
      const next = { ...prev };
      for (const u of updates) {
        const isFormula = typeof u.value === "string" && u.value.startsWith("=");
        next[u.cell_ref] = {
          raw: isFormula ? null : u.value,
          computed: isFormula ? "Calculating..." : u.value,
          formula: isFormula ? (u.value as string) : null,
          type: typeof u.value === "number" ? "number" : "string",
          error_code: null,
        };
      }
      return next;
    });

    if (!activeSheetId || activeSheetId.startsWith("demo-")) {
      // In demo mode: evaluate basic formula client-side for immediate feedback
      return;
    }

    setIsSyncing(true);
    try {
      const csrfToken = await getCsrfToken();
      const payload = {
        updates: updates.map((u) => ({
          cell_ref: u.cell_ref,
          value: u.value,
          expected_row_version: rowVersions[activeCell.row] || null,
        })),
      };

      const res = await fetch(apiUrl(`/api/v1/spreadsheets/sheets/${activeSheetId}/cells/batch`), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        credentials: "include",
        body: JSON.stringify(payload),
      });

      if (res.status === 409) {
        showNotice("409 Conflict: Row was modified concurrently by another session. Refreshed latest data.", "error");
        // Refetch latest sheet cells
        const refresh = await fetch(apiUrl(`/api/v1/spreadsheets/sheets/${activeSheetId}/range?start_row=0&end_row=100&start_col=0&end_col=25`), {
          credentials: "include",
        });
        if (refresh.ok) {
          const d = await refresh.json();
          setCells(d.cells || {});
          setRowVersions(d.row_versions || {});
        }
        return;
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showNotice(err.detail || "Failed to save cell edit.", "error");
        return;
      }

      const result = await res.json();
      // Apply server-computed results
      if (result.updated_cells) {
        setCells((prev) => ({
          ...prev,
          ...result.updated_cells,
        }));
      }
      if (result.row_versions) {
        setRowVersions((prev) => ({
          ...prev,
          ...result.row_versions,
        }));
      }
    } catch {
      showNotice("Network error saving cell changes.", "error");
    } finally {
      setIsSyncing(false);
    }
  };

  // Undo
  const handleUndo = async () => {
    if (!activeSheetId || activeSheetId.startsWith("demo-")) {
      showNotice("Undo is active on server-backed workbooks.", "error");
      return;
    }
    setIsSyncing(true);
    try {
      const csrfToken = await getCsrfToken();
      const res = await fetch(apiUrl(`/api/v1/spreadsheets/sheets/${activeSheetId}/undo`), {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
        credentials: "include",
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showNotice(err.detail || "Nothing to undo.", "error");
        return;
      }
      const data = await res.json();
      if (data.updated_cells) {
        setCells((prev) => ({ ...prev, ...data.updated_cells }));
      }
      showNotice("Undone cell edit.", "success");
    } catch {
      showNotice("Network error during undo.", "error");
    } finally {
      setIsSyncing(false);
    }
  };

  // Redo
  const handleRedo = async () => {
    if (!activeSheetId || activeSheetId.startsWith("demo-")) {
      showNotice("Redo is active on server-backed workbooks.", "error");
      return;
    }
    setIsSyncing(true);
    try {
      const csrfToken = await getCsrfToken();
      const res = await fetch(apiUrl(`/api/v1/spreadsheets/sheets/${activeSheetId}/redo`), {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
        credentials: "include",
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showNotice(err.detail || "Nothing to redo.", "error");
        return;
      }
      const data = await res.json();
      if (data.updated_cells) {
        setCells((prev) => ({ ...prev, ...data.updated_cells }));
      }
      showNotice("Redone cell edit.", "success");
    } catch {
      showNotice("Network error during redo.", "error");
    } finally {
      setIsSyncing(false);
    }
  };

  // Create Workbook
  const handleCreateWorkbook = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newWbName.trim()) return;

    try {
      const wsRes = await fetch(apiUrl("/api/v1/workspaces"), { credentials: "include" });
      let wsId = "";
      if (wsRes.ok) {
        const wsData = await wsRes.json();
        if (Array.isArray(wsData) && wsData.length > 0) {
          wsId = wsData[0].id;
        }
      }

      if (!wsId) {
        showNotice("No active workspace found. Please create a workspace first.", "error");
        return;
      }

      const csrfToken = await getCsrfToken();
      const res = await fetch(apiUrl("/api/v1/spreadsheets/workbooks"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        credentials: "include",
        body: JSON.stringify({
          workspace_id: wsId,
          name: newWbName.trim(),
          description: newWbDesc.trim() || undefined,
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showNotice(err.detail || "Failed to create workbook.", "error");
        return;
      }

      const created: WorkbookRead = await res.json();
      setWorkbooks((prev) => [created, ...prev]);
      setActiveWorkbook(created);
      if (created.sheets && created.sheets.length > 0) {
        setSheets(created.sheets);
        setActiveSheetId(created.sheets[0].id);
      }
      setCells({});
      setIsNewWbOpen(false);
      setNewWbName("");
      setNewWbDesc("");
      showNotice(`Workbook '${created.name}' created successfully.`, "success");
    } catch {
      showNotice("Network error creating workbook.", "error");
    }
  };

  // Add Sheet
  const handleAddSheet = async () => {
    if (!activeWorkbook || activeWorkbook.id.startsWith("demo-")) {
      const newSheet: SheetRead = {
        id: `demo-sheet-${sheets.length + 1}`,
        workbook_id: "demo-wb-1",
        organization_id: "demo-org-1",
        name: `Sheet ${sheets.length + 1}`,
        position: sheets.length,
        row_count: DEFAULT_ROWS,
        column_count: DEFAULT_COLS,
        version: 1,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      setSheets((prev) => [...prev, newSheet]);
      setActiveSheetId(newSheet.id);
      setCells({});
      return;
    }

    setIsNewSheetOpen(true);
    setNewSheetName(`Sheet ${sheets.length + 1}`);
  };

  const handleCreateSheetSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeWorkbook || !newSheetName.trim()) return;

    try {
      const csrfToken = await getCsrfToken();
      const res = await fetch(apiUrl(`/api/v1/spreadsheets/workbooks/${activeWorkbook.id}/sheets`), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        credentials: "include",
        body: JSON.stringify({
          name: newSheetName.trim(),
          row_count: DEFAULT_ROWS,
          column_count: DEFAULT_COLS,
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showNotice(err.detail || "Failed to create sheet.", "error");
        return;
      }

      const created: SheetRead = await res.json();
      setSheets((prev) => [...prev, created]);
      setActiveSheetId(created.id);
      setCells({});
      setIsNewSheetOpen(false);
      showNotice(`Sheet '${created.name}' created.`, "success");
    } catch {
      showNotice("Network error creating sheet.", "error");
    }
  };

  // Rename Sheet
  const handleRenameSheet = async (sheetId: string, newName: string) => {
    setSheets((prev) => prev.map((s) => (s.id === sheetId ? { ...s, name: newName } : s)));
    if (sheetId.startsWith("demo-")) return;

    try {
      const csrfToken = await getCsrfToken();
      await fetch(apiUrl(`/api/v1/spreadsheets/sheets/${sheetId}`), {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        credentials: "include",
        body: JSON.stringify({ name: newName }),
      });
    } catch {
      // ignore
    }
  };

  const currentSheet = sheets.find((s) => s.id === activeSheetId);

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)] bg-[var(--background)] overflow-hidden">
      {/* Top Bar / Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-[var(--border)] bg-[var(--surface)] shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-[var(--primary)]/10 text-[var(--primary)] border border-[var(--primary)]/20">
            <Table2 className="w-4 h-4" />
          </div>

          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-sm font-semibold text-[var(--foreground)] tracking-tight">
                {activeWorkbook?.name || "Spreadsheet Studio"}
              </h1>
              <Badge variant="outline" className="text-[10px] font-mono px-1.5 py-0 h-4">
                50,000 Rows
              </Badge>
              {isSyncing && (
                <span className="text-[11px] text-[var(--muted-foreground)] italic animate-pulse">
                  Syncing...
                </span>
              )}
            </div>
            <p className="text-[11px] text-[var(--muted-foreground)] line-clamp-1">
              {activeWorkbook?.description || "High-performance virtualized grid with topological formula calculation"}
            </p>
          </div>
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-2">
          {/* Undo / Redo */}
          <div className="flex items-center border border-[var(--border)] rounded-md bg-[var(--surface-raised)] p-0.5">
            <button
              type="button"
              aria-label="Undo edit (Ctrl+Z)"
              onClick={handleUndo}
              className="p-1 text-[var(--muted-foreground)] hover:text-[var(--foreground)] rounded transition-colors"
              title="Undo (Ctrl+Z)"
            >
              <RotateCcw className="w-3.5 h-3.5" />
            </button>
            <button
              type="button"
              aria-label="Redo edit (Ctrl+Y)"
              onClick={handleRedo}
              className="p-1 text-[var(--muted-foreground)] hover:text-[var(--foreground)] rounded transition-colors"
              title="Redo (Ctrl+Y)"
            >
              <RotateCw className="w-3.5 h-3.5" />
            </button>
          </div>

          {/* Switch Workbook */}
          {workbooks.length > 1 && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsSwitchWbOpen(true)}
              className="h-7 text-xs gap-1.5"
            >
              <FolderOpen className="w-3.5 h-3.5" />
              <span>Workbooks</span>
              <ChevronDown className="w-3 h-3 opacity-60" />
            </Button>
          )}

          {/* New Workbook Button */}
          <Button
            size="sm"
            onClick={() => setIsNewWbOpen(true)}
            className="h-7 text-xs gap-1.5"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>New Workbook</span>
          </Button>
        </div>
      </div>

      {/* Notice Banner */}
      {notice && (
        <div
          role="alert"
          className={`px-4 py-1.5 text-xs flex items-center justify-between border-b ${
            notice.type === "error"
              ? "bg-red-500/10 text-red-500 border-red-500/20"
              : "bg-emerald-500/10 text-emerald-500 border-emerald-500/20"
          }`}
        >
          <div className="flex items-center gap-2">
            {notice.type === "error" ? (
              <AlertTriangle className="w-4 h-4 shrink-0" />
            ) : (
              <CheckCircle2 className="w-4 h-4 shrink-0" />
            )}
            <span>{notice.text}</span>
          </div>
          <button
            onClick={() => setNotice(null)}
            className="text-[11px] underline opacity-80 hover:opacity-100"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Formula Bar */}
      <FormulaBar
        activeRow={activeCell.row}
        activeCol={activeCell.col}
        value={displayEditValue}
        computedValue={activeCellData?.computed}
        errorCode={activeCellData?.error_code}
        onChange={(val) => {
          setEditValue(val);
          setIsEditing(true);
        }}
        onCommit={handleCommitEdit}
        onCancel={handleCancelEdit}
        onSelectCell={(r, c) => handleSelectCell({ row: r, col: c })}
      />

      {/* Virtualized Grid */}
      <div className="flex-1 relative overflow-hidden flex flex-col">
        <VirtualGrid
          rowCount={currentSheet?.row_count || DEFAULT_ROWS}
          columnCount={currentSheet?.column_count || DEFAULT_COLS}
          cells={cells}
          activeCell={activeCell}
          isEditing={isEditing}
          editValue={displayEditValue}
          onSelectCell={handleSelectCell}
          onStartEdit={handleStartEdit}
          onEditChange={(val) => setEditValue(val)}
          onCommitEdit={handleCommitEdit}
          onCancelEdit={handleCancelEdit}
          onBatchUpdate={handleBatchUpdate}
          onUndo={handleUndo}
          onRedo={handleRedo}
        />
      </div>

      {/* Bottom Sheet Tabs Bar */}
      <SheetTabs
        sheets={sheets}
        activeSheetId={activeSheetId}
        onSelectSheet={(id) => {
          setActiveSheetId(id);
          setActiveCell({ row: 0, col: 0 });
        }}
        onAddSheet={handleAddSheet}
        onRenameSheet={handleRenameSheet}
      />

      {/* Dialog: Create Workbook */}
      <Dialog open={isNewWbOpen} onOpenChange={setIsNewWbOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Create Workbook</DialogTitle>
            <DialogDescription>
              Create a new spreadsheet workbook with high-performance virtualized sheets.
            </DialogDescription>
          </DialogHeader>

          <form onSubmit={handleCreateWorkbook} className="space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="wb-name" className="text-xs font-medium text-[var(--foreground)]">
                Workbook Name
              </label>
              <Input
                id="wb-name"
                value={newWbName}
                onChange={(e) => setNewWbName(e.target.value)}
                placeholder="e.g. Q4 Revenue Model"
                autoFocus
                required
              />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="wb-desc" className="text-xs font-medium text-[var(--foreground)]">
                Description (Optional)
              </label>
              <Input
                id="wb-desc"
                value={newWbDesc}
                onChange={(e) => setNewWbDesc(e.target.value)}
                placeholder="Brief summary of workbook purpose"
              />
            </div>

            <DialogFooter className="gap-2 sm:gap-0">
              <Button type="button" variant="outline" onClick={() => setIsNewWbOpen(false)}>
                Cancel
              </Button>
              <Button type="submit">Create Workbook</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Dialog: Create Sheet */}
      <Dialog open={isNewSheetOpen} onOpenChange={setIsNewSheetOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Add New Sheet</DialogTitle>
            <DialogDescription>
              Add a new tab to this workbook supporting up to 50,000 rows.
            </DialogDescription>
          </DialogHeader>

          <form onSubmit={handleCreateSheetSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="sheet-name" className="text-xs font-medium text-[var(--foreground)]">
                Sheet Name
              </label>
              <Input
                id="sheet-name"
                value={newSheetName}
                onChange={(e) => setNewSheetName(e.target.value)}
                placeholder="e.g. Expenses"
                autoFocus
                required
              />
            </div>

            <DialogFooter className="gap-2 sm:gap-0">
              <Button type="button" variant="outline" onClick={() => setIsNewSheetOpen(false)}>
                Cancel
              </Button>
              <Button type="submit">Add Sheet</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Dialog: Switch Workbook */}
      <Dialog open={isSwitchWbOpen} onOpenChange={setIsSwitchWbOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Select Workbook</DialogTitle>
            <DialogDescription>Switch to another workbook in this organization.</DialogDescription>
          </DialogHeader>

          <div className="max-h-64 overflow-y-auto space-y-2 py-2">
            {workbooks.map((wb) => (
              <div
                key={wb.id}
                onClick={() => {
                  setActiveWorkbook(wb);
                  if (wb.sheets && wb.sheets.length > 0) {
                    setSheets(wb.sheets);
                    setActiveSheetId(wb.sheets[0].id);
                  }
                  setCells({});
                  setIsSwitchWbOpen(false);
                }}
                className={`p-3 rounded-lg border cursor-pointer transition-colors ${
                  wb.id === activeWorkbook?.id
                    ? "border-[var(--primary)] bg-[var(--primary)]/10"
                    : "border-[var(--border)] hover:bg-[var(--surface-raised)]"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-[var(--foreground)]">{wb.name}</span>
                  <Badge variant="outline" className="text-[10px]">
                    {wb.sheet_count || wb.sheets?.length || 1} Sheets
                  </Badge>
                </div>
                {wb.description && (
                  <p className="text-[11px] text-[var(--muted-foreground)] mt-1">{wb.description}</p>
                )}
              </div>
            ))}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setIsSwitchWbOpen(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

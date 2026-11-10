"use client";

import { useSpaRouter } from "@/components/providers/spa-router-provider";
import { apiUrl } from "@/lib/api";
import * as React from "react";
import {
  FileText,
  Plus,
  Search,
  Play,
  Download,
  Clock,
  CheckCircle2,
  XCircle,
  AlertCircle,
  MoveUp,
  MoveDown,
  Trash2,
  Edit2,
  RefreshCw,
  FileCheck,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";

interface WorkspaceOption {
  id: string;
  name: string;
  slug: string;
}

interface ReportSection {
  id: string;
  report_definition_id: string;
  section_type: string;
  title?: string | null;
  description?: string | null;
  custom_content?: string | null;
  sort_order: number;
  page_break_after: boolean;
}

interface ReportDefinition {
  id: string;
  organization_id: string;
  workspace_id: string;
  title: string;
  description?: string | null;
  format: string;
  page_orientation: string;
  page_size: string;
  header_text?: string | null;
  footer_text?: string | null;
  include_table_of_contents: boolean;
  include_cover_page: boolean;
  theme: string;
  is_archived: boolean;
  sections?: ReportSection[];
}

interface ReportRun {
  id: string;
  report_definition_id?: string;
  report_title: string;
  trigger_type: string;
  status: string;
  pdf_file_size_bytes?: number | null;
  pdf_page_count?: number | null;
  execution_duration_ms?: number | null;
  queued_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
}

function generateIdempotencyKey(): string {
  return `manual-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
}

export default function ReportsPage() {
  const _router = useSpaRouter();
  const [workspaces, setWorkspaces] = React.useState<WorkspaceOption[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = React.useState<string>("");
  const [definitions, setDefinitions] = React.useState<ReportDefinition[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [search, setSearch] = React.useState("");
  const [csrfToken, setCsrfToken] = React.useState("");
  const [notice, setNotice] = React.useState<{ message: string; type: "success" | "error" } | null>(null);

  // Active run tracking & polling
  const [activeRuns, setActiveRuns] = React.useState<Record<string, ReportRun>>({});
  const [pollIntervals, setPollIntervals] = React.useState<Record<string, NodeJS.Timeout>>({});

  // History modal
  const [historyModalOpen, setHistoryModalOpen] = React.useState(false);
  const [selectedDefForHistory, setSelectedDefForHistory] = React.useState<ReportDefinition | null>(null);
  const [runHistory, setRunHistory] = React.useState<ReportRun[]>([]);
  const [historyLoading, setHistoryLoading] = React.useState(false);

  // Definition create/edit modal
  const [isDefModalOpen, setIsDefModalOpen] = React.useState(false);
  const [isEditingDef, setIsEditingDef] = React.useState(false);
  const [activeDefId, setActiveDefId] = React.useState<string | null>(null);
  const [defTitle, setDefTitle] = React.useState("");
  const [defDesc, setDefDesc] = React.useState("");
  const [defOrientation, setDefOrientation] = React.useState("portrait");
  const [defPageSize, setDefPageSize] = React.useState("a4");
  const [defHeader, setDefHeader] = React.useState("");
  const [defFooter, setDefFooter] = React.useState("");
  const [defIncludeCover, setDefIncludeCover] = React.useState(true);
  const [defIncludeToc, setDefIncludeToc] = React.useState(true);
  const [submittingDef, setSubmittingDef] = React.useState(false);

  // Sections management modal
  const [isSectionsModalOpen, setIsSectionsModalOpen] = React.useState(false);
  const [selectedDefForSections, setSelectedDefForSections] = React.useState<ReportDefinition | null>(null);
  const [sections, setSections] = React.useState<ReportSection[]>([]);
  const [isAddSectionOpen, setIsAddSectionOpen] = React.useState(false);
  const [secType, setSecType] = React.useState("summary");
  const [secTitle, setSecTitle] = React.useState("");
  const [secContent, setSecContent] = React.useState("");
  const [secPageBreak, setSecPageBreak] = React.useState(false);
  const [submittingSec, setSubmittingSec] = React.useState(false);

  const showNotice = React.useCallback((message: string, type: "success" | "error") => {
    setNotice({ message, type });
    setTimeout(() => setNotice(null), 6000);
  }, []);

  // Fetch CSRF Token
  const fetchCsrfToken = React.useCallback(async () => {
    try {
      const res = await fetch(apiUrl("/api/v1/auth/csrf-token"), { method: "POST", credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        if (data?.csrf_token) setCsrfToken(data.csrf_token);
      }
    } catch {
      // Graceful fallback
    }
  }, []);

  // Fetch Workspaces
  const loadWorkspaces = React.useCallback(async () => {
    try {
      const res = await fetch(apiUrl("/api/v1/workspaces"), { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        const wsList: WorkspaceOption[] = Array.isArray(data) ? data : [];
        setWorkspaces(wsList);
        setSelectedWorkspaceId((current) => current || (wsList.length > 0 ? wsList[0].id : ""));
      }
    } catch {
      // Fallback
    }
  }, []);

  // Fetch Definitions
  const loadDefinitions = React.useCallback(async (wsId?: string) => {
    try {
      setLoading(true);
      const targetWs = wsId || selectedWorkspaceId;
      const url = targetWs
        ? `/api/v1/reports/definitions?workspace_id=${encodeURIComponent(targetWs)}`
        : "/api/v1/reports/definitions";
      const res = await fetch(url, { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        setDefinitions(Array.isArray(data) ? data : []);
      } else {
        const err = await res.json().catch(() => ({}));
        showNotice(err?.error?.message || "Failed to load report definitions.", "error");
      }
    } catch {
      showNotice("Network error loading reports.", "error");
    } finally {
      setLoading(false);
    }
  }, [selectedWorkspaceId, showNotice]);

  React.useEffect(() => {
    fetchCsrfToken();
    loadWorkspaces();
  }, [fetchCsrfToken, loadWorkspaces]);

  React.useEffect(() => {
    if (selectedWorkspaceId) {
      loadDefinitions(selectedWorkspaceId);
    }
  }, [selectedWorkspaceId, loadDefinitions]);

  // Clean up intervals on unmount
  React.useEffect(() => {
    return () => {
      Object.values(pollIntervals).forEach((int) => clearInterval(int));
    };
  }, [pollIntervals]);

  // Create or Update Definition
  const handleSaveDefinition = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!defTitle.trim() || !selectedWorkspaceId) return;

    try {
      setSubmittingDef(true);
      if (isEditingDef && activeDefId) {
        const res = await fetch(apiUrl(`/api/v1/reports/definitions/${activeDefId}`), {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken,
          },
          credentials: "include",
          body: JSON.stringify({
            title: defTitle.trim(),
            description: defDesc.trim() || null,
            page_orientation: defOrientation,
            page_size: defPageSize,
            header_text: defHeader.trim() || null,
            footer_text: defFooter.trim() || null,
            include_cover_page: defIncludeCover,
            include_table_of_contents: defIncludeToc,
          }),
        });

        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          showNotice(err?.error?.message || "Failed to update report definition.", "error");
          return;
        }

        showNotice("Report definition updated successfully.", "success");
      } else {
        const res = await fetch(apiUrl("/api/v1/reports/definitions"), {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken,
          },
          credentials: "include",
          body: JSON.stringify({
            workspace_id: selectedWorkspaceId,
            title: defTitle.trim(),
            description: defDesc.trim() || null,
            page_orientation: defOrientation,
            page_size: defPageSize,
            header_text: defHeader.trim() || null,
            footer_text: defFooter.trim() || null,
            include_cover_page: defIncludeCover,
            include_table_of_contents: defIncludeToc,
            format: "pdf",
          }),
        });

        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          showNotice(err?.error?.message || "Failed to create report definition.", "error");
          return;
        }

        showNotice(`Report "${defTitle}" created successfully.`, "success");
      }

      setIsDefModalOpen(false);
      resetDefForm();
      await loadDefinitions(selectedWorkspaceId);
    } catch {
      showNotice("Network error saving report definition.", "error");
    } finally {
      setSubmittingDef(false);
    }
  };

  const resetDefForm = () => {
    setIsEditingDef(false);
    setActiveDefId(null);
    setDefTitle("");
    setDefDesc("");
    setDefOrientation("portrait");
    setDefPageSize("a4");
    setDefHeader("");
    setDefFooter("");
    setDefIncludeCover(true);
    setDefIncludeToc(true);
  };

  const openCreateDefModal = () => {
    resetDefForm();
    setIsDefModalOpen(true);
  };

  const openEditDefModal = (def: ReportDefinition) => {
    setIsEditingDef(true);
    setActiveDefId(def.id);
    setDefTitle(def.title);
    setDefDesc(def.description || "");
    setDefOrientation(def.page_orientation || "portrait");
    setDefPageSize(def.page_size || "a4");
    setDefHeader(def.header_text || "");
    setDefFooter(def.footer_text || "");
    setDefIncludeCover(def.include_cover_page ?? true);
    setDefIncludeToc(def.include_table_of_contents ?? true);
    setIsDefModalOpen(true);
  };

  // Delete Definition
  const handleDeleteDef = async (defId: string, title: string) => {
    if (!confirm(`Are you sure you want to delete report definition "${title}"?`)) return;

    try {
      const res = await fetch(apiUrl(`/api/v1/reports/definitions/${defId}`), {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrfToken },
        credentials: "include",
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showNotice(err?.error?.message || "Failed to delete report definition.", "error");
        return;
      }

      showNotice(`Report "${title}" deleted.`, "success");
      await loadDefinitions(selectedWorkspaceId);
    } catch {
      showNotice("Network error deleting report definition.", "error");
    }
  };

  // Open Sections Manager
  const openSectionsModal = async (def: ReportDefinition) => {
    setSelectedDefForSections(def);
    try {
      const res = await fetch(apiUrl(`/api/v1/reports/definitions/${def.id}`), { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        setSections(data.sections || []);
      }
    } catch {
      setSections(def.sections || []);
    }
    setIsSectionsModalOpen(true);
  };

  // Add Section
  const handleAddSection = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedDefForSections) return;

    try {
      setSubmittingSec(true);
      const res = await fetch(apiUrl(`/api/v1/reports/definitions/${selectedDefForSections.id}/sections`), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        credentials: "include",
        body: JSON.stringify({
          section_type: secType,
          title: secTitle.trim() || undefined,
          custom_content: secContent.trim() || undefined,
          page_break_after: secPageBreak,
          sort_order: sections.length,
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showNotice(err?.error?.message || "Failed to add section.", "error");
        return;
      }

      const createdSec = await res.json();
      setSections((prev) => [...prev, createdSec]);
      setIsAddSectionOpen(false);
      setSecTitle("");
      setSecContent("");
      setSecPageBreak(false);
      showNotice("Section added successfully.", "success");
    } catch {
      showNotice("Network error adding section.", "error");
    } finally {
      setSubmittingSec(false);
    }
  };

  // Delete Section
  const handleDeleteSection = async (secId: string) => {
    if (!selectedDefForSections) return;

    try {
      const res = await fetch(apiUrl(`/api/v1/reports/definitions/${selectedDefForSections.id}/sections/${secId}`), {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrfToken },
        credentials: "include",
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showNotice(err?.error?.message || "Failed to delete section.", "error");
        return;
      }

      setSections((prev) => prev.filter((s) => s.id !== secId));
      showNotice("Section deleted.", "success");
    } catch {
      showNotice("Network error deleting section.", "error");
    }
  };

  // Reorder Section
  const handleMoveSection = async (index: number, direction: "up" | "down") => {
    if (!selectedDefForSections) return;
    const targetIdx = direction === "up" ? index - 1 : index + 1;
    if (targetIdx < 0 || targetIdx >= sections.length) return;

    const newSections = [...sections];
    const [moved] = newSections.splice(index, 1);
    newSections.splice(targetIdx, 0, moved);
    setSections(newSections);

    try {
      await fetch(apiUrl(`/api/v1/reports/definitions/${selectedDefForSections.id}/sections/reorder`), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        credentials: "include",
        body: JSON.stringify({
          ordered_section_ids: newSections.map((s) => s.id),
        }),
      });
    } catch {
      // Revert if failed
    }
  };

  // Trigger Report Run
  const handleTriggerRun = async (def: ReportDefinition) => {
    try {
      const idempotencyKey = generateIdempotencyKey();
      const res = await fetch(apiUrl(`/api/v1/reports/definitions/${def.id}/run`), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        credentials: "include",
        body: JSON.stringify({
          idempotency_key: idempotencyKey,
          trigger_type: "manual",
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showNotice(err?.error?.message || "Failed to trigger report run.", "error");
        return;
      }

      const runData: ReportRun = await res.json();
      setActiveRuns((prev) => ({ ...prev, [def.id]: runData }));
      showNotice(`Report execution started for "${def.title}".`, "success");

      // Start short polling
      pollRunStatus(def.id, runData.id);
    } catch {
      showNotice("Network error starting report run.", "error");
    }
  };

  // Poll Run Status
  const pollRunStatus = (defId: string, runId: string) => {
    if (pollIntervals[defId]) {
      clearInterval(pollIntervals[defId]);
    }

    const interval = setInterval(async () => {
      try {
        const res = await fetch(apiUrl(`/api/v1/reports/runs/${runId}`), { credentials: "include" });
        if (res.ok) {
          const run: ReportRun = await res.json();
          setActiveRuns((prev) => ({ ...prev, [defId]: run }));

          if (run.status === "COMPLETED" || run.status === "FAILED") {
            clearInterval(interval);
            setPollIntervals((prev) => {
              const next = { ...prev };
              delete next[defId];
              return next;
            });
            if (run.status === "COMPLETED") {
              showNotice(`Report "${run.report_title}" compiled successfully!`, "success");
            } else {
              showNotice(`Report "${run.report_title}" failed: ${run.error_message || "Unknown error"}`, "error");
            }
          }
        }
      } catch {
        // Continue polling
      }
    }, 2000);

    setPollIntervals((prev) => ({ ...prev, [defId]: interval }));
  };

  // Download PDF via Token
  const handleDownloadPdf = async (runId: string) => {
    try {
      const resToken = await fetch(apiUrl(`/api/v1/reports/runs/${runId}/download-token`), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        credentials: "include",
      });

      if (!resToken.ok) {
        const err = await resToken.json().catch(() => ({}));
        showNotice(err?.error?.message || "Failed to generate download token.", "error");
        return;
      }

      const tokenData = await resToken.json();
      if (tokenData?.download_url) {
        window.open(tokenData.download_url, "_blank");
      }
    } catch {
      showNotice("Network error initiating PDF download.", "error");
    }
  };

  // Open Run History
  const openHistoryModal = async (def: ReportDefinition) => {
    setSelectedDefForHistory(def);
    setHistoryLoading(true);
    setHistoryModalOpen(true);
    try {
      const res = await fetch(apiUrl(`/api/v1/reports/definitions/${def.id}/runs`), { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        setRunHistory(Array.isArray(data) ? data : []);
      }
    } catch {
      showNotice("Failed to load run history.", "error");
    } finally {
      setHistoryLoading(false);
    }
  };

  const filteredDefs = definitions.filter(
    (d) =>
      d.title.toLowerCase().includes(search.toLowerCase()) ||
      (d.description && d.description.toLowerCase().includes(search.toLowerCase()))
  );

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
      {/* Notice Banner */}
      {notice && (
        <div
          role="alert"
          className={`flex items-center gap-3 px-4 py-3 rounded-lg border text-sm font-medium transition-all ${
            notice.type === "success"
              ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400"
              : "bg-rose-500/10 border-rose-500/30 text-rose-600 dark:text-rose-400"
          }`}
        >
          {notice.type === "success" ? <CheckCircle2 className="h-5 w-5 shrink-0" /> : <XCircle className="h-5 w-5 shrink-0" />}
          <span>{notice.message}</span>
        </div>
      )}

      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold tracking-tight text-[var(--foreground)]">Reports</h1>
            <Badge variant="outline" className="border-[var(--border)] text-[var(--muted-foreground)]">
              {definitions.length} Definitions
            </Badge>
          </div>
          <p className="text-sm text-[var(--muted-foreground)]">
            Pixel-perfect PDF compilation, modular section layouts, and secure token-authorized downloads.
          </p>
        </div>
        <Button onClick={openCreateDefModal} className="gap-2 shrink-0">
          <Plus className="h-4 w-4" />
          New Report
        </Button>
      </div>

      {/* Toolbar / Workspace Selector & Search */}
      <div className="flex flex-col sm:flex-row items-center gap-3">
        {workspaces.length > 0 && (
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <label htmlFor="rep-ws-select" className="text-xs font-medium text-[var(--muted-foreground)] whitespace-nowrap">
              Workspace:
            </label>
            <select
              id="rep-ws-select"
              value={selectedWorkspaceId}
              onChange={(e) => setSelectedWorkspaceId(e.target.value)}
              className="h-9 px-3 rounded-md border border-[var(--border)] bg-[var(--surface)] text-xs text-[var(--foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--ring)]"
            >
              {workspaces.map((ws) => (
                <option key={ws.id} value={ws.id}>
                  {ws.name} ({ws.slug})
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="relative flex-1 max-w-md w-full">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--muted-foreground)]" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search report definitions..."
            className="pl-9 bg-[var(--surface)]"
          />
        </div>
      </div>

      {/* Reports Grid */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {[1, 2, 3].map((i) => (
            <div key={i} className="p-5 rounded-xl border border-[var(--border)] bg-[var(--surface)] space-y-3">
              <Skeleton className="h-6 w-3/4" />
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-12 w-full" />
            </div>
          ))}
        </div>
      ) : filteredDefs.length === 0 ? (
        <div className="flex flex-col items-center justify-center p-12 text-center rounded-xl border border-dashed border-[var(--border)] bg-[var(--surface)] space-y-3">
          <div className="p-3 rounded-full bg-[var(--muted)] text-[var(--muted-foreground)]">
            <FileText className="h-8 w-8" />
          </div>
          <h3 className="font-semibold text-base text-[var(--foreground)]">No report definitions found</h3>
          <p className="text-xs text-[var(--muted-foreground)] max-w-sm">
            {search ? "No definitions match your search." : "Create your first multi-section report definition to generate compiled executive PDFs."}
          </p>
          {!search && (
            <Button onClick={openCreateDefModal} size="sm" className="mt-2 gap-2">
              <Plus className="h-3.5 w-3.5" />
              Create Report Definition
            </Button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {filteredDefs.map((def) => {
            const activeRun = activeRuns[def.id];
            const isRunning = activeRun && (activeRun.status === "PENDING" || activeRun.status === "GENERATING_SNAPSHOT" || activeRun.status === "RENDERING_HTML" || activeRun.status === "COMPILING_PDF");
            const isCompleted = activeRun && activeRun.status === "COMPLETED";

            return (
              <div
                key={def.id}
                className="flex flex-col justify-between rounded-xl border border-[var(--border)] bg-[var(--surface)] p-5 shadow-xs hover:border-[var(--border-hover)] transition-all"
              >
                <div className="space-y-3">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <h2 className="font-semibold text-base text-[var(--foreground)] truncate max-w-[200px]" title={def.title}>
                        {def.title}
                      </h2>
                      {def.description && (
                        <p className="text-xs text-[var(--muted-foreground)] line-clamp-2 mt-0.5">
                          {def.description}
                        </p>
                      )}
                    </div>
                    <Badge variant="outline" className="uppercase text-[10px] tracking-wider font-mono shrink-0">
                      {def.page_orientation}
                    </Badge>
                  </div>

                  <div className="flex items-center gap-2 text-xs text-[var(--muted-foreground)]">
                    <span className="px-2 py-0.5 rounded bg-[var(--muted)] font-mono text-[11px]">
                      {def.format.toUpperCase()} / {def.page_size.toUpperCase()}
                    </span>
                    <span>•</span>
                    <span>{def.sections?.length || 0} Sections</span>
                  </div>

                  {/* Active Run Status Widget */}
                  {activeRun && (
                    <div
                      className={`p-3 rounded-lg border text-xs flex items-center justify-between gap-2 transition-all ${
                        isRunning
                          ? "bg-sky-500/10 border-sky-500/20 text-sky-600 dark:text-sky-400"
                          : isCompleted
                          ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400"
                          : "bg-rose-500/10 border-rose-500/20 text-rose-600 dark:text-rose-400"
                      }`}
                    >
                      <div className="flex items-center gap-2 truncate">
                        {isRunning ? (
                          <RefreshCw className="h-4 w-4 animate-spin shrink-0" />
                        ) : isCompleted ? (
                          <FileCheck className="h-4 w-4 shrink-0" />
                        ) : (
                          <AlertCircle className="h-4 w-4 shrink-0" />
                        )}
                        <span className="font-medium truncate capitalize">
                          {(activeRun.status || "PENDING").replace(/_/g, " ").toLowerCase()}
                        </span>
                      </div>
                      {isCompleted && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => handleDownloadPdf(activeRun.id)}
                          className="h-7 text-xs font-semibold gap-1.5 shrink-0 bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-700 dark:text-emerald-300"
                        >
                          <Download className="h-3.5 w-3.5" />
                          Download
                        </Button>
                      )}
                    </div>
                  )}
                </div>

                <div className="mt-5 pt-4 border-t border-[var(--border)] flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5">
                    <Button
                      size="sm"
                      onClick={() => handleTriggerRun(def)}
                      disabled={Boolean(isRunning)}
                      className="h-8 gap-1.5 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white"
                    >
                      <Play className="h-3.5 w-3.5 fill-current" />
                      {isRunning ? "Running..." : "Run Now"}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => openSectionsModal(def)}
                      className="h-8 gap-1.5 text-xs"
                    >
                      Sections
                    </Button>
                  </div>

                  <div className="flex items-center gap-1">
                    <Button
                      size="icon"
                      variant="ghost"
                      onClick={() => openHistoryModal(def)}
                      className="h-8 w-8 text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                      title="Run History"
                      aria-label={`History for ${def.title}`}
                    >
                      <Clock className="h-4 w-4" />
                    </Button>
                    <Button
                      size="icon"
                      variant="ghost"
                      onClick={() => openEditDefModal(def)}
                      className="h-8 w-8 text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                      title="Edit Definition"
                      aria-label={`Edit ${def.title}`}
                    >
                      <Edit2 className="h-4 w-4" />
                    </Button>
                    <Button
                      size="icon"
                      variant="ghost"
                      onClick={() => handleDeleteDef(def.id, def.title)}
                      className="h-8 w-8 text-rose-500 hover:text-rose-600 hover:bg-rose-500/10"
                      title="Delete Definition"
                      aria-label={`Delete ${def.title}`}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Create / Edit Report Definition Modal */}
      <Dialog open={isDefModalOpen} onOpenChange={setIsDefModalOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{isEditingDef ? "Edit Report Definition" : "New Report Definition"}</DialogTitle>
            <DialogDescription>
              Configure page geometry, headers, footers, and layout properties for this report.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleSaveDefinition} className="space-y-4 py-2">
            <div className="space-y-1.5">
              <label htmlFor="def-title" className="text-xs font-medium text-[var(--foreground)]">
                Report Title
              </label>
              <Input
                id="def-title"
                value={defTitle}
                onChange={(e) => setDefTitle(e.target.value)}
                placeholder="e.g. Executive Quarterly Performance"
                required
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="def-desc" className="text-xs font-medium text-[var(--foreground)]">
                Description (Optional)
              </label>
              <Input
                id="def-desc"
                value={defDesc}
                onChange={(e) => setDefDesc(e.target.value)}
                placeholder="Brief summary of compiled metrics and targets"
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label htmlFor="def-orient" className="text-xs font-medium text-[var(--foreground)]">
                  Page Orientation
                </label>
                <select
                  id="def-orient"
                  value={defOrientation}
                  onChange={(e) => setDefOrientation(e.target.value)}
                  className="w-full h-9 px-3 rounded-md border border-[var(--border)] bg-[var(--surface)] text-xs text-[var(--foreground)]"
                >
                  <option value="portrait">Portrait</option>
                  <option value="landscape">Landscape</option>
                </select>
              </div>
              <div className="space-y-1.5">
                <label htmlFor="def-pagesize" className="text-xs font-medium text-[var(--foreground)]">
                  Page Size
                </label>
                <select
                  id="def-pagesize"
                  value={defPageSize}
                  onChange={(e) => setDefPageSize(e.target.value)}
                  className="w-full h-9 px-3 rounded-md border border-[var(--border)] bg-[var(--surface)] text-xs text-[var(--foreground)]"
                >
                  <option value="a4">A4 (Standard)</option>
                  <option value="letter">US Letter</option>
                </select>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label htmlFor="def-hdr" className="text-xs font-medium text-[var(--foreground)]">
                  Header Text
                </label>
                <Input
                  id="def-hdr"
                  value={defHeader}
                  onChange={(e) => setDefHeader(e.target.value)}
                  placeholder="VIBE Confidential"
                />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="def-ftr" className="text-xs font-medium text-[var(--foreground)]">
                  Footer Text
                </label>
                <Input
                  id="def-ftr"
                  value={defFooter}
                  onChange={(e) => setDefFooter(e.target.value)}
                  placeholder="Internal Audit Only"
                />
              </div>
            </div>

            <div className="flex items-center gap-6 pt-1 text-xs">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={defIncludeCover}
                  onChange={(e) => setDefIncludeCover(e.target.checked)}
                  className="rounded border-[var(--border)]"
                />
                Include Cover Page
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={defIncludeToc}
                  onChange={(e) => setDefIncludeToc(e.target.checked)}
                  className="rounded border-[var(--border)]"
                />
                Table of Contents
              </label>
            </div>

            <DialogFooter className="gap-2 sm:gap-0 pt-3">
              <Button type="button" variant="outline" onClick={() => setIsDefModalOpen(false)} disabled={submittingDef}>
                Cancel
              </Button>
              <Button type="submit" disabled={submittingDef || !defTitle.trim()}>
                {submittingDef ? "Saving..." : isEditingDef ? "Save Changes" : "Create Report"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Sections Management Modal */}
      <Dialog open={isSectionsModalOpen} onOpenChange={setIsSectionsModalOpen}>
        <DialogContent className="sm:max-w-2xl max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>Report Sections: {selectedDefForSections?.title}</DialogTitle>
            <DialogDescription>
              Add, organize, and reorder modular content blocks rendered in the PDF.
            </DialogDescription>
          </DialogHeader>

          <div className="flex items-center justify-between py-2 border-b border-[var(--border)]">
            <span className="text-xs font-semibold text-[var(--foreground)]">{sections.length} Active Sections</span>
            <Button size="sm" onClick={() => setIsAddSectionOpen(true)} className="gap-1 text-xs h-8">
              <Plus className="h-3.5 w-3.5" />
              Add Section
            </Button>
          </div>

          <div className="flex-1 overflow-y-auto space-y-2 py-3">
            {sections.length === 0 ? (
              <div className="text-center py-8 text-xs text-[var(--muted-foreground)]">
                No sections added yet. Click &quot;Add Section&quot; to configure summary blocks, charts, or markdown tables.
              </div>
            ) : (
              sections.map((sec, idx) => (
                <div
                  key={sec.id}
                  className="flex items-center justify-between p-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] text-xs"
                >
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-xs text-[var(--muted-foreground)] w-4 text-center">
                      {idx + 1}
                    </span>
                    <div>
                      <div className="font-semibold text-[var(--foreground)]">
                        {sec.title || `Untitled ${sec.section_type}`}
                      </div>
                      <div className="flex items-center gap-2 text-[10px] text-[var(--muted-foreground)] mt-0.5">
                        <Badge variant="outline" className="text-[9px] uppercase px-1 py-0">
                          {sec.section_type}
                        </Badge>
                        {sec.page_break_after && <span>• Page break after</span>}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-1">
                    <Button
                      size="icon"
                      variant="ghost"
                      onClick={() => handleMoveSection(idx, "up")}
                      disabled={idx === 0}
                      className="h-7 w-7 text-[var(--muted-foreground)] disabled:opacity-30"
                      aria-label="Move Section Up"
                    >
                      <MoveUp className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      size="icon"
                      variant="ghost"
                      onClick={() => handleMoveSection(idx, "down")}
                      disabled={idx === sections.length - 1}
                      className="h-7 w-7 text-[var(--muted-foreground)] disabled:opacity-30"
                      aria-label="Move Section Down"
                    >
                      <MoveDown className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      size="icon"
                      variant="ghost"
                      onClick={() => handleDeleteSection(sec.id)}
                      className="h-7 w-7 text-rose-500 hover:text-rose-600 hover:bg-rose-500/10"
                      aria-label="Delete Section"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              ))
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setIsSectionsModalOpen(false)}>
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Add Section Sub-Modal */}
      <Dialog open={isAddSectionOpen} onOpenChange={setIsAddSectionOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Add Section Block</DialogTitle>
            <DialogDescription>Select content type and provide custom markdown or query layout.</DialogDescription>
          </DialogHeader>
          <form onSubmit={handleAddSection} className="space-y-4 py-2">
            <div className="space-y-1.5">
              <label htmlFor="sec-type" className="text-xs font-medium text-[var(--foreground)]">
                Section Type
              </label>
              <select
                id="sec-type"
                value={secType}
                onChange={(e) => setSecType(e.target.value)}
                className="w-full h-9 px-3 rounded-md border border-[var(--border)] bg-[var(--surface)] text-xs text-[var(--foreground)]"
              >
                <option value="summary">Executive Summary Block</option>
                <option value="markdown">Markdown Narrative</option>
                <option value="table">Tabular Data Grid</option>
                <option value="chart">Analytical Chart</option>
              </select>
            </div>

            <div className="space-y-1.5">
              <label htmlFor="sec-title" className="text-xs font-medium text-[var(--foreground)]">
                Section Title
              </label>
              <Input
                id="sec-title"
                value={secTitle}
                onChange={(e) => setSecTitle(e.target.value)}
                placeholder="e.g. Monthly Revenue Breakdown"
                autoFocus
              />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="sec-content" className="text-xs font-medium text-[var(--foreground)]">
                Custom Content / Description
              </label>
              <textarea
                id="sec-content"
                value={secContent}
                onChange={(e) => setSecContent(e.target.value)}
                placeholder="Enter markdown narrative or executive notes to compile into this section..."
                rows={4}
                className="w-full p-2.5 rounded-md border border-[var(--border)] bg-[var(--surface)] text-xs text-[var(--foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--ring)]"
              />
            </div>

            <label className="flex items-center gap-2 text-xs cursor-pointer">
              <input
                type="checkbox"
                checked={secPageBreak}
                onChange={(e) => setSecPageBreak(e.target.checked)}
                className="rounded border-[var(--border)]"
              />
              Force Page Break After Section
            </label>

            <DialogFooter className="gap-2 sm:gap-0 pt-3">
              <Button type="button" variant="outline" onClick={() => setIsAddSectionOpen(false)} disabled={submittingSec}>
                Cancel
              </Button>
              <Button type="submit" disabled={submittingSec}>
                {submittingSec ? "Adding..." : "Add Section"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Run History Modal */}
      <Dialog open={historyModalOpen} onOpenChange={setHistoryModalOpen}>
        <DialogContent className="sm:max-w-2xl max-h-[80vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>Execution History: {selectedDefForHistory?.title}</DialogTitle>
            <DialogDescription>Past asynchronous compilation runs and download tokens.</DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto space-y-2 py-3">
            {historyLoading ? (
              <div className="space-y-2">
                {[1, 2, 3].map((i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : runHistory.length === 0 ? (
              <div className="text-center py-8 text-xs text-[var(--muted-foreground)]">
                No past runs recorded for this report definition.
              </div>
            ) : (
              runHistory.map((run) => (
                <div
                  key={run.id}
                  className="flex items-center justify-between p-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] text-xs"
                >
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-[var(--foreground)]">Run {run.id.substring(0, 8)}</span>
                      <Badge
                        className={`text-[9px] uppercase px-1.5 py-0 ${
                          run.status === "COMPLETED"
                            ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                            : run.status === "FAILED"
                            ? "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20"
                            : "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20"
                        }`}
                      >
                        {run.status}
                      </Badge>
                    </div>
                    <div className="text-[11px] text-[var(--muted-foreground)]">
                      Triggered {run.queued_at ? new Date(run.queued_at).toLocaleString() : "Unknown"}
                      {run.execution_duration_ms ? ` • Duration: ${run.execution_duration_ms}ms` : ""}
                      {run.pdf_page_count ? ` • Pages: ${run.pdf_page_count}` : ""}
                    </div>
                    {run.error_message && (
                      <p className="text-[11px] text-rose-500 font-mono">{run.error_message}</p>
                    )}
                  </div>

                  {run.status === "COMPLETED" && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => handleDownloadPdf(run.id)}
                      className="h-8 gap-1.5 text-xs font-semibold"
                    >
                      <Download className="h-3.5 w-3.5" />
                      Download PDF
                    </Button>
                  )}
                </div>
              ))
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setHistoryModalOpen(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

"use client";

import { useSpaRouter } from "@/components/providers/spa-router-provider";
import { apiUrl } from "@/lib/api";
import * as React from "react";
import {
  Layers,
  Plus,
  Search,
  LayoutDashboard,
  FileSpreadsheet,
  FileText,
  Cpu,
  Trash2,
  Edit2,
  AlertTriangle,
  CheckCircle2,
  XCircle,
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

interface ResourceCounts {
  dashboards: number;
  workbooks: number;
  analytics_jobs: number;
  report_definitions: number;
}

interface WorkspaceItem {
  id: string;
  organization_id: string;
  name: string;
  slug: string;
  created_at: string;
  updated_at: string;
  resource_counts?: ResourceCounts;
}

export default function WorkspacesPage() {
  const _router = useSpaRouter();
  const [workspaces, setWorkspaces] = React.useState<WorkspaceItem[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [search, setSearch] = React.useState("");
  const [csrfToken, setCsrfToken] = React.useState("");
  const [notice, setNotice] = React.useState<{ message: string; type: "success" | "error" } | null>(null);

  // Modal states
  const [isNewOpen, setIsNewOpen] = React.useState(false);
  const [newName, setNewName] = React.useState("");
  const [newSlug, setNewSlug] = React.useState("");
  const [slugManuallyEdited, setSlugManuallyEdited] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);

  const [isEditOpen, setIsEditOpen] = React.useState(false);
  const [editingWs, setEditingWs] = React.useState<WorkspaceItem | null>(null);
  const [editName, setEditName] = React.useState("");
  const [editSlug, setEditSlug] = React.useState("");

  const [isDeleteOpen, setIsDeleteOpen] = React.useState(false);
  const [deletingWs, setDeletingWs] = React.useState<WorkspaceItem | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = React.useState("");

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
  const loadWorkspaces = React.useCallback(async (query = "") => {
    try {
      setLoading(true);
      const url = query.trim()
        ? `/api/v1/workspaces?search=${encodeURIComponent(query.trim())}`
        : "/api/v1/workspaces";
      const res = await fetch(url, { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        setWorkspaces(Array.isArray(data) ? data : []);
      } else {
        const err = await res.json().catch(() => ({}));
        showNotice(err?.error?.message || "Failed to load workspaces.", "error");
      }
    } catch {
      showNotice("Network error loading workspaces.", "error");
    } finally {
      setLoading(false);
    }
  }, [showNotice]);

  React.useEffect(() => {
    fetchCsrfToken();
    loadWorkspaces();
  }, [fetchCsrfToken, loadWorkspaces]);

  // Handle Search Input
  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setSearch(val);
    loadWorkspaces(val);
  };

  // Auto-generate slug from name
  const handleNameChange = (val: string) => {
    setNewName(val);
    if (!slugManuallyEdited) {
      const generated = val
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
      setNewSlug(generated);
    }
  };

  // Create Workspace
  const handleCreateWorkspace = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim() || !newSlug.trim()) return;

    try {
      setSubmitting(true);
      const res = await fetch(apiUrl("/api/v1/workspaces"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        credentials: "include",
        body: JSON.stringify({
          name: newName.trim(),
          slug: newSlug.trim().toLowerCase(),
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showNotice(err?.error?.message || err?.detail || "Failed to create workspace.", "error");
        return;
      }

      showNotice(`Workspace "${newName}" created successfully.`, "success");
      setIsNewOpen(false);
      setNewName("");
      setNewSlug("");
      setSlugManuallyEdited(false);
      await loadWorkspaces(search);
    } catch {
      showNotice("Network error creating workspace.", "error");
    } finally {
      setSubmitting(false);
    }
  };

  // Open Edit Modal
  const openEditModal = (ws: WorkspaceItem) => {
    setEditingWs(ws);
    setEditName(ws.name);
    setEditSlug(ws.slug);
    setIsEditOpen(true);
  };

  // Update Workspace
  const handleUpdateWorkspace = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingWs || !editName.trim()) return;

    try {
      setSubmitting(true);
      const payload: { name: string; slug?: string } = { name: editName.trim() };
      if (editingWs.slug !== "default" && editSlug.trim()) {
        payload.slug = editSlug.trim().toLowerCase();
      }

      const res = await fetch(apiUrl(`/api/v1/workspaces/${editingWs.id}`), {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        credentials: "include",
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showNotice(err?.error?.message || err?.detail || "Failed to update workspace.", "error");
        return;
      }

      showNotice("Workspace updated successfully.", "success");
      setIsEditOpen(false);
      setEditingWs(null);
      await loadWorkspaces(search);
    } catch {
      showNotice("Network error updating workspace.", "error");
    } finally {
      setSubmitting(false);
    }
  };

  // Open Delete Modal
  const openDeleteModal = (ws: WorkspaceItem) => {
    setDeletingWs(ws);
    setDeleteConfirmation("");
    setIsDeleteOpen(true);
  };

  // Delete Workspace
  const handleDeleteWorkspace = async () => {
    if (!deletingWs) return;

    try {
      setSubmitting(true);
      const res = await fetch(apiUrl(`/api/v1/workspaces/${deletingWs.id}`), {
        method: "DELETE",
        headers: {
          "X-CSRF-Token": csrfToken,
        },
        credentials: "include",
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showNotice(err?.error?.message || err?.detail || "Failed to delete workspace.", "error");
        return;
      }

      showNotice(`Workspace "${deletingWs.name}" safely deleted.`, "success");
      setIsDeleteOpen(false);
      setDeletingWs(null);
      await loadWorkspaces(search);
    } catch {
      showNotice("Network error deleting workspace.", "error");
    } finally {
      setSubmitting(false);
    }
  };

  const calculateTotalResources = (counts?: ResourceCounts) => {
    if (!counts) return 0;
    return (
      (counts.dashboards || 0) +
      (counts.workbooks || 0) +
      (counts.report_definitions || 0) +
      (counts.analytics_jobs || 0)
    );
  };

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
            <h1 className="text-2xl font-bold tracking-tight text-[var(--foreground)]">Workspaces</h1>
            <Badge variant="outline" className="border-[var(--border)] text-[var(--muted-foreground)]">
              {workspaces.length} Total
            </Badge>
          </div>
          <p className="text-sm text-[var(--muted-foreground)]">
            Manage organizational domains, tenant isolation, and resource scoping boundaries.
          </p>
        </div>
        <Button onClick={() => setIsNewOpen(true)} className="gap-2 shrink-0">
          <Plus className="h-4 w-4" />
          New Workspace
        </Button>
      </div>

      {/* Toolbar / Search */}
      <div className="flex items-center gap-3 max-w-md">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--muted-foreground)]" />
          <Input
            value={search}
            onChange={handleSearchChange}
            placeholder="Search workspaces by name or slug..."
            className="pl-9 bg-[var(--surface)]"
          />
        </div>
      </div>

      {/* Workspace Grid */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="p-5 rounded-xl border border-[var(--border)] bg-[var(--surface)] space-y-3">
              <Skeleton className="h-6 w-3/4" />
              <Skeleton className="h-4 w-1/2" />
              <div className="grid grid-cols-2 gap-2 pt-2">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            </div>
          ))}
        </div>
      ) : workspaces.length === 0 ? (
        <div className="flex flex-col items-center justify-center p-12 text-center rounded-xl border border-dashed border-[var(--border)] bg-[var(--surface)] space-y-3">
          <div className="p-3 rounded-full bg-[var(--muted)] text-[var(--muted-foreground)]">
            <Layers className="h-8 w-8" />
          </div>
          <h3 className="font-semibold text-base text-[var(--foreground)]">No workspaces found</h3>
          <p className="text-xs text-[var(--muted-foreground)] max-w-sm">
            {search ? "No workspaces match your search criteria." : "Create your first operational workspace to scope dashboards, spreadsheets, and reports."}
          </p>
          {!search && (
            <Button onClick={() => setIsNewOpen(true)} size="sm" className="mt-2 gap-2">
              <Plus className="h-3.5 w-3.5" />
              Create Workspace
            </Button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {workspaces.map((ws) => {
            const counts = ws.resource_counts || { dashboards: 0, workbooks: 0, report_definitions: 0, analytics_jobs: 0 };
            const isDefault = ws.slug === "default";

            return (
              <div
                key={ws.id}
                className="flex flex-col justify-between rounded-xl border border-[var(--border)] bg-[var(--surface)] p-5 shadow-xs hover:border-[var(--border-hover)] transition-all"
              >
                <div>
                  <div className="flex items-start justify-between gap-3">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <h2 className="font-semibold text-base text-[var(--foreground)] truncate max-w-[200px]" title={ws.name}>
                          {ws.name}
                        </h2>
                        {isDefault && (
                          <Badge className="bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/20 text-[10px] px-2 py-0">
                            Default
                          </Badge>
                        )}
                      </div>
                      <div className="font-mono text-xs text-[var(--muted-foreground)]">/{ws.slug}</div>
                    </div>

                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => openEditModal(ws)}
                        className="h-8 w-8 text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                        title="Edit Workspace"
                        aria-label={`Edit ${ws.name}`}
                      >
                        <Edit2 className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => openDeleteModal(ws)}
                        className="h-8 w-8 text-rose-500 hover:text-rose-600 hover:bg-rose-500/10"
                        title="Delete Workspace"
                        aria-label={`Delete ${ws.name}`}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>

                  {/* Resource Counts Grid */}
                  <div className="grid grid-cols-2 gap-2 mt-4 pt-4 border-t border-[var(--border)] text-xs">
                    <div className="flex items-center gap-2 p-2 rounded-md bg-[var(--muted)]/50">
                      <LayoutDashboard className="h-4 w-4 text-sky-500 shrink-0" />
                      <div>
                        <div className="font-bold text-[var(--foreground)]">{counts.dashboards}</div>
                        <div className="text-[10px] text-[var(--muted-foreground)]">Dashboards</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 p-2 rounded-md bg-[var(--muted)]/50">
                      <FileSpreadsheet className="h-4 w-4 text-emerald-500 shrink-0" />
                      <div>
                        <div className="font-bold text-[var(--foreground)]">{counts.workbooks}</div>
                        <div className="text-[10px] text-[var(--muted-foreground)]">Workbooks</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 p-2 rounded-md bg-[var(--muted)]/50">
                      <FileText className="h-4 w-4 text-violet-500 shrink-0" />
                      <div>
                        <div className="font-bold text-[var(--foreground)]">{counts.report_definitions}</div>
                        <div className="text-[10px] text-[var(--muted-foreground)]">Reports</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 p-2 rounded-md bg-[var(--muted)]/50">
                      <Cpu className="h-4 w-4 text-amber-500 shrink-0" />
                      <div>
                        <div className="font-bold text-[var(--foreground)]">{counts.analytics_jobs}</div>
                        <div className="text-[10px] text-[var(--muted-foreground)]">Analytics Jobs</div>
                      </div>
                    </div>
                  </div>
                </div>

                <div className="mt-4 pt-3 border-t border-[var(--border)] flex items-center justify-between text-[11px] text-[var(--muted-foreground)]">
                  <span>Created {new Date(ws.created_at).toLocaleDateString()}</span>
                  <span className="font-mono text-[10px]">ID: {ws.id.substring(0, 8)}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* New Workspace Modal */}
      <Dialog open={isNewOpen} onOpenChange={setIsNewOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Create Workspace</DialogTitle>
            <DialogDescription>
              Workspaces isolate dashboards, workbooks, reports, and analytical workloads.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleCreateWorkspace} className="space-y-4 py-2">
            <div className="space-y-1.5">
              <label htmlFor="ws-name" className="text-xs font-medium text-[var(--foreground)]">
                Workspace Name
              </label>
              <Input
                id="ws-name"
                value={newName}
                onChange={(e) => handleNameChange(e.target.value)}
                placeholder="e.g. Finance & Treasury"
                required
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="ws-slug" className="text-xs font-medium text-[var(--foreground)]">
                Slug (URL Identifier)
              </label>
              <Input
                id="ws-slug"
                value={newSlug}
                onChange={(e) => {
                  setNewSlug(e.target.value);
                  setSlugManuallyEdited(true);
                }}
                placeholder="finance-treasury"
                required
                className="font-mono text-xs"
              />
              <p className="text-[11px] text-[var(--muted-foreground)]">
                Only lowercase alphanumeric characters and hyphens.
              </p>
            </div>
            <DialogFooter className="gap-2 sm:gap-0 pt-2">
              <Button type="button" variant="outline" onClick={() => setIsNewOpen(false)} disabled={submitting}>
                Cancel
              </Button>
              <Button type="submit" disabled={submitting || !newName.trim() || !newSlug.trim()}>
                {submitting ? "Creating..." : "Create Workspace"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Edit Workspace Modal */}
      <Dialog open={isEditOpen} onOpenChange={setIsEditOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Edit Workspace</DialogTitle>
            <DialogDescription>Modify workspace display title or URL slug.</DialogDescription>
          </DialogHeader>
          <form onSubmit={handleUpdateWorkspace} className="space-y-4 py-2">
            <div className="space-y-1.5">
              <label htmlFor="edit-ws-name" className="text-xs font-medium text-[var(--foreground)]">
                Workspace Name
              </label>
              <Input
                id="edit-ws-name"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                required
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="edit-ws-slug" className="text-xs font-medium text-[var(--foreground)]">
                Slug
              </label>
              <Input
                id="edit-ws-slug"
                value={editSlug}
                onChange={(e) => setEditSlug(e.target.value)}
                disabled={editingWs?.slug === "default"}
                required
                className="font-mono text-xs"
              />
              {editingWs?.slug === "default" && (
                <p className="text-[11px] text-amber-500">
                  The slug of the default workspace cannot be altered.
                </p>
              )}
            </div>
            <DialogFooter className="gap-2 sm:gap-0 pt-2">
              <Button type="button" variant="outline" onClick={() => setIsEditOpen(false)} disabled={submitting}>
                Cancel
              </Button>
              <Button type="submit" disabled={submitting || !editName.trim()}>
                {submitting ? "Saving..." : "Save Changes"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Delete Workspace Guardrail Modal */}
      <Dialog open={isDeleteOpen} onOpenChange={setIsDeleteOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <div className="flex items-center gap-2 text-rose-500 mb-1">
              <AlertTriangle className="h-5 w-5 shrink-0" />
              <DialogTitle className="text-rose-500">Delete Workspace</DialogTitle>
            </div>
            <DialogDescription>
              Protected deletion verification for workspace <strong>{deletingWs?.name}</strong>.
            </DialogDescription>
          </DialogHeader>

          {deletingWs && (
            <div className="space-y-4 py-2">
              {deletingWs.slug === "default" ? (
                <div className="p-3.5 rounded-lg border border-rose-500/30 bg-rose-500/10 text-xs text-rose-600 dark:text-rose-400 space-y-1.5">
                  <p className="font-semibold">Default Workspace Protected</p>
                  <p>
                    The default workspace is an essential organizational root and cannot be deleted.
                  </p>
                </div>
              ) : calculateTotalResources(deletingWs.resource_counts) > 0 ? (
                <div className="p-3.5 rounded-lg border border-amber-500/30 bg-amber-500/10 text-xs text-amber-600 dark:text-amber-400 space-y-2">
                  <p className="font-semibold">Active Dependent Resources Detected</p>
                  <p>
                    This workspace contains active resources. To prevent unintended cascade deletion, you must move or delete these resources first:
                  </p>
                  <ul className="list-disc list-inside space-y-1 font-mono text-[11px]">
                    {deletingWs.resource_counts?.dashboards ? <li>{deletingWs.resource_counts.dashboards} Dashboards</li> : null}
                    {deletingWs.resource_counts?.workbooks ? <li>{deletingWs.resource_counts.workbooks} Workbooks</li> : null}
                    {deletingWs.resource_counts?.report_definitions ? <li>{deletingWs.resource_counts.report_definitions} Reports</li> : null}
                    {deletingWs.resource_counts?.analytics_jobs ? <li>{deletingWs.resource_counts.analytics_jobs} Analytics Jobs</li> : null}
                  </ul>
                </div>
              ) : (
                <div className="space-y-3">
                  <p className="text-xs text-[var(--muted-foreground)]">
                    This action is permanent and cannot be undone. To confirm deletion of empty workspace <strong>{deletingWs.name}</strong>, please type the workspace slug <code className="px-1.5 py-0.5 rounded bg-[var(--muted)] text-[var(--foreground)] font-mono">{deletingWs.slug}</code> below:
                  </p>
                  <Input
                    value={deleteConfirmation}
                    onChange={(e) => setDeleteConfirmation(e.target.value)}
                    placeholder={deletingWs.slug}
                    className="font-mono text-xs"
                    autoFocus
                  />
                </div>
              )}

              <DialogFooter className="gap-2 sm:gap-0 pt-2">
                <Button type="button" variant="outline" onClick={() => setIsDeleteOpen(false)} disabled={submitting}>
                  Cancel
                </Button>
                <Button
                  type="button"
                  variant="destructive"
                  onClick={handleDeleteWorkspace}
                  disabled={
                    submitting ||
                    deletingWs.slug === "default" ||
                    calculateTotalResources(deletingWs.resource_counts) > 0 ||
                    deleteConfirmation.trim().toLowerCase() !== deletingWs.slug
                  }
                >
                  {submitting ? "Deleting..." : "Permanently Delete"}
                </Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

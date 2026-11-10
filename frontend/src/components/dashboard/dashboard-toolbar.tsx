"use client";

import * as React from "react";
import {
  LayoutDashboard,
  Plus,
  RefreshCw,
  Copy,
  Trash2,
  Edit2,
  X,
  Lock,
  Unlock,
  Save,
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
import { Dashboard } from "./dashboard-types";

interface DashboardToolbarProps {
  dashboards: Dashboard[];
  activeDashboard: Dashboard | null;
  isEditMode: boolean;
  isDirty: boolean;
  loading: boolean;
  onSelectDashboard: (dashboard: Dashboard) => void;
  onToggleEditMode: () => void;
  onSaveChanges: () => void;
  onCancelChanges: () => void;
  onNewDashboard: () => void;
  onAddWidget: () => void;
  onDuplicateDashboard: () => void;
  onDeleteDashboard: () => void;
  onRefreshData: () => void;
  onUpdateMetadata: (title: string, description?: string) => void;
}

export function DashboardToolbar({
  dashboards,
  activeDashboard,
  isEditMode,
  isDirty,
  loading,
  onSelectDashboard,
  onToggleEditMode,
  onSaveChanges,
  onCancelChanges,
  onNewDashboard,
  onAddWidget,
  onDuplicateDashboard,
  onDeleteDashboard,
  onRefreshData,
  onUpdateMetadata,
}: DashboardToolbarProps) {
  const [metaDialogOpen, setMetaDialogOpen] = React.useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = React.useState(false);
  const [editTitle, setEditTitle] = React.useState("");
  const [editDesc, setEditDesc] = React.useState("");

  const handleOpenMetaDialog = () => {
    setEditTitle(activeDashboard?.title || "");
    setEditDesc(activeDashboard?.description || "");
    setMetaDialogOpen(true);
  };

  const handleSaveMetadata = (e: React.FormEvent) => {
    e.preventDefault();
    if (!editTitle.trim()) return;
    onUpdateMetadata(editTitle.trim(), editDesc.trim());
    setMetaDialogOpen(false);
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Top Header Row */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <LayoutDashboard className="h-6 w-6 text-primary" />
            Analytics Dashboards
          </h1>
          <p className="text-sm text-muted-foreground">
            Enterprise BI Dashboard Studio with 12-column responsive layout and global filtering.
          </p>
        </div>

        {/* Global Toolbar Actions */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Dashboard Selector */}
          {dashboards.length > 0 && (
            <div className="relative">
              <select
                aria-label="Select Active Dashboard"
                className="text-sm border rounded-md p-1.5 bg-background pr-6 font-medium"
                value={activeDashboard?.id || ""}
                onChange={(e) => {
                  const found = dashboards.find((d) => d.id === e.target.value);
                  if (found) onSelectDashboard(found);
                }}
              >
                {dashboards.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.title} {d.is_shared ? "" : "(Private)"}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* New Dashboard */}
          <Button variant="outline" size="sm" onClick={onNewDashboard}>
            <Plus className="h-4 w-4 mr-1.5" />
            New Dashboard
          </Button>

          {/* Refresh Data */}
          <Button
            variant="outline"
            size="sm"
            onClick={onRefreshData}
            disabled={loading}
            title="Refresh analytical queries"
          >
            <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>

          {/* Edit / View Mode Toggle */}
          {activeDashboard && (
            <Button
              variant={isEditMode ? "secondary" : "default"}
              size="sm"
              onClick={onToggleEditMode}
              className="gap-1.5 font-medium"
            >
              {isEditMode ? (
                <>
                  <Unlock className="h-4 w-4 text-primary" />
                  Exit Edit Mode
                </>
              ) : (
                <>
                  <Lock className="h-4 w-4" />
                  Edit Dashboard
                </>
              )}
            </Button>
          )}
        </div>
      </div>

      {/* Active Dashboard Bar & Studio Controls */}
      {activeDashboard && (
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between p-3.5 rounded-lg border bg-card/60 gap-3">
          <div className="flex items-center gap-2">
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-bold">{activeDashboard.title}</h2>
                {isEditMode && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6 text-muted-foreground hover:text-foreground"
                    title="Edit dashboard title and description"
                    onClick={handleOpenMetaDialog}
                  >
                    <Edit2 className="h-3.5 w-3.5" />
                  </Button>
                )}
                {isDirty && (
                  <Badge variant="outline" className="border-amber-500/40 text-amber-600 dark:text-amber-400 text-[10px]">
                    Unsaved Changes
                  </Badge>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                {activeDashboard.description || "Interactive multi-series analytical visualization grid."}
              </p>
            </div>
          </div>

          {/* Edit Mode Bar Buttons */}
          <div className="flex flex-wrap items-center gap-2">
            {isEditMode ? (
              <>
                <Button size="sm" onClick={onAddWidget} className="gap-1.5 text-xs h-8">
                  <Plus className="h-3.5 w-3.5" />
                  Add Widget
                </Button>

                <Button
                  size="sm"
                  variant="default"
                  onClick={onSaveChanges}
                  disabled={!isDirty}
                  className="gap-1.5 text-xs h-8 bg-emerald-600 hover:bg-emerald-700 text-white"
                >
                  <Save className="h-3.5 w-3.5" />
                  Save Changes
                </Button>

                <Button
                  size="sm"
                  variant="outline"
                  onClick={onCancelChanges}
                  className="gap-1.5 text-xs h-8"
                >
                  <X className="h-3.5 w-3.5" />
                  Cancel
                </Button>
              </>
            ) : (
              <>
                {/* Duplicate Dashboard */}
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1 text-xs h-8"
                  onClick={onDuplicateDashboard}
                  title="Clone this dashboard with all its widgets"
                >
                  <Copy className="h-3.5 w-3.5 mr-1" />
                  Duplicate
                </Button>

                {/* Delete Dashboard */}
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1 text-xs h-8 text-destructive hover:bg-destructive/10 hover:text-destructive"
                  onClick={() => setDeleteConfirmOpen(true)}
                  title="Delete dashboard permanently"
                >
                  <Trash2 className="h-3.5 w-3.5 mr-1" />
                  Delete
                </Button>
              </>
            )}
          </div>
        </div>
      )}

      {/* Edit Metadata Modal */}
      <Dialog open={metaDialogOpen} onOpenChange={setMetaDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Edit Dashboard Metadata</DialogTitle>
            <DialogDescription>
              Update the title and description for this analytical canvas.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleSaveMetadata} className="space-y-4">
            <div>
              <label className="text-xs font-semibold">Dashboard Title</label>
              <Input
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                required
              />
            </div>
            <div>
              <label className="text-xs font-semibold">Description (Optional)</label>
              <Input
                value={editDesc}
                onChange={(e) => setEditDesc(e.target.value)}
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setMetaDialogOpen(false)}>
                Cancel
              </Button>
              <Button type="submit">Update Details</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Modal */}
      <Dialog open={deleteConfirmOpen} onOpenChange={setDeleteConfirmOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-destructive flex items-center gap-2">
              <Trash2 className="h-5 w-5" />
              Delete Dashboard
            </DialogTitle>
            <DialogDescription>
              Are you sure you want to permanently delete &quot;{activeDashboard?.title}&quot;?
              All child visualization widgets will be removed. This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setDeleteConfirmOpen(false)}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                setDeleteConfirmOpen(false);
                onDeleteDashboard();
              }}
            >
              Delete Permanently
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

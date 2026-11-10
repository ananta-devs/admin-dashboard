"use client";

import * as React from "react";
import { Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
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

interface DashboardDuplicateDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  dashboard: Dashboard | null;
  onConfirmDuplicate: (title: string) => Promise<void>;
}

function DuplicateForm({
  dashboard,
  onConfirmDuplicate,
  onCancel,
}: {
  dashboard: Dashboard | null;
  onConfirmDuplicate: (title: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [duplicateTitle, setDuplicateTitle] = React.useState(
    dashboard ? `${dashboard.title} (Copy)` : ""
  );
  const [submitting, setSubmitting] = React.useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!duplicateTitle.trim() || submitting) return;
    setSubmitting(true);
    try {
      await onConfirmDuplicate(duplicateTitle.trim());
      onCancel();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="text-xs font-semibold">New Dashboard Title</label>
        <Input
          value={duplicateTitle}
          onChange={(e) => setDuplicateTitle(e.target.value)}
          placeholder="e.g. Sales Performance (Q2 Copy)"
          required
        />
      </div>

      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          disabled={submitting}
          onClick={onCancel}
        >
          Cancel
        </Button>
        <Button type="submit" disabled={submitting}>
          {submitting ? "Duplicating..." : "Duplicate Dashboard"}
        </Button>
      </DialogFooter>
    </form>
  );
}

export function DashboardDuplicateDialog({
  open,
  onOpenChange,
  dashboard,
  onConfirmDuplicate,
}: DashboardDuplicateDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Copy className="h-5 w-5 text-primary" />
            Duplicate Dashboard
          </DialogTitle>
          <DialogDescription>
            Create an independent clone of &quot;{dashboard?.title}&quot; with all child visualization widgets, layouts, and filter settings.
          </DialogDescription>
        </DialogHeader>

        {open && (
          <DuplicateForm
            key={dashboard?.id || "dup-active"}
            dashboard={dashboard}
            onConfirmDuplicate={onConfirmDuplicate}
            onCancel={() => onOpenChange(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

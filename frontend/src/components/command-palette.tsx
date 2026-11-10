"use client";

import * as React from "react";
import { useSpaRouter } from "@/components/providers/spa-router-provider";
import {
  Settings,
  User,
  LayoutDashboard,
  Table2,
  BarChart3,
  Search,
  Layers,
  FileText,
  FileSpreadsheet,
} from "lucide-react";
import { Command } from "cmdk";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const router = useSpaRouter();

  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ignore if typing in form inputs, textareas, or contenteditables
      const target = e.target;
      if (
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable ||
          target.getAttribute("contenteditable") === "true")
      ) {
        return;
      }

      if (open && e.key === "Escape") {
        e.preventDefault();
        onOpenChange(false);
        return;
      }

      if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        onOpenChange(!open);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onOpenChange]);

  const executeAction = (action: () => void) => {
    onOpenChange(false);
    action();
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="p-0 max-w-lg overflow-hidden border-[var(--border)] bg-[var(--surface)] text-[var(--foreground)] shadow-2xl">
        <DialogTitle className="sr-only">Command Menu</DialogTitle>
        <DialogDescription className="sr-only">
          Search navigation, actions, and features
        </DialogDescription>
        <Command className="flex flex-col w-full h-full">
          <div className="flex items-center px-3 border-b border-[var(--border)]">
            <Search className="w-4 h-4 mr-2.5 text-[var(--muted-foreground)] shrink-0" />
            <Command.Input
              placeholder="Type a command or search platform..."
              className="w-full py-3 text-xs bg-transparent outline-none placeholder:text-[var(--muted-foreground)] text-[var(--foreground)]"
            />
          </div>
          <Command.List className="max-h-80 overflow-y-auto p-2 space-y-1 text-xs">
            <Command.Empty className="py-6 text-center text-xs text-[var(--muted-foreground)]">
              No matching commands found.
            </Command.Empty>

            <Command.Group
              heading="Active Navigation"
              className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--muted-foreground)]"
            >
              <Command.Item
                onSelect={() => executeAction(() => router.push("/"))}
                className="flex items-center gap-2.5 px-2.5 py-1.5 rounded-md cursor-pointer hover:bg-[var(--muted)] text-[var(--foreground)] text-xs select-none aria-selected:bg-[var(--accent)] aria-selected:text-[var(--accent-foreground)]"
              >
                <LayoutDashboard className="w-4 h-4 text-indigo-500 shrink-0" />
                <span className="flex-1">Dashboard Overview</span>
                <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-mono">Live</span>
              </Command.Item>
            </Command.Group>

            <Command.Group
              heading="Planned Platform Modules (Phase 5 - 9)"
              className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--muted-foreground)] pt-2"
            >
              <Command.Item
                onSelect={() => executeAction(() => router.push("/spreadsheets"))}
                className="flex items-center gap-2.5 px-2.5 py-1.5 rounded-md cursor-pointer hover:bg-[var(--muted)] text-[var(--foreground)] text-xs select-none aria-selected:bg-[var(--accent)] aria-selected:text-[var(--accent-foreground)]"
              >
                <Table2 className="w-4 h-4 text-emerald-500 shrink-0" />
                <span className="flex-1">Spreadsheet Engine</span>
                <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-mono">Live</span>
              </Command.Item>
              <Command.Item
                onSelect={() => executeAction(() => router.push("/workspaces"))}
                className="flex items-center gap-2.5 px-2.5 py-1.5 rounded-md cursor-pointer hover:bg-[var(--muted)] text-[var(--foreground)] text-xs select-none aria-selected:bg-[var(--accent)] aria-selected:text-[var(--accent-foreground)]"
              >
                <Layers className="w-4 h-4 text-purple-500 shrink-0" />
                <span className="flex-1">Workspace Manager</span>
                <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-mono">Live</span>
              </Command.Item>
              <Command.Item
                onSelect={() => executeAction(() => router.push("/analytics"))}
                className="flex items-center gap-2.5 px-2.5 py-1.5 rounded-md cursor-pointer hover:bg-[var(--muted)] text-[var(--foreground)] text-xs select-none aria-selected:bg-[var(--accent)] aria-selected:text-[var(--accent-foreground)]"
              >
                <BarChart3 className="w-4 h-4 text-amber-500 shrink-0" />
                <span className="flex-1">Analytics Engine (Polars & Celery)</span>
                <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-mono">Live</span>
              </Command.Item>
              <Command.Item
                onSelect={() => executeAction(() => router.push("/integrations"))}
                className="flex items-center gap-2.5 px-2.5 py-1.5 rounded-md cursor-pointer hover:bg-[var(--muted)] text-[var(--foreground)] text-xs select-none aria-selected:bg-[var(--accent)] aria-selected:text-[var(--accent-foreground)]"
              >
                <FileSpreadsheet className="w-4 h-4 text-emerald-600 shrink-0" />
                <span className="flex-1">Google Sheets Sync</span>
                <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-mono">Live</span>
              </Command.Item>
              <Command.Item
                onSelect={() => executeAction(() => router.push("/reports"))}
                className="flex items-center gap-2.5 px-2.5 py-1.5 rounded-md cursor-pointer hover:bg-[var(--muted)] text-[var(--foreground)] text-xs select-none aria-selected:bg-[var(--accent)] aria-selected:text-[var(--accent-foreground)]"
              >
                <FileText className="w-4 h-4 text-blue-500 shrink-0" />
                <span className="flex-1">Executive Reports</span>
                <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-mono">Live</span>
              </Command.Item>
            </Command.Group>

            <Command.Group
              heading="Administration & Governance (Phase 4 - 5)"
              className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--muted-foreground)] pt-2"
            >
              <Command.Item
                onSelect={() => executeAction(() => router.push("/users"))}
                className="flex items-center gap-2.5 px-2.5 py-1.5 rounded-md cursor-pointer hover:bg-[var(--muted)] text-[var(--foreground)] text-xs select-none aria-selected:bg-[var(--accent)] aria-selected:text-[var(--accent-foreground)]"
              >
                <User className="w-4 h-4 text-indigo-500 shrink-0" />
                <span className="flex-1">User Management & Permissions</span>
                <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-mono">Live</span>
              </Command.Item>
              <Command.Item
                onSelect={() => executeAction(() => router.push("/settings"))}
                className="flex items-center gap-2.5 px-2.5 py-1.5 rounded-md cursor-pointer hover:bg-[var(--muted)] text-[var(--foreground)] text-xs select-none aria-selected:bg-[var(--accent)] aria-selected:text-[var(--accent-foreground)]"
              >
                <Settings className="w-4 h-4 text-slate-500 shrink-0" />
                <span className="flex-1">Organization Settings</span>
                <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-mono">Live</span>
              </Command.Item>
            </Command.Group>
          </Command.List>
        </Command>
      </DialogContent>
    </Dialog>
  );
}

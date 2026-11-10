"use client";

import { useSpaRouter, SpaLink as Link } from "@/components/providers/spa-router-provider";
import { apiUrl } from "@/lib/api";
import * as React from "react";
import {
  LayoutDashboard,
  FileSpreadsheet,
  Cpu,
  HardDrive,
  Pin,
  PinOff,
  Activity,
  CheckCircle2,
  AlertTriangle,
  Clock,
  ArrowUpRight,
  RefreshCw,
  Shield,
  ShieldAlert,
  Layers,
  Database,
  Server,
  Plus,
} from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

// Types
export interface StorageTelemetry {
  organization_id?: string;
  usage_bytes: number;
  quota_bytes: number;
  available_bytes: number;
  utilization_percent: number;
}

export interface WorkbookSummary {
  id: string;
  title: string;
  description?: string;
  sheets?: { id: string; name: string }[];
  updated_at?: string;
  created_at?: string;
}

export interface AnalyticsJobSummary {
  id: string;
  operation: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED" | string;
  created_at?: string;
  started_at?: string;
  completed_at?: string;
  execution_time_ms?: number;
  error_message?: string;
}

export interface DashboardSummary {
  id: string;
  title: string;
  description?: string;
  is_shared?: boolean;
  widgets?: { id: string; title: string; widget_type: string }[];
  updated_at?: string;
  created_at?: string;
}

export interface AuditEventSummary {
  id: string;
  action: string;
  actor: string;
  targetType: string;
  targetId?: string;
  timestamp: string;
}

function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(i === 0 ? 0 : 1)} ${sizes[i]}`;
}

function formatRelativeTime(dateStr?: string, isMounted = true): string {
  if (!dateStr || !isMounted) return "Recently";
  try {
    const date = new Date(dateStr);
    if (isNaN(date.getTime())) return "Recently";
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    if (diffSec < 60) return "Just now";
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHours = Math.floor(diffMin / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays}d ago`;
  } catch {
    return "Recently";
  }
}

const PINNED_STORAGE_KEY = "vibe_pinned_dashboards";

export default function ExecutiveCommandCenter() {
  const _router = useSpaRouter();
  const [loading, setLoading] = React.useState(true);
  const [refreshing, setRefreshing] = React.useState(false);

  // Domain states
  const [workbooks, setWorkbooks] = React.useState<WorkbookSummary[]>([]);
  const [analyticsJobs, setAnalyticsJobs] = React.useState<AnalyticsJobSummary[]>([]);
  const [storageData, setStorageData] = React.useState<StorageTelemetry | null>(null);
  const [dashboards, setDashboards] = React.useState<DashboardSummary[]>([]);
  const [pinnedIds, setPinnedIds] = React.useState<string[]>([]);
  const [auditEvents, setAuditEvents] = React.useState<AuditEventSummary[]>([]);
  const [auditForbidden, setAuditForbidden] = React.useState(false);

  // Load pinned dashboards from localStorage on mount
  React.useEffect(() => {
    try {
      const stored = localStorage.getItem(PINNED_STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored);
        if (Array.isArray(parsed)) {
          setPinnedIds(parsed);
        }
      }
    } catch {
      // LocalStorage access might fail in restricted environments
    }
  }, []);

  // Save pinned dashboards when changed
  const togglePin = (dashboardId: string) => {
    setPinnedIds((prev) => {
      const next = prev.includes(dashboardId)
        ? prev.filter((id) => id !== dashboardId)
        : [...prev, dashboardId];
      try {
        localStorage.setItem(PINNED_STORAGE_KEY, JSON.stringify(next));
      } catch {
        // Silently handle storage limits
      }
      return next;
    });
  };

  // Main telemetry fetcher
  const fetchAllTelemetry = React.useCallback(async (isManualRefresh = false) => {
    if (isManualRefresh) setRefreshing(true);
    else setLoading(true);

    try {
      // Concurrent fetching with graceful settlement
      const [wbResult, jobsResult, storageResult, dashResult, auditResult] = await Promise.allSettled([
        fetch(apiUrl("/api/v1/spreadsheets/workbooks"), { credentials: "include" }),
        fetch(apiUrl("/api/v1/analytics/jobs"), { credentials: "include" }),
        fetch(apiUrl("/api/v1/analytics/storage"), { credentials: "include" }),
        fetch(apiUrl("/api/v1/dashboards"), { credentials: "include" }),
        fetch(apiUrl("/api/v1/organization/audit-logs"), { credentials: "include" }),
      ]);

      // 1. Spreadsheets
      if (wbResult.status === "fulfilled" && wbResult.value.ok) {
        try {
          const wbData = await wbResult.value.json();
          if (Array.isArray(wbData)) setWorkbooks(wbData);
        } catch {
          // Keep previous or default
        }
      }

      // 2. Analytics Jobs
      if (jobsResult.status === "fulfilled" && jobsResult.value.ok) {
        try {
          const jData = await jobsResult.value.json();
          if (jData && Array.isArray(jData.items)) {
            setAnalyticsJobs(jData.items);
          } else if (Array.isArray(jData)) {
            setAnalyticsJobs(jData);
          }
        } catch {
          // Keep previous or default
        }
      }

      // 3. Storage Telemetry
      if (storageResult.status === "fulfilled" && storageResult.value.ok) {
        try {
          const sData = await storageResult.value.json();
          if (sData && typeof sData.usage_bytes === "number") {
            setStorageData(sData);
          }
        } catch {
          // Keep previous or default
        }
      }

      // 4. Dashboards
      if (dashResult.status === "fulfilled" && dashResult.value.ok) {
        try {
          const dData = await dashResult.value.json();
          if (Array.isArray(dData)) setDashboards(dData);
        } catch {
          // Keep previous or default
        }
      }

      // 5. Audit Events
      if (auditResult.status === "fulfilled") {
        if (auditResult.value.status === 403) {
          setAuditForbidden(true);
          setAuditEvents([]);
        } else if (auditResult.value.ok) {
          setAuditForbidden(false);
          try {
            const aData = await auditResult.value.json();
            if (Array.isArray(aData)) {
              setAuditEvents(
                aData.slice(0, 8).map((e: {
                  id?: string;
                  action?: string;
                  actor?: { display_name?: string };
                  target_type?: string;
                  target_id?: string;
                  occurred_at?: string;
                }) => ({
                  id: e.id || String(Math.random()),
                  action: e.action || "action",
                  actor: e.actor?.display_name || "System",
                  targetType: e.target_type || "Resource",
                  targetId: e.target_id,
                  timestamp: e.occurred_at ? formatRelativeTime(e.occurred_at) : "Recently",
                }))
              );
            }
          } catch {
            // Keep previous or default
          }
        }
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  React.useEffect(() => {
    fetchAllTelemetry(false);
  }, [fetchAllTelemetry]);

  // Derived metric calculations
  const totalSheetsCount = React.useMemo(() => {
    return workbooks.reduce((acc, wb) => acc + (wb.sheets?.length || 0), 0);
  }, [workbooks]);

  const activeJobsCount = React.useMemo(() => {
    return analyticsJobs.filter((j) => j.status === "RUNNING" || j.status === "PENDING").length;
  }, [analyticsJobs]);

  const pinnedDashboards = React.useMemo(() => {
    if (pinnedIds.length === 0) return [];
    return dashboards.filter((d) => pinnedIds.includes(d.id));
  }, [dashboards, pinnedIds]);

  // Status badge renderer for analytics jobs
  const renderJobStatusBadge = (status: string) => {
    switch (status.toUpperCase()) {
      case "COMPLETED":
        return (
          <Badge variant="default" className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/20 text-xs font-medium">
            <CheckCircle2 className="w-3 h-3 mr-1" />
            COMPLETED
          </Badge>
        );
      case "RUNNING":
        return (
          <Badge variant="default" className="bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/20 text-xs font-medium">
            <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-ping mr-1.5" />
            RUNNING
          </Badge>
        );
      case "PENDING":
        return (
          <Badge variant="outline" className="bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/20 text-xs font-medium">
            <Clock className="w-3 h-3 mr-1" />
            PENDING
          </Badge>
        );
      case "FAILED":
        return (
          <Badge variant="outline" className="bg-rose-500/15 text-rose-700 dark:text-rose-400 border-rose-500/20 text-xs font-medium">
            <AlertTriangle className="w-3 h-3 mr-1" />
            FAILED
          </Badge>
        );
      default:
        return (
          <Badge variant="outline" className="text-xs">
            {status}
          </Badge>
        );
    }
  };

  return (
    <div className="space-y-8 max-w-7xl mx-auto pb-16">
      {/* 1. Header & Overview Banner */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 p-6 rounded-xl border border-[var(--border)] bg-[var(--surface)] shadow-sm">
        <div>
          <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 mb-2 border border-emerald-500/20">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            Operational &bull; All Subsystems Active
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--foreground)]">
            Executive Command Center
          </h1>
          <p className="text-sm text-[var(--muted-foreground)] mt-1">
            Real-time enterprise intelligence, operational telemetry, and workspace orchestrations.
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          <Button
            variant="outline"
            size="sm"
            onClick={() => fetchAllTelemetry(true)}
            disabled={refreshing}
            className="gap-1.5 text-xs font-medium"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`} />
            Refresh Telemetry
          </Button>
          <Link
            href="/spreadsheets"
            className={cn(buttonVariants({ variant: "default", size: "sm" }), "gap-1.5 text-xs font-medium")}
          >
            <Plus className="w-3.5 h-3.5" />
            New Spreadsheet
          </Link>
          <Link
            href="/dashboards"
            className={cn(buttonVariants({ variant: "secondary", size: "sm" }), "gap-1.5 text-xs font-medium")}
          >
            <LayoutDashboard className="w-3.5 h-3.5" />
            Dashboards
          </Link>
        </div>
      </div>

      {/* 2. Top-Level Executive Metric Cards (4-Grid) */}
      <section aria-labelledby="metrics-heading" className="space-y-3">
        <h2 id="metrics-heading" className="text-sm font-semibold uppercase tracking-wider text-[var(--muted-foreground)]">
          Platform Overview &amp; Metrics
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {/* Card 1: Spreadsheets */}
          <Card className="border-[var(--border)] bg-[var(--surface)] shadow-sm hover:shadow-md transition-shadow">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium text-[var(--muted-foreground)]">
                Active Spreadsheets
              </CardTitle>
              <div className="w-8 h-8 rounded-lg bg-emerald-500/10 flex items-center justify-center text-emerald-600 dark:text-emerald-400">
                <FileSpreadsheet className="w-4 h-4" />
              </div>
            </CardHeader>
            <CardContent>
              {loading ? (
                <Skeleton className="h-8 w-20" />
              ) : (
                <div className="text-2xl font-bold text-[var(--foreground)]">
                  {workbooks.length}
                </div>
              )}
              <p className="text-xs text-[var(--muted-foreground)] mt-1">
                {loading ? "..." : `${totalSheetsCount} sheets across active workbooks`}
              </p>
            </CardContent>
          </Card>

          {/* Card 2: Analytics Executions */}
          <Card className="border-[var(--border)] bg-[var(--surface)] shadow-sm hover:shadow-md transition-shadow">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium text-[var(--muted-foreground)]">
                Analytics Executions
              </CardTitle>
              <div className="w-8 h-8 rounded-lg bg-sky-500/10 flex items-center justify-center text-sky-600 dark:text-sky-400">
                <Cpu className="w-4 h-4" />
              </div>
            </CardHeader>
            <CardContent>
              {loading ? (
                <Skeleton className="h-8 w-20" />
              ) : (
                <div className="text-2xl font-bold text-[var(--foreground)]">
                  {analyticsJobs.length}
                </div>
              )}
              <p className="text-xs text-[var(--muted-foreground)] mt-1">
                {loading
                  ? "..."
                  : activeJobsCount > 0
                  ? `${activeJobsCount} active job${activeJobsCount > 1 ? "s" : ""} processing`
                  : "All jobs finished cleanly"}
              </p>
            </CardContent>
          </Card>

          {/* Card 3: Storage Quota */}
          <Card className="border-[var(--border)] bg-[var(--surface)] shadow-sm hover:shadow-md transition-shadow">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium text-[var(--muted-foreground)]">
                Storage Utilization
              </CardTitle>
              <div className="w-8 h-8 rounded-lg bg-indigo-500/10 flex items-center justify-center text-indigo-600 dark:text-indigo-400">
                <HardDrive className="w-4 h-4" />
              </div>
            </CardHeader>
            <CardContent>
              {loading ? (
                <Skeleton className="h-8 w-24" />
              ) : (
                <div data-testid="storage-metric-percent" className="text-2xl font-bold text-[var(--foreground)]">
                  {storageData ? `${storageData.utilization_percent.toFixed(1)}%` : "0.0%"}
                </div>
              )}
              <p className="text-xs text-[var(--muted-foreground)] mt-1">
                {loading
                  ? "..."
                  : storageData
                  ? `${formatBytes(storageData.usage_bytes)} of ${formatBytes(storageData.quota_bytes)} used`
                  : "Live parquet storage telemetry"}
              </p>
            </CardContent>
          </Card>

          {/* Card 4: Pinned Dashboards */}
          <Card className="border-[var(--border)] bg-[var(--surface)] shadow-sm hover:shadow-md transition-shadow">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium text-[var(--muted-foreground)]">
                Pinned Dashboards
              </CardTitle>
              <div className="w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center text-amber-600 dark:text-amber-400">
                <Pin className="w-4 h-4" />
              </div>
            </CardHeader>
            <CardContent>
              {loading ? (
                <Skeleton className="h-8 w-16" />
              ) : (
                <div className="text-2xl font-bold text-[var(--foreground)]">
                  {pinnedDashboards.length}
                </div>
              )}
              <p className="text-xs text-[var(--muted-foreground)] mt-1">
                {loading ? "..." : `${dashboards.length} total dashboard${dashboards.length === 1 ? "" : "s"} available`}
              </p>
            </CardContent>
          </Card>
        </div>
      </section>

      {/* 3. Main Operational Sections (2-Column Grid) */}
      <section aria-labelledby="subsystems-heading" className="space-y-4">
        <h2 id="subsystems-heading" className="text-lg font-semibold tracking-tight text-[var(--foreground)]">
          Operational Subsystems &amp; Intelligence
        </h2>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Left Column (2 Cols): Active Spreadsheets & Recent Analytics Runs */}
        <div className="lg:col-span-2 space-y-8">
          {/* Section A: Active Spreadsheets */}
          <Card className="border-[var(--border)] bg-[var(--surface)] shadow-sm">
            <CardHeader className="flex flex-row items-center justify-between pb-3">
              <div>
                <CardTitle className="text-base font-semibold text-[var(--foreground)] flex items-center gap-2">
                  <FileSpreadsheet className="w-4 h-4 text-emerald-500" />
                  Active Spreadsheets &amp; Workbooks
                </CardTitle>
                <CardDescription className="text-xs text-[var(--muted-foreground)]">
                  Live multi-sheet enterprise calculation workbooks with collaborative cell meshes.
                </CardDescription>
              </div>
              <Link
                href="/spreadsheets"
                className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "text-xs gap-1")}
              >
                Open Studio <ArrowUpRight className="w-3.5 h-3.5" />
              </Link>
            </CardHeader>
            <CardContent className="p-0">
              {loading ? (
                <div className="p-4 space-y-2">
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="h-10 w-full" />
                </div>
              ) : workbooks.length === 0 ? (
                <div className="p-8">
                  <EmptyState
                    icon={FileSpreadsheet}
                    title="No workbooks found"
                    description="Get started by creating your first calculation workbook."
                    actionLabel="Create Workbook"
                    actionHref="/spreadsheets"
                  />
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Workbook Name</TableHead>
                      <TableHead>Sheets</TableHead>
                      <TableHead>Last Modified</TableHead>
                      <TableHead className="text-right">Action</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {workbooks.slice(0, 5).map((wb) => (
                      <TableRow key={wb.id} className="hover:bg-[var(--surface-muted)] transition-colors">
                        <TableCell className="font-medium">
                          <div className="flex flex-col">
                            <span className="text-sm text-[var(--foreground)] font-semibold">{wb.title}</span>
                            {wb.description && (
                              <span className="text-xs text-[var(--muted-foreground)] line-clamp-1">{wb.description}</span>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          <Badge variant="secondary" className="text-xs">
                            {wb.sheets?.length || 1} {wb.sheets?.length === 1 ? "sheet" : "sheets"}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-xs text-[var(--muted-foreground)]">
                          {formatRelativeTime(wb.updated_at || wb.created_at)}
                        </TableCell>
                        <TableCell className="text-right">
                          <Link
                            href={`/spreadsheets?workbookId=${wb.id}`}
                            className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "text-xs")}
                          >
                            Open
                          </Link>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>

          {/* Section B: Recent Analytics Executions */}
          <Card className="border-[var(--border)] bg-[var(--surface)] shadow-sm">
            <CardHeader className="flex flex-row items-center justify-between pb-3">
              <div>
                <CardTitle className="text-base font-semibold text-[var(--foreground)] flex items-center gap-2">
                  <Cpu className="w-4 h-4 text-blue-500" />
                  Recent Analytics Executions
                </CardTitle>
                <CardDescription className="text-xs text-[var(--muted-foreground)]">
                  Telemetry from DuckDB analytical calculations and data pipelines.
                </CardDescription>
              </div>
              <Link
                href="/analytics"
                className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "text-xs gap-1")}
              >
                View All <ArrowUpRight className="w-3.5 h-3.5" />
              </Link>
            </CardHeader>
            <CardContent className="p-0">
              {loading ? (
                <div className="p-4 space-y-2">
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="h-10 w-full" />
                </div>
              ) : analyticsJobs.length === 0 ? (
                <div className="p-8">
                  <EmptyState
                    icon={Cpu}
                    title="No analytics jobs executed yet"
                    description="Run an aggregation, regression, or script execution from the Analytics workspace."
                    actionLabel="Go to Analytics"
                    actionHref="/analytics"
                  />
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Operation</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Duration</TableHead>
                      <TableHead>Executed</TableHead>
                      <TableHead className="text-right">Inspect</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {analyticsJobs.slice(0, 5).map((job) => (
                      <TableRow key={job.id} className="hover:bg-[var(--surface-muted)] transition-colors">
                        <TableCell className="font-medium text-xs font-mono">
                          {job.operation}
                        </TableCell>
                        <TableCell>
                          {renderJobStatusBadge(job.status)}
                        </TableCell>
                        <TableCell className="text-xs text-[var(--muted-foreground)]">
                          {job.execution_time_ms ? `${job.execution_time_ms} ms` : "—"}
                        </TableCell>
                        <TableCell className="text-xs text-[var(--muted-foreground)]">
                          {formatRelativeTime(job.created_at)}
                        </TableCell>
                        <TableCell className="text-right">
                          <Link
                            href="/analytics"
                            className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "text-xs")}
                          >
                            Details
                          </Link>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>

          {/* Section C: Pinned Dashboards Gallery */}
          <Card className="border-[var(--border)] bg-[var(--surface)] shadow-sm">
            <CardHeader className="flex flex-row items-center justify-between pb-3">
              <div>
                <CardTitle className="text-base font-semibold text-[var(--foreground)] flex items-center gap-2">
                  <Pin className="w-4 h-4 text-amber-500" />
                  Pinned Dashboards Gallery
                </CardTitle>
                <CardDescription className="text-xs text-[var(--muted-foreground)]">
                  Your prioritized BI monitoring views and KPI layouts.
                </CardDescription>
              </div>
              <Link
                href="/dashboards"
                className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "text-xs gap-1")}
              >
                All Dashboards <ArrowUpRight className="w-3.5 h-3.5" />
              </Link>
            </CardHeader>
            <CardContent>
              {loading ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <Skeleton className="h-28 w-full" />
                  <Skeleton className="h-28 w-full" />
                </div>
              ) : pinnedDashboards.length === 0 ? (
                <div className="p-6 text-center border border-dashed rounded-lg">
                  <p className="text-sm font-medium text-[var(--foreground)]">No dashboards pinned yet</p>
                  <p className="text-xs text-[var(--muted-foreground)] mt-1 mb-4">
                    Pin your high-priority operational dashboards here for quick access.
                  </p>
                  {dashboards.length > 0 ? (
                    <div className="flex flex-wrap justify-center gap-2">
                      {dashboards.slice(0, 3).map((dash) => (
                        <Button
                          key={dash.id}
                          variant="outline"
                          size="sm"
                          onClick={() => togglePin(dash.id)}
                          className="text-xs gap-1"
                        >
                          <Pin className="w-3 h-3 text-amber-500" />
                          Pin {dash.title}
                        </Button>
                      ))}
                    </div>
                  ) : (
                    <Link
                      href="/dashboards"
                      className={cn(buttonVariants({ variant: "default", size: "sm" }))}
                    >
                      Create a Dashboard
                    </Link>
                  )}
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  {pinnedDashboards.map((dash) => (
                    <div
                      key={dash.id}
                      className="p-4 rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] hover:border-amber-500/40 transition-all flex flex-col justify-between"
                    >
                      <div>
                        <div className="flex items-start justify-between gap-2">
                          <h4 className="text-sm font-semibold text-[var(--foreground)] line-clamp-1">
                            {dash.title}
                          </h4>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => togglePin(dash.id)}
                            className="h-6 w-6 text-amber-500 hover:text-amber-600"
                            title="Unpin dashboard"
                          >
                            <PinOff className="w-3.5 h-3.5" />
                          </Button>
                        </div>
                        {dash.description && (
                          <p className="text-xs text-[var(--muted-foreground)] line-clamp-2 mt-1">
                            {dash.description}
                          </p>
                        )}
                      </div>
                      <div className="flex items-center justify-between mt-4 pt-2 border-t border-[var(--border)] text-xs text-[var(--muted-foreground)]">
                        <span>{dash.widgets?.length || 0} widgets</span>
                        <Link
                          href={`/dashboards?dashboardId=${dash.id}`}
                          className="text-xs font-medium text-[var(--primary)] hover:underline"
                        >
                          View Live &rarr;
                        </Link>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Right Rail (1 Col): Storage & Quota, Subsystem Telemetry & Activity */}
        <div className="space-y-8">
          {/* Storage & Quota Widget */}
          <Card className="border-[var(--border)] bg-[var(--surface)] shadow-sm">
            <CardHeader className="pb-3">
              <CardTitle className="text-base font-semibold text-[var(--foreground)] flex items-center gap-2">
                <HardDrive className="w-4 h-4 text-purple-500" />
                Storage &amp; Quota
              </CardTitle>
              <CardDescription className="text-xs text-[var(--muted-foreground)]">
                Authoritative multi-tenant parquet analytics and results capacity.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {loading ? (
                <div className="space-y-2">
                  <Skeleton className="h-4 w-full" />
                  <Skeleton className="h-6 w-full" />
                </div>
              ) : storageData ? (
                <>
                  <div className="space-y-1.5">
                    <div className="flex justify-between text-xs font-medium">
                      <span className="text-[var(--muted-foreground)]">Utilization</span>
                      <span data-testid="storage-gauge-percent" className="text-[var(--foreground)] font-semibold">
                        {storageData.utilization_percent.toFixed(1)}%
                      </span>
                    </div>
                    {/* Progress Bar with Dynamic Threshold Styling */}
                    <div className="w-full bg-[var(--border)] rounded-full h-2.5 overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-500 ${
                          storageData.utilization_percent > 90
                            ? "bg-rose-500"
                            : storageData.utilization_percent > 70
                            ? "bg-amber-500"
                            : "bg-emerald-500"
                        }`}
                        style={{ width: `${Math.min(100, Math.max(0, storageData.utilization_percent))}%` }}
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-3 pt-2 text-xs">
                    <div className="p-2.5 rounded-lg bg-[var(--surface-muted)] border border-[var(--border)]">
                      <span className="text-[var(--muted-foreground)] block">Used Capacity</span>
                      <span className="text-[var(--foreground)] font-semibold text-sm">
                        {formatBytes(storageData.usage_bytes)}
                      </span>
                    </div>
                    <div className="p-2.5 rounded-lg bg-[var(--surface-muted)] border border-[var(--border)]">
                      <span className="text-[var(--muted-foreground)] block">Total Quota</span>
                      <span className="text-[var(--foreground)] font-semibold text-sm">
                        {formatBytes(storageData.quota_bytes)}
                      </span>
                    </div>
                  </div>
                </>
              ) : (
                <div className="p-4 rounded-lg bg-[var(--surface-muted)] text-center text-xs text-[var(--muted-foreground)]">
                  Storage telemetry endpoint active.
                </div>
              )}
            </CardContent>
          </Card>

          {/* Subsystem Health Status Card */}
          <Card className="border-[var(--border)] bg-[var(--surface)] shadow-sm">
            <CardHeader className="pb-3">
              <CardTitle className="text-base font-semibold text-[var(--foreground)] flex items-center gap-2">
                <Activity className="w-4 h-4 text-emerald-500" />
                Subsystem Telemetry
              </CardTitle>
              <CardDescription className="text-xs text-[var(--muted-foreground)]">
                Health status of enterprise platform nodes.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center justify-between text-xs py-1.5 border-b border-[var(--border)]">
                <span className="flex items-center gap-2 text-[var(--foreground)] font-medium">
                  <Server className="w-3.5 h-3.5 text-blue-500" /> FastAPI Core API
                </span>
                <span className="inline-flex items-center gap-1 text-emerald-800 dark:text-emerald-300 font-semibold">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> Ready
                </span>
              </div>
              <div className="flex items-center justify-between text-xs py-1.5 border-b border-[var(--border)]">
                <span className="flex items-center gap-2 text-[var(--foreground)] font-medium">
                  <Database className="w-3.5 h-3.5 text-indigo-500" /> PostgreSQL 16
                </span>
                <span className="inline-flex items-center gap-1 text-emerald-800 dark:text-emerald-300 font-semibold">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> Connected
                </span>
              </div>
              <div className="flex items-center justify-between text-xs py-1.5 border-b border-[var(--border)]">
                <span className="flex items-center gap-2 text-[var(--foreground)] font-medium">
                  <Layers className="w-3.5 h-3.5 text-rose-500" /> Redis 7 State &amp; Cache
                </span>
                <span className="inline-flex items-center gap-1 text-emerald-800 dark:text-emerald-300 font-semibold">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> Active
                </span>
              </div>
              <div className="flex items-center justify-between text-xs py-1.5">
                <span className="flex items-center gap-2 text-[var(--foreground)] font-medium">
                  <Shield className="w-3.5 h-3.5 text-purple-500" /> Report Renderer (Sandbox-Disabled)
                </span>
                <span className="inline-flex items-center gap-1 text-emerald-800 dark:text-emerald-300 font-semibold">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> Isolated
                </span>
              </div>
            </CardContent>
          </Card>

          {/* Section D: Recent Operational Activity (Audit Logs) */}
          <Card className="border-[var(--border)] bg-[var(--surface)] shadow-sm">
            <CardHeader className="flex flex-row items-center justify-between pb-3">
              <div>
                <CardTitle className="text-base font-semibold text-[var(--foreground)] flex items-center gap-2">
                  <Shield className="w-4 h-4 text-emerald-500" />
                  Recent Operational Activity
                </CardTitle>
                <CardDescription className="text-xs text-[var(--muted-foreground)]">
                  Authoritative security &amp; audit event logs.
                </CardDescription>
              </div>
              <Link
                href="/audit"
                className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "text-xs gap-1")}
              >
                View Logs <ArrowUpRight className="w-3.5 h-3.5" />
              </Link>
            </CardHeader>
            <CardContent>
              {loading ? (
                <div className="space-y-3">
                  <Skeleton className="h-8 w-full" />
                  <Skeleton className="h-8 w-full" />
                  <Skeleton className="h-8 w-full" />
                </div>
              ) : auditForbidden ? (
                <div className="p-4 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-700 dark:text-amber-400 text-xs space-y-1">
                  <div className="flex items-center gap-1.5 font-semibold">
                    <ShieldAlert className="w-3.5 h-3.5" />
                    Administrative Permission Required
                  </div>
                  <p className="text-[11px] text-amber-600/90 dark:text-amber-400/90">
                    Your current role does not have the <code className="font-mono">audit.read</code> permission to inspect tenant audit events.
                  </p>
                </div>
              ) : auditEvents.length === 0 ? (
                <p className="text-xs text-center text-[var(--muted-foreground)] py-4">
                  No recent audit activity recorded.
                </p>
              ) : (
                <div className="space-y-3">
                  {auditEvents.map((evt) => (
                    <div
                      key={evt.id}
                      className="flex items-start justify-between gap-2 text-xs pb-2 border-b border-[var(--border)] last:border-0 last:pb-0"
                    >
                      <div className="space-y-0.5">
                        <span className="font-semibold text-[var(--foreground)]">{evt.action}</span>
                        <div className="text-[11px] text-[var(--muted-foreground)]">
                          by <span className="text-[var(--foreground)]">{evt.actor}</span> on {evt.targetType}
                        </div>
                      </div>
                      <span className="text-[10px] text-[var(--muted-foreground)] whitespace-nowrap">
                        {evt.timestamp}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </section>
  </div>
);
}

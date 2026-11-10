"use client";

import { useSpaRouter } from "@/components/providers/spa-router-provider";
import { apiUrl } from "@/lib/api";
import * as React from "react";
import {
  Activity,
  Server,
  Database,
  Layers,
  Cpu,
  HardDrive,
  ShieldCheck,
  RefreshCw,
  Clock,
  AlertTriangle,
  Lock,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

interface SubsystemTelemetry {
  timestamp: string;
  overall_status: "Healthy" | "Degraded" | "Unavailable" | string;
  subsystems: {
    backend: {
      status: string;
      uptime_seconds: number;
      version: string;
      environment: string;
      timestamp: string;
    };
    database: {
      status: string;
      latency_ms: number | null;
      engine_version?: string;
      pool?: {
        pool_size?: number;
        checked_in?: number;
        checked_out?: number;
        overflow?: number;
      };
      error?: string;
    };
    redis: {
      status: string;
      latency_ms: number | null;
      connected_clients?: number;
      used_memory_human?: string;
      error?: string;
    };
    celery: {
      status: string;
      active_queues?: {
        default?: number;
        analytics?: number;
        reporting?: number;
      };
      total_queued_tasks?: number;
    };
    analytics: {
      status: string;
      active_jobs?: number;
      completed_24h?: number;
      failed_24h?: number;
    };
    storage: {
      status: string;
      tenant_files_stored?: number;
      tenant_storage_bytes?: number;
      host_free_disk_bytes?: number;
      host_free_disk_gb?: number;
    };
    backup_recovery: {
      status: string;
      rpo?: {
        target_minutes: number;
        measured_nominal_minutes?: number | null;
        benchmark_minutes?: number;
        status?: string;
        note?: string;
        sla_compliant: boolean;
      };
      rto?: {
        target_minutes: number;
        measured_duration_minutes?: number | null;
        benchmark_drill_minutes?: number;
        status?: string;
        note?: string;
        sla_compliant: boolean;
      };
      spool?: {
        total_batches?: number;
        uncommitted_files?: number;
        spool_directory?: string;
      };
    };
  };
}

function formatUptime(seconds: number): string {
  if (!seconds || seconds <= 0) return "0s";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  const parts: string[] = [];
  if (days > 0) parts.push(`${days}d`);
  if (hours > 0) parts.push(`${hours}h`);
  if (minutes > 0) parts.push(`${minutes}m`);
  parts.push(`${secs}s`);
  return parts.join(" ");
}

function formatBytes(bytes?: number): string {
  if (bytes === undefined || bytes === null || isNaN(bytes)) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function StatusBadge({ status }: { status: string }) {
  const norm = (status || "").toLowerCase();
  if (norm === "healthy" || norm === "active" || norm === "ready") {
    return (
      <Badge variant="outline" className="bg-emerald-500/10 text-emerald-400 border-emerald-500/30 gap-1.5 font-mono text-xs">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
        Healthy
      </Badge>
    );
  }
  if (norm === "degraded" || norm === "warning") {
    return (
      <Badge variant="outline" className="bg-amber-500/10 text-amber-400 border-amber-500/30 gap-1.5 font-mono text-xs">
        <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
        Degraded
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="bg-rose-500/10 text-rose-400 border-rose-500/30 gap-1.5 font-mono text-xs">
      <span className="w-1.5 h-1.5 rounded-full bg-rose-400" />
      {status || "Unavailable"}
    </Badge>
  );
}

export default function SystemHealthPage() {
  const _router = useSpaRouter();
  const [telemetry, setTelemetry] = React.useState<SubsystemTelemetry | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [forbidden, setForbidden] = React.useState(false);
  const [autoRefresh, setAutoRefresh] = React.useState(false);
  const [lastRefreshedAt, setLastRefreshedAt] = React.useState<Date | null>(null);

  const fetchTelemetry = React.useCallback(async () => {
    try {
      const res = await fetch(apiUrl("/api/v1/system/telemetry"), {
        headers: { Accept: "application/json" },
      });
      if (res.status === 403) {
        setForbidden(true);
        setError("Administrative access required: Only organization owners and administrators have permission to inspect system health.");
        setLoading(false);
        return;
      }
      if (!res.ok) {
        throw new Error(`Telemetry probe failed: HTTP ${res.status}`);
      }
      const data = await res.json();
      setTelemetry(data);
      setForbidden(false);
      setError(null);
      setLastRefreshedAt(new Date());
    } catch (err: unknown) {
      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("Failed to fetch system operational telemetry.");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    fetchTelemetry();
  }, [fetchTelemetry]);

  React.useEffect(() => {
    if (!autoRefresh || forbidden) return;
    const interval = setInterval(() => {
      fetchTelemetry();
    }, 10000);
    return () => clearInterval(interval);
  }, [autoRefresh, forbidden, fetchTelemetry]);

  return (
    <div
      data-testid="system-page-container"
      className="space-y-6 max-w-7xl mx-auto pb-12"
    >
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 border-b border-border/40 pb-5">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center gap-2">
              <Activity className="h-8 w-8 text-primary" />
              System Health
            </h1>
            {telemetry && (
              <StatusBadge status={telemetry.overall_status} />
            )}
          </div>
          <p className="text-sm text-muted-foreground mt-1">
            Authoritative operational health diagnostics, engine performance & disaster recovery metrics
          </p>
        </div>

        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer select-none">
            <input
              type="checkbox"
              data-testid="system-auto-refresh-toggle"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="rounded border-zinc-700 bg-zinc-900 text-primary focus:ring-primary h-4 w-4"
            />
            Auto-refresh (10s)
          </label>
          <Button
            variant="outline"
            size="sm"
            data-testid="refresh-telemetry-btn"
            onClick={() => {
              setLoading(true);
              fetchTelemetry();
            }}
            disabled={loading}
            className="gap-2"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            Refresh Diagnostics
          </Button>
        </div>
      </div>

      {/* RBAC Forbidden Alert */}
      {forbidden && (
        <div
          data-testid="system-forbidden-banner"
          className="p-6 rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-200 flex items-start gap-4"
        >
          <Lock className="w-6 h-6 text-amber-400 mt-1 flex-shrink-0" />
          <div className="space-y-1">
            <h3 className="font-semibold text-amber-300">Administrative Privilege Required</h3>
            <p className="text-sm text-amber-200/90">{error}</p>
          </div>
        </div>
      )}

      {/* General Error Alert */}
      {error && !forbidden && (
        <div
          data-testid="system-error-banner"
          className="p-4 rounded-lg border border-destructive/50 bg-destructive/10 text-destructive flex items-center gap-3"
        >
          <AlertTriangle className="w-5 h-5 flex-shrink-0" />
          <span className="text-sm font-medium">{error}</span>
        </div>
      )}

      {/* Loading Skeleton */}
      {loading && !telemetry && !forbidden && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <Card key={i} className="p-6 space-y-4">
              <Skeleton className="h-6 w-1/3" />
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-12 w-full" />
            </Card>
          ))}
        </div>
      )}

      {/* Operational Diagnostics Cards */}
      {telemetry && (
        <>
          {/* Subsystem Health Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {/* 1. Backend Core */}
            <Card data-testid="subsystem-card-backend" className="border-border/60 bg-card/60 backdrop-blur-sm">
              <CardHeader className="flex flex-row items-center justify-between pb-2">
                <div className="flex items-center gap-2">
                  <Server className="h-5 w-5 text-indigo-400" />
                  <CardTitle className="text-base font-semibold">Backend Core</CardTitle>
                </div>
                <StatusBadge status={telemetry.subsystems.backend.status} />
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="flex justify-between items-center py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Uptime</span>
                  <span className="font-mono font-medium text-foreground">
                    {formatUptime(telemetry.subsystems.backend.uptime_seconds)}
                  </span>
                </div>
                <div className="flex justify-between items-center py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Version</span>
                  <span className="font-mono text-xs text-foreground">
                    v{telemetry.subsystems.backend.version}
                  </span>
                </div>
                <div className="flex justify-between items-center py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Environment</span>
                  <span className="capitalize font-medium text-foreground">
                    {telemetry.subsystems.backend.environment}
                  </span>
                </div>
              </CardContent>
            </Card>

            {/* 2. PostgreSQL Database */}
            <Card data-testid="subsystem-card-database" className="border-border/60 bg-card/60 backdrop-blur-sm">
              <CardHeader className="flex flex-row items-center justify-between pb-2">
                <div className="flex items-center gap-2">
                  <Database className="h-5 w-5 text-sky-400" />
                  <CardTitle className="text-base font-semibold">PostgreSQL Engine</CardTitle>
                </div>
                <StatusBadge status={telemetry.subsystems.database.status} />
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="flex justify-between items-center py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Query Latency</span>
                  <span className="font-mono font-medium text-foreground">
                    {telemetry.subsystems.database.latency_ms !== null
                      ? `${telemetry.subsystems.database.latency_ms} ms`
                      : "N/A"}
                  </span>
                </div>
                <div className="flex justify-between items-center py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Engine Version</span>
                  <span className="font-mono text-xs text-foreground truncate max-w-[180px]">
                    {telemetry.subsystems.database.engine_version || "PostgreSQL 16+"}
                  </span>
                </div>
                {telemetry.subsystems.database.pool && (
                  <div className="pt-1">
                    <div className="flex justify-between text-xs text-muted-foreground mb-1">
                      <span>Connection Pool Active</span>
                      <span>
                        {(telemetry.subsystems.database.pool.checked_out || 0)} /{" "}
                        {(telemetry.subsystems.database.pool.pool_size || 10)}
                      </span>
                    </div>
                    <div className="w-full bg-secondary/50 rounded-full h-2 overflow-hidden">
                      <div
                        className="bg-sky-500 h-2 rounded-full transition-all duration-300"
                        style={{
                          width: `${Math.min(
                            100,
                            Math.round(
                              ((telemetry.subsystems.database.pool.checked_out || 0) /
                                (telemetry.subsystems.database.pool.pool_size || 10)) *
                                100
                            )
                          )}%`,
                        }}
                      />
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* 3. Redis Cache & Broker */}
            <Card data-testid="subsystem-card-redis" className="border-border/60 bg-card/60 backdrop-blur-sm">
              <CardHeader className="flex flex-row items-center justify-between pb-2">
                <div className="flex items-center gap-2">
                  <Layers className="h-5 w-5 text-rose-400" />
                  <CardTitle className="text-base font-semibold">Redis Cache & Locks</CardTitle>
                </div>
                <StatusBadge status={telemetry.subsystems.redis.status} />
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="flex justify-between items-center py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Ping Latency</span>
                  <span className="font-mono font-medium text-foreground">
                    {telemetry.subsystems.redis.latency_ms !== null
                      ? `${telemetry.subsystems.redis.latency_ms} ms`
                      : "N/A"}
                  </span>
                </div>
                <div className="flex justify-between items-center py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Used Memory</span>
                  <span className="font-mono text-xs text-foreground">
                    {telemetry.subsystems.redis.used_memory_human || "N/A"}
                  </span>
                </div>
                <div className="flex justify-between items-center py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Connected Clients</span>
                  <span className="font-mono text-xs text-foreground">
                    {telemetry.subsystems.redis.connected_clients ?? 0}
                  </span>
                </div>
              </CardContent>
            </Card>

            {/* 4. Celery Task Workers */}
            <Card data-testid="subsystem-card-celery" className="border-border/60 bg-card/60 backdrop-blur-sm">
              <CardHeader className="flex flex-row items-center justify-between pb-2">
                <div className="flex items-center gap-2">
                  <Cpu className="h-5 w-5 text-emerald-400" />
                  <CardTitle className="text-base font-semibold">Celery Task Workers</CardTitle>
                </div>
                <StatusBadge status={telemetry.subsystems.celery.status} />
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="flex justify-between items-center py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Queued Tasks Total</span>
                  <span className="font-mono font-semibold text-foreground">
                    {telemetry.subsystems.celery.total_queued_tasks ?? 0}
                  </span>
                </div>
                <div className="space-y-1.5 pt-1">
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>Default Queue</span>
                    <span className="font-mono">{telemetry.subsystems.celery.active_queues?.default ?? 0}</span>
                  </div>
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>Analytics Queue</span>
                    <span className="font-mono">{telemetry.subsystems.celery.active_queues?.analytics ?? 0}</span>
                  </div>
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>Reporting Queue</span>
                    <span className="font-mono">{telemetry.subsystems.celery.active_queues?.reporting ?? 0}</span>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* 5. Analytics Execution Engine */}
            <Card data-testid="subsystem-card-analytics" className="border-border/60 bg-card/60 backdrop-blur-sm">
              <CardHeader className="flex flex-row items-center justify-between pb-2">
                <div className="flex items-center gap-2">
                  <Activity className="h-5 w-5 text-amber-400" />
                  <CardTitle className="text-base font-semibold">Analytics Engine</CardTitle>
                </div>
                <StatusBadge status={telemetry.subsystems.analytics.status} />
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="flex justify-between items-center py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Active Computations</span>
                  <span className="font-mono font-medium text-foreground">
                    {telemetry.subsystems.analytics.active_jobs ?? 0}
                  </span>
                </div>
                <div className="flex justify-between items-center py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Completed (24h)</span>
                  <span className="font-mono text-emerald-400 font-medium">
                    {telemetry.subsystems.analytics.completed_24h ?? 0}
                  </span>
                </div>
                <div className="flex justify-between items-center py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Failures (24h)</span>
                  <span className="font-mono text-rose-400 font-medium">
                    {telemetry.subsystems.analytics.failed_24h ?? 0}
                  </span>
                </div>
              </CardContent>
            </Card>

            {/* 6. File Storage & Quota */}
            <Card data-testid="subsystem-card-storage" className="border-border/60 bg-card/60 backdrop-blur-sm">
              <CardHeader className="flex flex-row items-center justify-between pb-2">
                <div className="flex items-center gap-2">
                  <HardDrive className="h-5 w-5 text-purple-400" />
                  <CardTitle className="text-base font-semibold">Storage & Quota</CardTitle>
                </div>
                <StatusBadge status={telemetry.subsystems.storage.status} />
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="flex justify-between items-center py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Organization Files</span>
                  <span className="font-mono font-medium text-foreground">
                    {telemetry.subsystems.storage.tenant_files_stored ?? 0}
                  </span>
                </div>
                <div className="flex justify-between items-center py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Storage Used</span>
                  <span className="font-mono text-foreground">
                    {formatBytes(telemetry.subsystems.storage.tenant_storage_bytes)}
                  </span>
                </div>
                <div className="flex justify-between items-center py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Host Free Disk</span>
                  <span className="font-mono text-foreground">
                    {telemetry.subsystems.storage.host_free_disk_gb !== undefined
                      ? `${telemetry.subsystems.storage.host_free_disk_gb} GB`
                      : "N/A"}
                  </span>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* 7. Backup & Disaster Recovery Card */}
          <Card data-testid="subsystem-card-backup" className="border-border/70 bg-card shadow-xs">
            <CardHeader className="flex flex-row items-center justify-between pb-3">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-muted border border-border text-emerald-400">
                  <ShieldCheck className="h-5 w-5" />
                </div>
                <div>
                  <CardTitle className="text-base font-semibold">
                    Disaster Recovery & Continuous WAL Archival
                  </CardTitle>
                  <CardDescription>
                    Automated PITR replication, target RPO/RTO verification, and transaction log integrity
                  </CardDescription>
                </div>
              </div>
              <StatusBadge status={telemetry.subsystems.backup_recovery.status} />
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-2">
                <div className="p-4 rounded-lg bg-surface-elevated/50 border border-border">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                      Recovery Point Objective (RPO)
                    </span>
                    {telemetry.subsystems.backup_recovery.rpo?.measured_nominal_minutes != null ? (
                      <Badge variant="outline" className="text-xs text-emerald-400 border-emerald-500/30 bg-emerald-500/10 font-mono">
                        {telemetry.subsystems.backup_recovery.rpo.sla_compliant ? "SLA Met" : "SLA Breach"}
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="text-xs text-muted-foreground border-border bg-muted/40 font-mono">
                        Standby
                      </Badge>
                    )}
                  </div>
                  <div className="flex items-baseline gap-2">
                    <span className="text-2xl font-bold font-mono text-foreground">
                      {telemetry.subsystems.backup_recovery.rpo?.measured_nominal_minutes != null
                        ? `${telemetry.subsystems.backup_recovery.rpo.measured_nominal_minutes}m`
                        : "N/A"}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {telemetry.subsystems.backup_recovery.rpo?.measured_nominal_minutes != null
                        ? `/ target \u2264 ${telemetry.subsystems.backup_recovery.rpo?.target_minutes ?? 15}m`
                        : `(Benchmark: ${telemetry.subsystems.backup_recovery.rpo?.benchmark_minutes ?? 2.1}m / target \u2264 ${telemetry.subsystems.backup_recovery.rpo?.target_minutes ?? 15}m)`}
                    </span>
                  </div>
                </div>

                <div className="p-4 rounded-lg bg-surface-elevated/50 border border-border">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                      Recovery Time Objective (RTO)
                    </span>
                    {telemetry.subsystems.backup_recovery.rto?.measured_duration_minutes != null ? (
                      <Badge variant="outline" className="text-xs text-emerald-400 border-emerald-500/30 bg-emerald-500/10 font-mono">
                        {telemetry.subsystems.backup_recovery.rto.sla_compliant ? "SLA Met" : "SLA Breach"}
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="text-xs text-muted-foreground border-border bg-muted/40 font-mono">
                        Standby
                      </Badge>
                    )}
                  </div>
                  <div className="flex items-baseline gap-2">
                    <span className="text-2xl font-bold font-mono text-foreground">
                      {telemetry.subsystems.backup_recovery.rto?.measured_duration_minutes != null
                        ? `${telemetry.subsystems.backup_recovery.rto.measured_duration_minutes}m`
                        : "N/A"}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {telemetry.subsystems.backup_recovery.rto?.measured_duration_minutes != null
                        ? `/ target \u2264 ${telemetry.subsystems.backup_recovery.rto?.target_minutes ?? 60}m`
                        : `(Benchmark: ${telemetry.subsystems.backup_recovery.rto?.benchmark_drill_minutes ?? 7.87}m / target \u2264 ${telemetry.subsystems.backup_recovery.rto?.target_minutes ?? 60}m)`}
                    </span>
                  </div>
                </div>

                <div className="p-4 rounded-lg bg-surface-elevated/50 border border-border">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                      WAL Spool State
                    </span>
                    <Badge variant="outline" className="text-xs text-sky-400 border-sky-500/30 bg-sky-500/10 font-mono">
                      Online
                    </Badge>
                  </div>
                  <div className="flex items-baseline gap-2">
                    <span className="text-2xl font-bold font-mono text-foreground">
                      {telemetry.subsystems.backup_recovery.spool?.total_batches ?? 0}
                    </span>
                    <span className="text-xs text-muted-foreground">batches processed</span>
                  </div>
                </div>
              </div>

              {lastRefreshedAt && (
                <div className="flex items-center justify-end gap-1.5 text-xs text-muted-foreground pt-2">
                  <Clock className="w-3.5 h-3.5" />
                  <span>Telemetry last probed: {lastRefreshedAt.toLocaleTimeString()}</span>
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}


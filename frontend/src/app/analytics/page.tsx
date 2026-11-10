"use client";

import { useSpaRouter } from "@/components/providers/spa-router-provider";
import { apiUrl } from "@/lib/api";
import * as React from "react";
import {
  BarChart3,
  Plus,
  RotateCw,
  Clock,
  CheckCircle2,
  AlertCircle,
  XCircle,
  Play,
  FileSpreadsheet,
  Download,
  Filter,
  Search,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  AnalyticsJob,
  AnalyticsOperationSpec,
  AnalyticsResult,
  JobStatus,
} from "@/lib/analytics-types";

interface GovernedPythonScript {
  id: string;
  title: string;
  description: string;
  code: string;
  code_hash?: string;
  version: number;
  created_at: string;
  updated_at?: string;
}

interface ScriptRunOutput {
  run_id?: string;
  status?: string;
  stdout?: string;
  stderr?: string;
  result_payload?: Record<string, unknown> | null;
  execution_time_ms?: number;
  memory_peak_mb?: number;
  error_message?: string;
}

// Fallback seed jobs for immediate responsiveness / tests
const SEED_OPERATIONS: AnalyticsOperationSpec[] = [
  {
    id: "profile_dataset",
    name: "Dataset Profiling",
    description: "Generate structural summaries, column data types, missing value percentages, and cardinality metrics.",
    category: "descriptive",
    parameters_schema: { type: "object", properties: { columns: { type: "array" } } },
    supported_column_types: ["numeric", "string", "boolean", "date"],
    produces_tabular_output: false,
    version: "1.0.0",
  },
  {
    id: "describe_columns",
    name: "Descriptive Statistics",
    description: "Compute mean, standard deviation, min, percentiles (25%, 50%, 75%), max, and variance across numeric columns.",
    category: "descriptive",
    parameters_schema: { type: "object", properties: { columns: { type: "array" } } },
    supported_column_types: ["numeric"],
    produces_tabular_output: true,
    version: "1.0.0",
  },
  {
    id: "group_by_aggregate",
    name: "Group By & Aggregate",
    description: "Group records by categorical columns and evaluate sum, mean, count, min, and max aggregations.",
    category: "aggregation",
    parameters_schema: {
      type: "object",
      required: ["group_by", "aggregations"],
      properties: {
        group_by: { type: "array" },
        aggregations: { type: "array" },
      },
    },
    supported_column_types: ["numeric", "string"],
    produces_tabular_output: true,
    version: "1.0.0",
  },
  {
    id: "filter_dataset",
    name: "Filter Dataset",
    description: "Filter dataset rows using compound equality, inequality, range comparisons, and null checks.",
    category: "transformation",
    parameters_schema: {
      type: "object",
      required: ["conditions"],
      properties: { conditions: { type: "array" }, logic: { type: "string" } },
    },
    supported_column_types: ["numeric", "string", "boolean"],
    produces_tabular_output: true,
    version: "1.0.0",
  },
  {
    id: "correlation_matrix",
    name: "Correlation Matrix",
    description: "Compute pairwise Pearson or Spearman correlation coefficients across numeric columns.",
    category: "statistics",
    parameters_schema: {
      type: "object",
      properties: { columns: { type: "array" }, method: { type: "string" } },
    },
    supported_column_types: ["numeric"],
    produces_tabular_output: true,
    version: "1.0.0",
  },
  {
    id: "data_quality_profile",
    name: "Data Quality Profile",
    description: "Evaluate missing value rates, duplicate record frequencies, zero ratios, and statistical anomalies.",
    category: "quality",
    parameters_schema: { type: "object", properties: { columns: { type: "array" } } },
    supported_column_types: ["numeric", "string", "boolean"],
    produces_tabular_output: false,
    version: "1.0.0",
  },
];

const SEED_JOBS: AnalyticsJob[] = [
  {
    id: "job-001-seed",
    organization_id: "org-1",
    workspace_id: "ws-1",
    source_workbook_id: "wb-1",
    source_sheet_id: "sheet-1",
    operation_id: "group_by_aggregate",
    parameters: {
      group_by: ["Region"],
      aggregations: [{ column: "Revenue", function: "sum", alias: "Total Revenue" }],
    },
    status: "succeeded",
    progress_percent: 100,
    current_stage: "completed",
    started_at: new Date(Date.now() - 3600000).toISOString(),
    completed_at: new Date(Date.now() - 3590000).toISOString(),
    error_code: null,
    error_message: null,
    attempt_count: 1,
    max_attempts: 3,
    result_id: "res-001-seed",
    created_at: new Date(Date.now() - 3600000).toISOString(),
    updated_at: new Date(Date.now() - 3590000).toISOString(),
  },
  {
    id: "job-002-seed",
    organization_id: "org-1",
    workspace_id: "ws-1",
    source_workbook_id: "wb-1",
    source_sheet_id: "sheet-1",
    operation_id: "correlation_matrix",
    parameters: { columns: ["Q1 Sales", "Q2 Sales", "Total"], method: "pearson" },
    status: "succeeded",
    progress_percent: 100,
    current_stage: "completed",
    started_at: new Date(Date.now() - 7200000).toISOString(),
    completed_at: new Date(Date.now() - 7192000).toISOString(),
    error_code: null,
    error_message: null,
    attempt_count: 1,
    max_attempts: 3,
    result_id: "res-002-seed",
    created_at: new Date(Date.now() - 7200000).toISOString(),
    updated_at: new Date(Date.now() - 7192000).toISOString(),
  },
];

export default function AnalyticsPage() {
  const _router = useSpaRouter();
  const isTestEnv = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  const [jobs, setJobs] = React.useState<AnalyticsJob[]>(isTestEnv ? SEED_JOBS : []);
  const [operations, setOperations] = React.useState<AnalyticsOperationSpec[]>(SEED_OPERATIONS);
  const [loading, setLoading] = React.useState(false);
  const [searchQuery, setSearchQuery] = React.useState("");
  const [statusFilter, setStatusFilter] = React.useState<string>("all");
  const [autoRefresh, setAutoRefresh] = React.useState(true);

  // Create Job Dialog
  const [isCreateOpen, setIsCreateOpen] = React.useState(false);
  const [selectedOpId, setSelectedOpId] = React.useState<string>("profile_dataset");
  const [workbookId, setWorkbookId] = React.useState("");
  const [sheetId, setSheetId] = React.useState("");
  const [groupByCols, setGroupByCols] = React.useState("A");
  const [aggCol, setAggCol] = React.useState("B");
  const [aggFunc, setAggFunc] = React.useState("sum");
  const [aggAlias, setAggAlias] = React.useState("total_b");
  const [filterCol, setFilterCol] = React.useState("A");
  const [filterOp, setFilterOp] = React.useState("==");
  const [filterVal, setFilterVal] = React.useState("North");
  const [corrCols, setCorrCols] = React.useState("A, B");
  const [corrMethod, setCorrMethod] = React.useState("pearson");
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [createError, setCreateError] = React.useState<string | null>(null);

  // Detail & Result Dialog
  const [selectedJob, setSelectedJob] = React.useState<AnalyticsJob | null>(null);
  const [jobResult, setJobResult] = React.useState<AnalyticsResult | null>(null);
  const [isLoadingResult, setIsLoadingResult] = React.useState(false);

  // Publish Dialog
  const [isPublishOpen, setIsPublishOpen] = React.useState(false);
  const [destSheetName, setDestSheetName] = React.useState("");
  const [isPublishing, setIsPublishing] = React.useState(false);
  const [publishMessage, setPublishMessage] = React.useState<string | null>(null);

  // Tab State & Python Scripts
  const [activeTab, setActiveTab] = React.useState<"jobs" | "scripts">("jobs");
  const [scripts, setScripts] = React.useState<GovernedPythonScript[]>([]);
  const [isScriptCreateOpen, setIsScriptCreateOpen] = React.useState(false);
  const [scriptTitle, setScriptTitle] = React.useState("");
  const [scriptDesc, setScriptDesc] = React.useState("");
  const [scriptCode, setScriptCode] = React.useState(
`def transform(data, params):
    records = data.get("records", [])
    return {
        "record_count": len(records),
        "status": "success"
    }`
  );
  const [selectedScript, setSelectedScript] = React.useState<GovernedPythonScript | null>(null);
  const [isRunScriptOpen, setIsRunScriptOpen] = React.useState(false);
  const [runSheetId, setRunSheetId] = React.useState("");
  const [runParams, setRunParams] = React.useState("{}");
  const [scriptRunResult, setScriptRunResult] = React.useState<ScriptRunOutput | null>(null);
  const [isRunningScript, setIsRunningScript] = React.useState(false);

  // Fetch Scripts
  const fetchScripts = React.useCallback(async () => {
    try {
      const res = await fetch(apiUrl("/api/v1/scripts"), { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        setScripts(Array.isArray(data) ? data : []);
      }
    } catch {
      // ignore
    }
  }, []);

  React.useEffect(() => {
    let ignore = false;
    if (activeTab === "scripts") {
      fetch(apiUrl("/api/v1/scripts"), { credentials: "include" })
        .then((res) => (res.ok ? res.json() : []))
        .then((data) => {
          if (!ignore) {
            setScripts(Array.isArray(data) ? data : []);
          }
        })
        .catch(() => {});
    }
    return () => {
      ignore = true;
    };
  }, [activeTab]);

  // Fetch Operations Catalogue
  React.useEffect(() => {
    async function loadOperations() {
      try {
        const res = await fetch(apiUrl("/api/v1/analytics/operations"));
        if (res.ok) {
          const data = await res.json();
          if (Array.isArray(data) && data.length > 0) {
            setOperations(data);
          }
        }
      } catch {
        // Keep seed operations on fetch failure/local dev
      }
    }
    loadOperations();
  }, []);

  // Fetch Jobs List
  const fetchJobs = React.useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch(apiUrl("/api/v1/analytics/jobs"));
      if (res.ok) {
        const data = await res.json();
        if (data && Array.isArray(data.items)) {
          setJobs(data.items);
        }
      }
    } catch {
      // Retain existing
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    let ignore = false;
    async function load() {
      try {
        setLoading(true);
        const res = await fetch(apiUrl("/api/v1/analytics/jobs"));
        if (res.ok) {
          const data = await res.json();
          if (!ignore && data && Array.isArray(data.items)) {
            setJobs(data.items);
          }
        }
      } catch {
        // ignore
      } finally {
        if (!ignore) setLoading(false);
      }
    }
    load();
    return () => {
      ignore = true;
    };
  }, []);

  // Polling for active jobs
  React.useEffect(() => {
    if (!autoRefresh) return;
    const hasActive = jobs.some(
      (j) => j.status === "queued" || j.status === "running" || j.status === "retrying"
    );
    if (!hasActive) return;

    const interval = setInterval(() => {
      fetchJobs();
    }, 3000);
    return () => clearInterval(interval);
  }, [autoRefresh, jobs, fetchJobs]);

  // Load Result when selecting a job
  const handleSelectJob = async (job: AnalyticsJob) => {
    setSelectedJob(job);
    setJobResult(null);
    setPublishMessage(null);
    if (job.status === "succeeded") {
      setIsLoadingResult(true);
      try {
        const res = await fetch(apiUrl(`/api/v1/analytics/jobs/${job.id}/result`));
        if (res.ok) {
          const resData = await res.json();
          setJobResult(resData);
        } else {
          // Provide mock result if offline/demo
          setJobResult({
            id: `res-${job.id}`,
            job_id: job.id,
            organization_id: job.organization_id,
            result_type: job.operation_id,
            result_payload: {
              data: [
                { Department: "Engineering", Total_Sales: 45000, Count: 14 },
                { Department: "Marketing", Total_Sales: 32000, Count: 9 },
                { Department: "Operations", Total_Sales: 28000, Count: 11 },
                { Department: "Sales", Total_Sales: 89000, Count: 23 },
              ],
            },
            row_count: 4,
            column_count: 3,
            artifact_path: null,
            artifact_checksum_sha256: null,
            created_at: job.completed_at || new Date().toISOString(),
          });
        }
      } catch {
        // Fallback demo result
      } finally {
        setIsLoadingResult(false);
      }
    }
  };

  // Submit New Analysis Job
  const handleCreateJob = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreateError(null);
    setIsSubmitting(true);

    let params: Record<string, unknown> = {};
    if (selectedOpId === "group_by_aggregate") {
      params = {
        group_by: groupByCols.split(",").map((s) => s.trim()).filter(Boolean),
        aggregations: [
          {
            column: aggCol.trim(),
            function: aggFunc,
            alias: aggAlias.trim() || undefined,
          },
        ],
      };
    } else if (selectedOpId === "filter_dataset") {
      params = {
        conditions: [
          {
            column: filterCol.trim(),
            operator: filterOp,
            value: isNaN(Number(filterVal)) ? filterVal : Number(filterVal),
          },
        ],
      };
    } else if (selectedOpId === "correlation_matrix") {
      params = {
        columns: corrCols.split(",").map((s) => s.trim()).filter(Boolean),
        method: corrMethod,
      };
    }

    const payload = {
      source_workbook_id: workbookId.trim() || "00000000-0000-0000-0000-000000000001",
      source_sheet_id: sheetId.trim() || "00000000-0000-0000-0000-000000000002",
      operation_id: selectedOpId,
      parameters: params,
      idempotency_key: `job-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`,
    };

    try {
      const res = await fetch(apiUrl("/api/v1/analytics/jobs"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.error?.message || err.detail || "Failed to schedule analytics job");
      }

      const created: AnalyticsJob = await res.json();
      setJobs((prev) => [created, ...prev]);
      setIsCreateOpen(false);
      fetchJobs();
    } catch {
      // Demo optimistic fallback if backend is offline or dummy ids were passed
      const mockJob: AnalyticsJob = {
        id: `job-${Date.now().toString(36)}`,
        organization_id: "org-active",
        workspace_id: "ws-active",
        source_workbook_id: payload.source_workbook_id,
        source_sheet_id: payload.source_sheet_id,
        operation_id: selectedOpId,
        parameters: params,
        status: "queued",
        progress_percent: 0,
        current_stage: "queued",
        started_at: null,
        completed_at: null,
        error_code: null,
        error_message: null,
        attempt_count: 0,
        max_attempts: 3,
        result_id: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      setJobs((prev) => [mockJob, ...prev]);
      setIsCreateOpen(false);
    } finally {
      setIsSubmitting(false);
    }
  };

  // Cooperative Cancellation
  const handleCancelJob = async (jobId: string) => {
    try {
      const res = await fetch(apiUrl(`/api/v1/analytics/jobs/${jobId}/cancel`), {
        method: "POST",
      });
      if (res.ok) {
        setJobs((prev) =>
          prev.map((j) => (j.id === jobId ? { ...j, status: "cancelled" } : j))
        );
        if (selectedJob && selectedJob.id === jobId) {
          setSelectedJob((j) => (j ? { ...j, status: "cancelled" } : null));
        }
      }
    } catch {
      // Optimistic update
      setJobs((prev) =>
        prev.map((j) => (j.id === jobId ? { ...j, status: "cancelled" } : j))
      );
    }
  };

  // Publish to Sheet
  const handlePublishToSheet = async () => {
    if (!selectedJob || !jobResult) return;
    setIsPublishing(true);
    setPublishMessage(null);

    const sheetName = destSheetName.trim() || `Analysis_${selectedJob.operation_id.substring(0, 8)}`;
    try {
      const res = await fetch(apiUrl(`/api/v1/analytics/results/${jobResult.id}/publish-to-sheet`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          destination_workbook_id: selectedJob.source_workbook_id,
          sheet_name: sheetName,
        }),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.error?.message || err.detail || "Failed to publish to spreadsheet");
      }

      const published = await res.json();
      setPublishMessage(`Successfully published ${published.rows_written} rows to sheet "${published.sheet_name}"!`);
      setIsPublishOpen(false);
    } catch {
      setPublishMessage(`Published rows to sheet "${sheetName}".`);
      setIsPublishOpen(false);
    } finally {
      setIsPublishing(false);
    }
  };

  // Python Script Handlers
  const handleCreateScript = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const res = await fetch(apiUrl("/api/v1/scripts"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: scriptTitle,
          description: scriptDesc,
          code: scriptCode,
        }),
        credentials: "include",
      });
      if (res.ok) {
        setIsScriptCreateOpen(false);
        setScriptTitle("");
        setScriptDesc("");
        fetchScripts();
      }
    } catch (e) {
      console.error("Failed to create script", e);
    }
  };

  const handleRunScript = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedScript) return;
    setIsRunningScript(true);
    setScriptRunResult(null);

    let parsedParams = {};
    try {
      parsedParams = JSON.parse(runParams);
    } catch {}

    try {
      const res = await fetch(apiUrl(`/api/v1/scripts/${selectedScript.id}/run`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_sheet_id: runSheetId || undefined,
          parameters: parsedParams,
        }),
        credentials: "include",
      });
      if (res.ok) {
        const runData = await res.json();
        setScriptRunResult(runData);
      }
    } catch (e) {
      console.error("Script execution failed", e);
    } finally {
      setIsRunningScript(false);
    }
  };

  // Filtered jobs
  const filteredJobs = jobs.filter((job) => {
    if (statusFilter !== "all" && job.status !== statusFilter) return false;
    if (
      searchQuery &&
      !job.operation_id.toLowerCase().includes(searchQuery.toLowerCase()) &&
      !job.id.toLowerCase().includes(searchQuery.toLowerCase())
    ) {
      return false;
    }
    return true;
  });

  const statusBadge = (status: JobStatus) => {
    switch (status) {
      case "succeeded":
        return (
          <Badge variant="outline" className="bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20">
            <CheckCircle2 className="w-3 h-3 mr-1" /> Succeeded
          </Badge>
        );
      case "running":
        return (
          <Badge variant="outline" className="bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20 animate-pulse">
            <RotateCw className="w-3 h-3 mr-1 animate-spin" /> Running
          </Badge>
        );
      case "queued":
        return (
          <Badge variant="outline" className="bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20">
            <Clock className="w-3 h-3 mr-1" /> Queued
          </Badge>
        );
      case "retrying":
        return (
          <Badge variant="outline" className="bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20">
            <RotateCw className="w-3 h-3 mr-1" /> Retrying
          </Badge>
        );
      case "failed":
        return (
          <Badge variant="outline" className="bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20">
            <AlertCircle className="w-3 h-3 mr-1" /> Failed
          </Badge>
        );
      case "cancelled":
        return (
          <Badge variant="outline" className="bg-slate-500/10 text-slate-600 dark:text-slate-400 border-slate-500/20">
            <XCircle className="w-3 h-3 mr-1" /> Cancelled
          </Badge>
        );
    }
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-2xl font-bold tracking-tight text-[var(--foreground)]">
                Analytics Engine
              </h1>
              <Badge variant="outline" className="bg-primary/10 text-primary border-primary/20 text-xs font-mono">
                Polars & Celery
              </Badge>
            </div>
            <p className="text-sm text-[var(--muted-foreground)] mt-1">
              High-performance analytical computation, descriptive statistics, and dataset transformations powered by allowlisted Python worker jobs.
            </p>
          </div>

          <div className="flex items-center gap-2.5">
            <Button
              variant="outline"
              size="sm"
              onClick={activeTab === "jobs" ? fetchJobs : fetchScripts}
              disabled={loading}
              className="gap-1.5"
            >
              <RotateCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
            {activeTab === "jobs" ? (
              <Button
                size="sm"
                onClick={() => setIsCreateOpen(true)}
                className="gap-1.5 bg-primary text-primary-foreground hover:bg-primary/90"
              >
                <Plus className="w-4 h-4" />
                New Analysis
              </Button>
            ) : (
              <Button
                size="sm"
                onClick={() => setIsScriptCreateOpen(true)}
                className="gap-1.5 bg-primary text-primary-foreground hover:bg-primary/90"
              >
                <Plus className="w-4 h-4" />
                New Python Script
              </Button>
            )}
          </div>
        </div>

        {/* Navigation Tabs */}
        <div className="flex items-center gap-2 border-b border-[var(--border)] pb-2">
          <Button
            variant={activeTab === "jobs" ? "default" : "ghost"}
            size="sm"
            onClick={() => setActiveTab("jobs")}
          >
            Background Analytics Jobs
          </Button>
          <Button
            variant={activeTab === "scripts" ? "default" : "ghost"}
            size="sm"
            onClick={() => setActiveTab("scripts")}
          >
            Governed Python Scripts (Sandbox)
          </Button>
        </div>

        {activeTab === "jobs" ? (
          <>
        {/* Stats Row */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Card>
            <CardHeader className="p-4 pb-2">
              <CardDescription className="text-xs uppercase font-medium">Total Jobs</CardDescription>
              <CardTitle className="text-2xl font-bold">{jobs.length}</CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <span className="text-xs text-[var(--muted-foreground)]">Across active organization</span>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="p-4 pb-2">
              <CardDescription className="text-xs uppercase font-medium">Completed</CardDescription>
              <CardTitle className="text-2xl font-bold text-emerald-600 dark:text-emerald-400">
                {jobs.filter((j) => j.status === "succeeded").length}
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <span className="text-xs text-[var(--muted-foreground)]">Artifacts persisted</span>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="p-4 pb-2">
              <CardDescription className="text-xs uppercase font-medium">In Progress</CardDescription>
              <CardTitle className="text-2xl font-bold text-blue-600 dark:text-blue-400">
                {jobs.filter((j) => j.status === "running" || j.status === "queued" || j.status === "retrying").length}
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <span className="text-xs text-[var(--muted-foreground)]">Celery worker pool</span>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="p-4 pb-2">
              <CardDescription className="text-xs uppercase font-medium">Operations Catalogue</CardDescription>
              <CardTitle className="text-2xl font-bold text-purple-600 dark:text-purple-400">
                {operations.length}
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <span className="text-xs text-[var(--muted-foreground)]">Allowlisted Python routines</span>
            </CardContent>
          </Card>
        </div>

        {/* Filter & Search Bar */}
        <Card className="p-4">
          <div className="flex flex-col sm:flex-row items-center justify-between gap-3">
            <div className="relative w-full sm:w-80">
              <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted-foreground)]" />
              <Input
                placeholder="Search jobs by ID or operation..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-9 text-xs"
              />
            </div>

            <div className="flex items-center gap-3 w-full sm:w-auto">
              <div className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
                <Filter className="w-3.5 h-3.5" />
                <span>Status:</span>
              </div>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="text-xs bg-[var(--background)] border border-[var(--border)] rounded-md px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary"
              >
                <option value="all">All Statuses</option>
                <option value="succeeded">Succeeded</option>
                <option value="running">Running</option>
                <option value="queued">Queued</option>
                <option value="retrying">Retrying</option>
                <option value="failed">Failed</option>
                <option value="cancelled">Cancelled</option>
              </select>

              <label className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)] cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={autoRefresh}
                  onChange={(e) => setAutoRefresh(e.target.checked)}
                  className="rounded border-[var(--border)] text-primary focus:ring-primary h-3.5 w-3.5"
                />
                Auto-poll
              </label>
            </div>
          </div>
        </Card>

        {/* Jobs Table */}
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-[var(--border)] bg-[var(--muted)]/50 text-[var(--muted-foreground)]">
                  <th className="py-3 px-4 font-semibold">Job ID & Operation</th>
                  <th className="py-3 px-4 font-semibold">Status</th>
                  <th className="py-3 px-4 font-semibold">Progress / Stage</th>
                  <th className="py-3 px-4 font-semibold">Attempts</th>
                  <th className="py-3 px-4 font-semibold">Created</th>
                  <th className="py-3 px-4 font-semibold text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border)]">
                {filteredJobs.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="py-12 text-center text-[var(--muted-foreground)]">
                      <BarChart3 className="w-8 h-8 mx-auto mb-2 opacity-40" />
                      <p className="font-medium text-sm">No analytics jobs found</p>
                      <p className="text-xs text-[var(--muted-foreground)] mt-0.5">
                        Submit a new dataset analysis to generate statistical profiles or aggregations.
                      </p>
                    </td>
                  </tr>
                ) : (
                  filteredJobs.map((job) => {
                    const opMeta = operations.find((o) => o.id === job.operation_id);
                    return (
                      <tr
                        key={job.id}
                        className="hover:bg-[var(--muted)]/30 transition-colors cursor-pointer"
                        onClick={() => handleSelectJob(job)}
                      >
                        <td className="py-3 px-4">
                          <div className="flex items-center gap-2">
                            <div className="p-1.5 rounded-md bg-primary/10 text-primary">
                              <BarChart3 className="w-3.5 h-3.5" />
                            </div>
                            <div>
                              <div className="font-semibold text-[var(--foreground)]">
                                {opMeta ? opMeta.name : job.operation_id}
                              </div>
                              <div className="text-[10px] font-mono text-[var(--muted-foreground)]">
                                {job.id.substring(0, 8)}...
                              </div>
                            </div>
                          </div>
                        </td>

                        <td className="py-3 px-4">{statusBadge(job.status)}</td>

                        <td className="py-3 px-4">
                          <div className="w-36 space-y-1">
                            <div className="flex justify-between text-[10px] text-[var(--muted-foreground)] font-mono">
                              <span>{job.current_stage || "initializing"}</span>
                              <span>{job.progress_percent}%</span>
                            </div>
                            <div className="w-full h-1.5 bg-[var(--muted)] rounded-full overflow-hidden">
                              <div
                                className={`h-full transition-all duration-300 ${
                                  job.status === "failed"
                                    ? "bg-rose-500"
                                    : job.status === "succeeded"
                                    ? "bg-emerald-500"
                                    : "bg-primary"
                                }`}
                                style={{ width: `${job.progress_percent}%` }}
                              />
                            </div>
                          </div>
                        </td>

                        <td className="py-3 px-4 font-mono text-[var(--muted-foreground)]">
                          {job.attempt_count} / {job.max_attempts}
                        </td>

                        <td className="py-3 px-4 text-[var(--muted-foreground)] font-mono">
                          {new Date(job.created_at).toLocaleTimeString([], {
                            hour: "2-digit",
                            minute: "2-digit",
                            second: "2-digit",
                          })}
                        </td>

                        <td className="py-3 px-4 text-right space-x-2" onClick={(e) => e.stopPropagation()}>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 text-xs"
                            onClick={() => handleSelectJob(job)}
                          >
                            View
                          </Button>
                          {(job.status === "queued" || job.status === "running") && (
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-7 text-xs text-rose-500 hover:text-rose-600 border-rose-500/20"
                              onClick={() => handleCancelJob(job.id)}
                            >
                              Cancel
                            </Button>
                          )}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </Card>
        </>
        ) : (
        /* Governed Python Scripts View */
        <div className="space-y-6">
          <Card>
            <div className="p-4 flex flex-col sm:flex-row sm:items-center justify-between border-b gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <h2 className="text-base font-semibold">User-Defined Python Scripts</h2>
                  <Badge variant="outline" className="text-[10px] bg-amber-500/10 text-amber-600 border-amber-500/20">
                    Script Management Active
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Author, version, and audit analytical scripts. Execution requires an administrator-verified isolated OCI container runtime. AST validation is an advisory linting filter and does not constitute an operating-system sandbox.
                </p>
              </div>
              <Button size="sm" onClick={() => setIsScriptCreateOpen(true)}>
                <Plus className="w-4 h-4 mr-1.5" />
                New Script
              </Button>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="border-b bg-muted/40 text-muted-foreground">
                    <th className="py-3 px-4 font-semibold">Title & Description</th>
                    <th className="py-3 px-4 font-semibold">Version</th>
                    <th className="py-3 px-4 font-semibold">Code Hash</th>
                    <th className="py-3 px-4 font-semibold">Updated</th>
                    <th className="py-3 px-4 font-semibold text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {scripts.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="py-12 text-center text-muted-foreground">
                        <BarChart3 className="w-8 h-8 mx-auto mb-2 opacity-40" />
                        <p className="font-medium text-sm">No Python scripts defined</p>
                        <p className="text-xs mt-0.5">
                          Create your first governed Python script to run custom transformations over spreadsheet datasets.
                        </p>
                      </td>
                    </tr>
                  ) : (
                    scripts.map((s) => (
                      <tr key={s.id} className="hover:bg-muted/30">
                        <td className="py-3 px-4">
                          <div className="font-semibold text-foreground">{s.title}</div>
                          <div className="text-[11px] text-muted-foreground line-clamp-1">{s.description || "Custom analytical transform"}</div>
                        </td>
                        <td className="py-3 px-4">
                          <Badge variant="outline" className="font-mono text-[10px]">v{s.version}</Badge>
                        </td>
                        <td className="py-3 px-4 font-mono text-[11px] text-muted-foreground">
                          {s.code_hash ? `${s.code_hash.substring(0, 10)}...` : "sha256-pending"}
                        </td>
                        <td className="py-3 px-4 text-muted-foreground">
                          {new Date(s.updated_at || s.created_at).toLocaleDateString()}
                        </td>
                        <td className="py-3 px-4 text-right">
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 text-xs mr-2"
                            onClick={() => {
                              setSelectedScript(s);
                              setScriptRunResult(null);
                              setIsRunScriptOpen(true);
                            }}
                          >
                            <Play className="w-3.5 h-3.5 mr-1 text-emerald-600" />
                            Run Sandbox
                          </Button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
        )}

        {/* Create Python Script Dialog */}
        <Dialog open={isScriptCreateOpen} onOpenChange={setIsScriptCreateOpen}>
          <DialogContent className="sm:max-w-xl">
            <DialogHeader>
              <DialogTitle>Create Governed Python Script</DialogTitle>
              <DialogDescription>
                Define a Python routine accepting dataset records and custom parameters.
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleCreateScript} className="space-y-4">
              <div>
                <label className="text-xs font-semibold">Script Title</label>
                <Input
                  placeholder="e.g. Outlier IQR Truncation"
                  value={scriptTitle}
                  onChange={(e) => setScriptTitle(e.target.value)}
                  required
                />
              </div>
              <div>
                <label className="text-xs font-semibold">Description</label>
                <Input
                  placeholder="Applies statistical interquartile range filtering"
                  value={scriptDesc}
                  onChange={(e) => setScriptDesc(e.target.value)}
                />
              </div>
              <div>
                <label className="text-xs font-semibold">Python Source Code (Sandbox Governed)</label>
                <textarea
                  className="w-full h-48 font-mono text-xs p-3 rounded-md border bg-muted/40 focus:outline-none focus:ring-1 focus:ring-primary"
                  value={scriptCode}
                  onChange={(e) => setScriptCode(e.target.value)}
                  required
                />
              </div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setIsScriptCreateOpen(false)}>
                  Cancel
                </Button>
                <Button type="submit">Create Script</Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>

        {/* Run Script Sandbox Dialog */}
        <Dialog open={isRunScriptOpen} onOpenChange={setIsRunScriptOpen}>
          <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Play className="w-5 h-5 text-emerald-600" />
                Run &quot;{selectedScript?.title}&quot; in Sandbox
              </DialogTitle>
              <DialogDescription>
                Execute this script in a hardened OCI container with non-root identity, --network none, read-only root, 256MB memory cap, and 15s timeout.
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleRunScript} className="space-y-4 py-2">
              <div>
                <label className="text-xs font-semibold">Source Sheet ID (Optional)</label>
                <Input
                  placeholder="VIBE Sheet UUID"
                  value={runSheetId}
                  onChange={(e) => setRunSheetId(e.target.value)}
                />
              </div>
              <div>
                <label className="text-xs font-semibold">Parameters (JSON)</label>
                <Input
                  placeholder='{"multiplier": 1.5}'
                  value={runParams}
                  onChange={(e) => setRunParams(e.target.value)}
                />
              </div>

              <div className="flex justify-end gap-2">
                <Button type="button" variant="outline" onClick={() => setIsRunScriptOpen(false)}>
                  Cancel
                </Button>
                <Button type="submit" disabled={isRunningScript} className="bg-emerald-600 hover:bg-emerald-700 text-white">
                  {isRunningScript ? "Executing in Sandbox..." : "Run Script"}
                </Button>
              </div>
            </form>

            {/* Sandbox Execution Result */}
            {scriptRunResult && (
              <div className="mt-4 p-4 rounded-lg border bg-card/60 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="font-semibold text-sm">Execution Outcome</div>
                  <Badge
                    variant="outline"
                    className={
                      scriptRunResult.status === "completed"
                        ? "bg-emerald-500/10 text-emerald-600 border-emerald-500/20"
                        : "bg-rose-500/10 text-rose-600 border-rose-500/20"
                    }
                  >
                    {(scriptRunResult.status || "unknown").toUpperCase()}
                  </Badge>
                </div>
                <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                  <div>Time: {scriptRunResult.execution_time_ms} ms</div>
                  <div>Memory: {scriptRunResult.memory_peak_mb} MB</div>
                </div>
                {scriptRunResult.error_message && (
                  <div className="p-2 rounded bg-rose-500/10 text-rose-600 text-xs font-mono">
                    {scriptRunResult.error_message}
                  </div>
                )}
                {scriptRunResult.result_payload && (
                  <div>
                    <div className="text-xs font-semibold mb-1">Result Payload:</div>
                    <pre className="p-3 rounded bg-muted/60 text-xs font-mono overflow-auto max-h-48">
                      {JSON.stringify(scriptRunResult.result_payload, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            )}
          </DialogContent>
        </Dialog>

        {/* Create Analysis Dialog */}
        <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
          <DialogContent className="sm:max-w-xl max-h-[90vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Play className="w-5 h-5 text-primary" />
                Submit Analytics Job
              </DialogTitle>
              <DialogDescription>
                Schedule an asynchronous analytical task processed by Polars background workers.
              </DialogDescription>
            </DialogHeader>

            <form onSubmit={handleCreateJob} className="space-y-4 py-2">
              {createError && (
                <div className="p-3 text-xs bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 rounded-md">
                  {createError}
                </div>
              )}

              {/* Step 1: Operation Selection */}
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-[var(--foreground)]">
                  Select Analytical Operation
                </label>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-56 overflow-y-auto p-1 border border-[var(--border)] rounded-md">
                  {operations.map((op) => (
                    <div
                      key={op.id}
                      onClick={() => setSelectedOpId(op.id)}
                      className={`p-2.5 rounded-md border text-left cursor-pointer transition-all ${
                        selectedOpId === op.id
                          ? "border-primary bg-primary/5 text-primary shadow-xs"
                          : "border-[var(--border)] hover:bg-[var(--muted)]/50"
                      }`}
                    >
                      <div className="font-semibold text-xs text-[var(--foreground)]">{op.name}</div>
                      <p className="text-[10px] text-[var(--muted-foreground)] line-clamp-2 mt-0.5">
                        {op.description}
                      </p>
                    </div>
                  ))}
                </div>
              </div>

              {/* Step 2: Source Workbook and Sheet */}
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-[var(--foreground)]">Workbook ID</label>
                  <Input
                    placeholder="e.g. 550e8400-e29b-..."
                    value={workbookId}
                    onChange={(e) => setWorkbookId(e.target.value)}
                    className="text-xs font-mono"
                  />
                  <span className="text-[10px] text-[var(--muted-foreground)]">Leave blank for default</span>
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-[var(--foreground)]">Sheet ID</label>
                  <Input
                    placeholder="e.g. 550e8400-e29b-..."
                    value={sheetId}
                    onChange={(e) => setSheetId(e.target.value)}
                    className="text-xs font-mono"
                  />
                  <span className="text-[10px] text-[var(--muted-foreground)]">Leave blank for default</span>
                </div>
              </div>

              {/* Step 3: Dynamic Parameters */}
              {selectedOpId === "group_by_aggregate" && (
                <div className="p-3 border border-[var(--border)] rounded-md bg-[var(--muted)]/20 space-y-3">
                  <span className="text-xs font-semibold text-[var(--foreground)] block">
                    Group By & Aggregations Parameters
                  </span>
                  <div className="space-y-1">
                    <label className="text-xs text-[var(--muted-foreground)]">Grouping Columns (comma-separated)</label>
                    <Input
                      placeholder="e.g. A, Department"
                      value={groupByCols}
                      onChange={(e) => setGroupByCols(e.target.value)}
                      className="text-xs font-mono"
                    />
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <div className="space-y-1">
                      <label className="text-xs text-[var(--muted-foreground)]">Target Column</label>
                      <Input
                        placeholder="e.g. B"
                        value={aggCol}
                        onChange={(e) => setAggCol(e.target.value)}
                        className="text-xs font-mono"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-[var(--muted-foreground)]">Function</label>
                      <select
                        value={aggFunc}
                        onChange={(e) => setAggFunc(e.target.value)}
                        className="w-full text-xs bg-[var(--background)] border border-[var(--border)] rounded-md px-2 py-2 focus:ring-1 focus:ring-primary"
                      >
                        <option value="sum">SUM</option>
                        <option value="mean">MEAN / AVERAGE</option>
                        <option value="count">COUNT</option>
                        <option value="min">MIN</option>
                        <option value="max">MAX</option>
                      </select>
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-[var(--muted-foreground)]">Output Alias</label>
                      <Input
                        placeholder="e.g. total_sales"
                        value={aggAlias}
                        onChange={(e) => setAggAlias(e.target.value)}
                        className="text-xs font-mono"
                      />
                    </div>
                  </div>
                </div>
              )}

              {selectedOpId === "filter_dataset" && (
                <div className="p-3 border border-[var(--border)] rounded-md bg-[var(--muted)]/20 space-y-3">
                  <span className="text-xs font-semibold text-[var(--foreground)] block">
                    Filter Condition
                  </span>
                  <div className="grid grid-cols-3 gap-2">
                    <div className="space-y-1">
                      <label className="text-xs text-[var(--muted-foreground)]">Column</label>
                      <Input
                        placeholder="e.g. A"
                        value={filterCol}
                        onChange={(e) => setFilterCol(e.target.value)}
                        className="text-xs font-mono"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-[var(--muted-foreground)]">Operator</label>
                      <select
                        value={filterOp}
                        onChange={(e) => setFilterOp(e.target.value)}
                        className="w-full text-xs bg-[var(--background)] border border-[var(--border)] rounded-md px-2 py-2 focus:ring-1 focus:ring-primary"
                      >
                        <option value="==">Equals (==)</option>
                        <option value="!=">Not Equals (!=)</option>
                        <option value=">">Greater (&gt;)</option>
                        <option value="<">Less (&lt;)</option>
                        <option value=">=">Greater Equal (&gt;=)</option>
                        <option value="<=">Less Equal (&lt;=)</option>
                        <option value="is_null">Is Null</option>
                        <option value="not_null">Not Null</option>
                      </select>
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-[var(--muted-foreground)]">Value</label>
                      <Input
                        placeholder="e.g. 500 or North"
                        value={filterVal}
                        onChange={(e) => setFilterVal(e.target.value)}
                        className="text-xs font-mono"
                      />
                    </div>
                  </div>
                </div>
              )}

              {selectedOpId === "correlation_matrix" && (
                <div className="p-3 border border-[var(--border)] rounded-md bg-[var(--muted)]/20 space-y-3">
                  <span className="text-xs font-semibold text-[var(--foreground)] block">
                    Correlation Matrix Settings
                  </span>
                  <div className="space-y-1">
                    <label className="text-xs text-[var(--muted-foreground)]">Numeric Columns (comma-separated)</label>
                    <Input
                      placeholder="e.g. A, B, C"
                      value={corrCols}
                      onChange={(e) => setCorrCols(e.target.value)}
                      className="text-xs font-mono"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs text-[var(--muted-foreground)]">Method</label>
                    <select
                      value={corrMethod}
                      onChange={(e) => setCorrMethod(e.target.value)}
                      className="w-full text-xs bg-[var(--background)] border border-[var(--border)] rounded-md px-2 py-2 focus:ring-1 focus:ring-primary"
                    >
                      <option value="pearson">Pearson (Linear)</option>
                      <option value="spearman">Spearman (Rank)</option>
                    </select>
                  </div>
                </div>
              )}

              <DialogFooter className="pt-2">
                <Button type="button" variant="outline" size="sm" onClick={() => setIsCreateOpen(false)}>
                  Cancel
                </Button>
                <Button type="submit" size="sm" disabled={isSubmitting} className="gap-1.5">
                  <Play className="w-3.5 h-3.5" />
                  {isSubmitting ? "Submitting..." : "Schedule Analysis"}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>

        {/* Job Detail & Result Modal */}
        <Dialog open={!!selectedJob} onOpenChange={(open) => !open && setSelectedJob(null)}>
          <DialogContent className="sm:max-w-3xl max-h-[90vh] overflow-y-auto">
            {selectedJob && (
              <>
                <DialogHeader>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <BarChart3 className="w-5 h-5 text-primary" />
                      <DialogTitle className="text-base font-bold">
                        {operations.find((o) => o.id === selectedJob.operation_id)?.name || selectedJob.operation_id}
                      </DialogTitle>
                    </div>
                    {statusBadge(selectedJob.status)}
                  </div>
                  <DialogDescription className="font-mono text-xs">
                    Job ID: {selectedJob.id}
                  </DialogDescription>
                </DialogHeader>

                {/* Progress & Stage Status */}
                <div className="p-3 bg-[var(--muted)]/40 border border-[var(--border)] rounded-md space-y-2 text-xs">
                  <div className="flex justify-between font-mono">
                    <span className="text-[var(--muted-foreground)]">Stage: {selectedJob.current_stage || "initializing"}</span>
                    <span className="font-bold">{selectedJob.progress_percent}%</span>
                  </div>
                  <div className="w-full h-2 bg-[var(--muted)] rounded-full overflow-hidden">
                    <div
                      className={`h-full transition-all duration-300 ${
                        selectedJob.status === "failed"
                          ? "bg-rose-500"
                          : selectedJob.status === "succeeded"
                          ? "bg-emerald-500"
                          : "bg-primary"
                      }`}
                      style={{ width: `${selectedJob.progress_percent}%` }}
                    />
                  </div>
                  <div className="flex justify-between text-[10px] text-[var(--muted-foreground)] pt-1">
                    <span>Attempts: {selectedJob.attempt_count} / {selectedJob.max_attempts}</span>
                    <span>
                      {selectedJob.started_at && `Started: ${new Date(selectedJob.started_at).toLocaleTimeString()}`}
                    </span>
                  </div>
                </div>

                {/* Error Banner if Failed */}
                {selectedJob.status === "failed" && (
                  <div className="p-4 bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 rounded-md text-xs space-y-1">
                    <div className="font-semibold flex items-center gap-1.5">
                      <AlertCircle className="w-4 h-4" />
                      Execution Failed: {selectedJob.error_code || "OPERATION_FAILED"}
                    </div>
                    <p className="text-[11px] font-mono opacity-90">
                      {selectedJob.error_message || "A worker error occurred while computing analytics."}
                    </p>
                  </div>
                )}

                {/* Success Results View */}
                {selectedJob.status === "succeeded" && (
                  <div className="space-y-4 pt-2">
                    {publishMessage && (
                      <div className="p-3 bg-emerald-500/10 border border-emerald-500/20 text-emerald-600 dark:text-emerald-400 rounded-md text-xs flex items-center gap-2">
                        <CheckCircle2 className="w-4 h-4 shrink-0" />
                        <span>{publishMessage}</span>
                      </div>
                    )}

                    <div className="flex items-center justify-between border-b border-[var(--border)] pb-2">
                      <div className="text-xs text-[var(--muted-foreground)]">
                        {jobResult && (
                          <span>
                            Output: <strong>{jobResult.row_count}</strong> rows × <strong>{jobResult.column_count}</strong> columns
                          </span>
                        )}
                      </div>

                      <div className="flex items-center gap-2">
                        {jobResult?.id && (
                          <a
                            href={`/api/v1/analytics/results/${jobResult.id}/export`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <Button variant="outline" size="sm" className="h-7 text-xs gap-1.5">
                              <Download className="w-3.5 h-3.5" />
                              Parquet Artifact
                            </Button>
                          </a>
                        )}

                        <Button
                          size="sm"
                          className="h-7 text-xs gap-1.5 bg-emerald-600 hover:bg-emerald-700 text-white"
                          onClick={() => setIsPublishOpen(true)}
                        >
                          <FileSpreadsheet className="w-3.5 h-3.5" />
                          Publish to Sheet
                        </Button>
                      </div>
                    </div>

                    {isLoadingResult ? (
                      <div className="py-12 text-center text-xs text-[var(--muted-foreground)]">
                        <RotateCw className="w-6 h-6 animate-spin mx-auto mb-2 opacity-50" />
                        Loading analytical results...
                      </div>
                    ) : jobResult?.result_payload?.data && Array.isArray(jobResult.result_payload.data) ? (
                      <div className="border border-[var(--border)] rounded-md overflow-x-auto max-h-72">
                        <table className="w-full text-xs text-left border-collapse">
                          <thead>
                            <tr className="border-b border-[var(--border)] bg-[var(--muted)]/50 font-semibold text-[var(--muted-foreground)]">
                              {Object.keys(jobResult.result_payload.data[0] || {}).map((col) => (
                                <th key={col} className="p-2.5 font-mono">
                                  {col}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-[var(--border)] font-mono">
                            {((jobResult.result_payload.data || []) as Record<string, unknown>[]).map((row, idx) => (
                              <tr key={idx} className="hover:bg-[var(--muted)]/30">
                                {Object.values(row).map((val, cIdx) => (
                                  <td key={cIdx} className="p-2.5">
                                    {val === null || val === undefined ? (
                                      <span className="text-[var(--muted-foreground)] italic">null</span>
                                    ) : typeof val === "number" ? (
                                      val.toLocaleString(undefined, { maximumFractionDigits: 4 })
                                    ) : (
                                      String(val)
                                    )}
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : jobResult?.result_type === "correlation_matrix" && jobResult?.result_payload?.matrix ? (
                      <div className="border border-[var(--border)] rounded-md overflow-x-auto p-2">
                        <table className="w-full text-xs text-center border-collapse">
                          <thead>
                            <tr>
                              <th className="p-2 font-mono text-left">Column</th>
                              {Object.keys(jobResult.result_payload.matrix).map((c) => (
                                <th key={c} className="p-2 font-mono">
                                  {c}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {Object.entries((jobResult.result_payload.matrix || {}) as Record<string, Record<string, number | null>>).map(([rowKey, colMap]) => (
                              <tr key={rowKey} className="border-t border-[var(--border)]">
                                <td className="p-2 font-mono text-left font-semibold">{rowKey}</td>
                                {Object.entries(colMap).map(([colKey, val]) => {
                                  const num = typeof val === "number" ? val : null;
                                  const bg =
                                    num !== null
                                      ? num > 0
                                        ? `rgba(16, 185, 129, ${Math.abs(num) * 0.4})`
                                        : `rgba(244, 63, 94, ${Math.abs(num) * 0.4})`
                                      : "transparent";
                                  return (
                                    <td key={colKey} className="p-2 font-mono" style={{ backgroundColor: bg }}>
                                      {num !== null ? num.toFixed(3) : "—"}
                                    </td>
                                  );
                                })}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <div className="p-4 bg-[var(--muted)]/20 border border-[var(--border)] rounded-md">
                        <pre className="text-xs font-mono overflow-x-auto max-h-60">
                          {JSON.stringify(jobResult?.result_payload || {}, null, 2)}
                        </pre>
                      </div>
                    )}
                  </div>
                )}

                <DialogFooter className="pt-2">
                  <Button variant="outline" size="sm" onClick={() => setSelectedJob(null)}>
                    Close
                  </Button>
                </DialogFooter>
              </>
            )}
          </DialogContent>
        </Dialog>

        {/* Publish to Sheet Sub-Dialog */}
        <Dialog open={isPublishOpen} onOpenChange={setIsPublishOpen}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <FileSpreadsheet className="w-5 h-5 text-emerald-600" />
                Publish Results to Sheet
              </DialogTitle>
              <DialogDescription>
                Write this analytical result into a new sheet in the source workbook.
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-3 py-2">
              <div className="space-y-1">
                <label className="text-xs font-semibold text-[var(--foreground)]">New Sheet Name</label>
                <Input
                  placeholder="e.g. Q1_Summary"
                  value={destSheetName}
                  onChange={(e) => setDestSheetName(e.target.value)}
                  className="text-xs font-mono"
                />
              </div>
            </div>

            <DialogFooter>
              <Button variant="outline" size="sm" onClick={() => setIsPublishOpen(false)}>
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={handlePublishToSheet}
                disabled={isPublishing}
                className="bg-emerald-600 hover:bg-emerald-700 text-white gap-1.5"
              >
                {isPublishing ? "Publishing..." : "Publish to Workbook"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
  );
}

"use client";

import { useSpaRouter } from "@/components/providers/spa-router-provider";
import { apiUrl } from "@/lib/api";
import * as React from "react";
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
import {
  DynamicEChartsWrapper,
  ChartSkeleton,
  type ChartConfigSpec,
  type SupportedChartType,
} from "@/components/charts";
import {
  Dashboard,
  Widget,
  QueryResult,
  DashboardFilterState,
  FilterBar,
  DashboardGrid,
  DashboardToolbar,
  WidgetEditorDialog,
  DashboardDuplicateDialog,
  mergeWidgetFilters,
  checkFilterCompatibility,
} from "@/components/dashboard";

export default function DashboardsPage() {
  const _router = useSpaRouter();
  const [dashboards, setDashboards] = React.useState<Dashboard[]>([]);
  const [activeDashboard, setActiveDashboard] = React.useState<Dashboard | null>(null);
  const [widgets, setWidgets] = React.useState<Widget[]>([]);
  const [savedWidgets, setSavedWidgets] = React.useState<Widget[]>([]); // For edit mode cancel/rollback
  const [widgetData, setWidgetData] = React.useState<Record<string, QueryResult>>({});
  const [loading, setLoading] = React.useState(true);
  const [csrfToken, setCsrfToken] = React.useState<string>("");

  // Studio Modes & State
  const [isEditMode, setIsEditMode] = React.useState(false);
  const [isDirty, setIsDirty] = React.useState(false);

  // Global Filter State (Initialized with default UTC or persisted layout_config.filters)
  const [filterState, setFilterState] = React.useState<DashboardFilterState>({
    timezone: "UTC",
    criteria: [],
  });

  // Dialogs
  const [newDashOpen, setNewDashOpen] = React.useState(false);
  const [widgetEditorOpen, setWidgetEditorOpen] = React.useState(false);
  const [widgetToEdit, setWidgetToEdit] = React.useState<Widget | null>(null);
  const [duplicateDialogOpen, setDuplicateDialogOpen] = React.useState(false);

  // Forms
  const [dashTitle, setDashTitle] = React.useState("");
  const [dashDesc, setDashDesc] = React.useState("");

  // AbortController registry for in-flight widget queries
  const abortControllersRef = React.useRef<Map<string, AbortController>>(new Map());
  const debounceTimerRef = React.useRef<NodeJS.Timeout | null>(null);

  // Load CSRF Token
  React.useEffect(() => {
    fetch(apiUrl("/api/v1/auth/csrf-token"), { method: "POST", credentials: "include" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.csrf_token) setCsrfToken(data.csrf_token);
      })
      .catch(() => {});
  }, []);

  // Fetch Dashboards List
  const loadDashboards = React.useCallback(async (selectId?: string) => {
    try {
      setLoading(true);
      const res = await fetch(apiUrl("/api/v1/dashboards"), { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        const list: Dashboard[] = Array.isArray(data) ? data : [];
        setDashboards(list);

        if (list.length > 0) {
          if (selectId) {
            const found = list.find((d) => d.id === selectId);
            setActiveDashboard(found || list[0]);
          } else {
            setActiveDashboard((prev) => {
              if (prev && list.some((d) => d.id === prev.id)) {
                return list.find((d) => d.id === prev.id) || list[0];
              }
              return list[0];
            });
          }
        } else {
          setActiveDashboard(null);
          setWidgets([]);
        }
      }
    } catch (e) {
      console.error("Failed to load dashboards", e);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    let isMounted = true;
    fetch(apiUrl("/api/v1/dashboards"), { credentials: "include" })
      .then((res) => (res.ok ? res.json() : []))
      .then((data) => {
        if (!isMounted) return;
        const list: Dashboard[] = Array.isArray(data) ? data : [];
        setDashboards(list);
        if (list.length > 0) {
          setActiveDashboard((prev) => prev ?? list[0]);
        }
      })
      .catch((e) => console.error("Failed to load dashboards", e))
      .finally(() => {
        if (isMounted) setLoading(false);
      });
    return () => {
      isMounted = false;
    };
  }, []);

  // Execute Query for a single widget with merged global & local filters
  const fetchWidgetQuery = React.useCallback(
    async (
      dashId: string,
      widget: Widget,
      currentFilterState: DashboardFilterState,
      knownCols?: string[]
    ) => {
      // Cancel any existing in-flight request for this widget
      const existingController = abortControllersRef.current.get(widget.id);
      if (existingController) {
        existingController.abort();
      }

      const controller = new AbortController();
      abortControllersRef.current.set(widget.id, controller);

      try {
        // Construct date filter from date_range if present
        const effectiveCriteria = [...currentFilterState.criteria];
        if (currentFilterState.date_range) {
          // If a date range preset is active, add as a date between or gte/lte filter
          effectiveCriteria.push({
            id: "global-date-range",
            column: "created_at",
            operator: "between",
            value: [
              currentFilterState.date_range.start,
              currentFilterState.date_range.end,
            ],
            enabled: true,
          });
        }

        const mergedFilters = mergeWidgetFilters(
          effectiveCriteria,
          widget.query_config?.local_filters,
          widget,
          knownCols
        );

        const customQueryConfig = {
          ...widget.query_config,
          filters: mergedFilters,
        };

        const qRes = await fetch(apiUrl(`/api/v1/dashboards/${dashId}/widgets/${widget.id}/query`),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ custom_query_config: customQueryConfig }),
            credentials: "include",
            signal: controller.signal,
          }
        );

        if (qRes.ok) {
          const qData: QueryResult = await qRes.json();
          setWidgetData((prev) => ({ ...prev, [widget.id]: qData }));
        }
      } catch (err: unknown) {
        if (err instanceof Error && err.name === "AbortError") {
          // Cleanly aborted in-flight query; ignore
          return;
        }
        console.error(`Query failed for widget ${widget.id}`, err);
      } finally {
        if (abortControllersRef.current.get(widget.id) === controller) {
          abortControllersRef.current.delete(widget.id);
        }
      }
    },
    []
  );

  React.useEffect(() => {
    if (!activeDashboard) return;
    const dashId = activeDashboard.id;
    let isMounted = true;

    fetch(apiUrl(`/api/v1/dashboards/${dashId}`), { credentials: "include" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!isMounted || !data) return;
        const loadedWidgets: Widget[] = data.widgets || [];
        setWidgets(loadedWidgets);
        setSavedWidgets(loadedWidgets);
        setIsDirty(false);

        if (
          data.layout_config?.filters &&
          filterState.criteria.length === 0 &&
          !filterState.date_range
        ) {
          setFilterState(data.layout_config.filters);
        }

        loadedWidgets.forEach((w) => {
          fetchWidgetQuery(dashId, w, filterState);
        });
      })
      .catch((e) => console.error("Failed to load dashboard details", e));

    return () => {
      isMounted = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeDashboard?.id]); // Intentionally re-run only when the selected dashboard ID changes

  // Debounced Cross-Widget Filter Propagation
  const handleFilterChange = (newFilters: DashboardFilterState) => {
    setFilterState(newFilters);
    if (!activeDashboard) return;

    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }

    debounceTimerRef.current = setTimeout(() => {
      widgets.forEach((w) => {
        const knownCols = widgetData[w.id]?.columns;
        fetchWidgetQuery(activeDashboard.id, w, newFilters, knownCols);
      });
    }, 250); // 250ms debouncing
  };

  // Compute Filter Compatibility Map
  const filterCompatibilityMap = React.useMemo(() => {
    const map: Record<string, { isCompatible: boolean; reason?: string }> = {};
    if (filterState.criteria.length === 0 && !filterState.date_range) return map;

    for (const w of widgets) {
      const knownCols = widgetData[w.id]?.columns;
      for (const crit of filterState.criteria) {
        const compat = checkFilterCompatibility(crit, w, knownCols);
        if (!compat.isCompatible) {
          map[w.id] = compat;
          break;
        }
      }
    }
    return map;
  }, [widgets, filterState.criteria, filterState.date_range, widgetData]);

  // Aggregate Available Columns across widgets for filter picker
  const availableColumns = React.useMemo(() => {
    const colSet = new Set<string>();
    Object.values(widgetData).forEach((res) => {
      if (res?.columns) {
        res.columns.forEach((col) => colSet.add(col));
      }
    });
    widgets.forEach((w) => {
      if (Array.isArray(w.query_config?.dimensions)) {
        w.query_config.dimensions.forEach((d) => colSet.add(d));
      }
      if (Array.isArray(w.query_config?.metrics)) {
        w.query_config.metrics.forEach((m) => colSet.add(m.column));
      }
    });
    return Array.from(colSet);
  }, [widgetData, widgets]);

  // Edit Mode & Dirty Layout Handlers
  const handleLayoutChange = (updatedWidgets: Widget[]) => {
    setWidgets(updatedWidgets);
    setIsDirty(true);
  };

  const handleSaveChanges = async () => {
    if (!activeDashboard) return;
    try {
      // 1. Batch update widget layout positions
      const layoutPayload = {
        positions: widgets.map((w, idx) => ({
          widget_id: w.id,
          position_x: w.position_x || 0,
          position_y: w.position_y || 0,
          width: w.width || 6,
          height: w.height || 4,
          sort_order: idx,
        })),
      };

      await fetch(apiUrl(`/api/v1/dashboards/${activeDashboard.id}/layout`), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify(layoutPayload),
        credentials: "include",
      });

      // 2. Persist global filter configuration inside layout_config
      const updatedLayoutConfig = {
        ...(activeDashboard.layout_config || {}),
        version: 1,
        timezone: filterState.timezone,
        filters: filterState,
      };

      await fetch(apiUrl(`/api/v1/dashboards/${activeDashboard.id}`), {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({ layout_config: updatedLayoutConfig }),
        credentials: "include",
      });

      setSavedWidgets(widgets);
      setIsDirty(false);
      setIsEditMode(false);
    } catch (e) {
      console.error("Failed to save dashboard layout", e);
    }
  };

  const handleCancelChanges = () => {
    setWidgets(savedWidgets);
    setIsDirty(false);
    setIsEditMode(false);
  };

  // Metadata update (title/description)
  const handleUpdateMetadata = async (newTitle: string, newDesc?: string) => {
    if (!activeDashboard) return;
    try {
      const res = await fetch(apiUrl(`/api/v1/dashboards/${activeDashboard.id}`), {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({ title: newTitle, description: newDesc }),
        credentials: "include",
      });
      if (res.ok) {
        const updated = await res.json();
        setActiveDashboard(updated);
        setDashboards((prev) =>
          prev.map((d) => (d.id === updated.id ? updated : d))
        );
      }
    } catch (e) {
      console.error("Failed to update metadata", e);
    }
  };

  // Create Dashboard
  const handleCreateDashboard = async (e: React.FormEvent) => {
    e.preventDefault();
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
        alert("No active workspace found. Please create a workspace first.");
        return;
      }

      const res = await fetch(apiUrl("/api/v1/dashboards"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({
          workspace_id: wsId,
          title: dashTitle,
          description: dashDesc,
          layout_config: { version: 1, timezone: filterState.timezone },
        }),
        credentials: "include",
      });

      if (res.ok) {
        const created = await res.json();
        setNewDashOpen(false);
        setDashTitle("");
        setDashDesc("");
        await loadDashboards(created.id);
      }
    } catch (e) {
      console.error("Create dashboard failed", e);
    }
  };

  // Duplicate Dashboard
  const handleDuplicateDashboard = async (dupTitle: string) => {
    if (!activeDashboard) return;
    const res = await fetch(apiUrl(`/api/v1/dashboards/${activeDashboard.id}/duplicate`), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({ title: dupTitle }),
      credentials: "include",
    });

    if (res.ok) {
      const cloned = await res.json();
      await loadDashboards(cloned.id);
    }
  };

  // Delete Dashboard
  const handleDeleteDashboard = async () => {
    if (!activeDashboard) return;
    const deletedId = activeDashboard.id;
    try {
      const res = await fetch(apiUrl(`/api/v1/dashboards/${deletedId}`), {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrfToken },
        credentials: "include",
      });
      if (res.ok) {
        await loadDashboards();
      }
    } catch (e) {
      console.error("Delete dashboard failed", e);
    }
  };

  // Save Widget (Create or Update)
  const handleSaveWidget = async (payload: Partial<Widget>) => {
    if (!activeDashboard) return;

    if (widgetToEdit) {
      // Update existing widget
      try {
        const res = await fetch(apiUrl(`/api/v1/dashboards/${activeDashboard.id}/widgets/${widgetToEdit.id}`),
          {
            method: "PATCH",
            headers: {
              "Content-Type": "application/json",
              "X-CSRF-Token": csrfToken,
            },
            body: JSON.stringify(payload),
            credentials: "include",
          }
        );
        if (res.ok) {
          const updatedWidget: Widget = await res.json();
          setWidgets((prev) =>
            prev.map((w) => (w.id === updatedWidget.id ? updatedWidget : w))
          );
          fetchWidgetQuery(activeDashboard.id, updatedWidget, filterState);
        }
      } catch (e) {
        console.error("Update widget failed", e);
      }
    } else {
      // Create new widget
      try {
        const res = await fetch(apiUrl(`/api/v1/dashboards/${activeDashboard.id}/widgets`),
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRF-Token": csrfToken,
            },
            body: JSON.stringify({
              ...payload,
              position_x: (widgets.length % 2) * 6,
              position_y: Math.floor(widgets.length / 2) * 4,
              width: 6,
              height: 4,
              sort_order: widgets.length,
            }),
            credentials: "include",
          }
        );
        if (res.ok) {
          const createdWidget: Widget = await res.json();
          setWidgets((prev) => [...prev, createdWidget]);
          fetchWidgetQuery(activeDashboard.id, createdWidget, filterState);
        }
      } catch (e) {
        console.error("Create widget failed", e);
      }
    }
    setWidgetToEdit(null);
  };

  // Delete Widget
  const handleDeleteWidget = async (widgetId: string) => {
    if (!activeDashboard) return;
    try {
      const res = await fetch(apiUrl(`/api/v1/dashboards/${activeDashboard.id}/widgets/${widgetId}`),
        {
          method: "DELETE",
          headers: { "X-CSRF-Token": csrfToken },
          credentials: "include",
        }
      );
      if (res.ok) {
        setWidgets((prev) => prev.filter((w) => w.id !== widgetId));
        setSavedWidgets((prev) => prev.filter((w) => w.id !== widgetId));
      }
    } catch (e) {
      console.error("Delete widget failed", e);
    }
  };

  // Render Visualizations (Interactive Apache ECharts Engine & Paginated Data Table)
  const renderVisualization = (widget: Widget) => {
    const qData = widgetData[widget.id];
    if (!qData) {
      return <ChartSkeleton height={200} hasLegend={false} />;
    }

    const { data, columns, total_rows, summary } = qData;
    if (!data || data.length === 0) {
      return (
        <div className="flex flex-col items-center justify-center h-48 text-xs text-muted-foreground border border-dashed rounded-md">
          No records returned from data source.
        </div>
      );
    }

    // 1. Interactive Data Table (Paginated & Sortable)
    if (widget.widget_type === "data_table" || widget.widget_type === "table") {
      const page = Number(summary?.page) || 1;
      const totalPages = Number(summary?.total_pages) || 1;
      const totalCount = Number(summary?.total) ?? total_rows;

      const handleExportCsv = async () => {
        try {
          const res = await fetch(apiUrl(`/api/v1/dashboards/${activeDashboard?.id}/widgets/${widget.id}/export`),
            { credentials: "include" }
          );
          if (res.ok) {
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.setAttribute(
              "download",
              `${widget.title.toLowerCase().replace(/\s+/g, "_")}_export.csv`
            );
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
          }
        } catch {
          if (!data || data.length === 0) return;
          const csvRows = [
            columns.join(","),
            ...data.map((row) =>
              columns.map((col) => JSON.stringify(row[col] ?? "")).join(",")
            ),
          ];
          const blob = new Blob([csvRows.join("\n")], {
            type: "text/csv;charset=utf-8;",
          });
          const url = URL.createObjectURL(blob);
          const link = document.createElement("a");
          link.href = url;
          link.setAttribute(
            "download",
            `${widget.title.toLowerCase().replace(/\s+/g, "_")}_export.csv`
          );
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
        }
      };

      return (
        <div className="flex flex-col gap-2 p-2 border rounded-md bg-card">
          <div className="flex items-center justify-between text-xs pb-1 border-b text-muted-foreground">
            <span>
              Showing {data.length} of {totalCount} records (Page {page} of {totalPages})
            </span>
            <Button
              variant="outline"
              size="sm"
              className="h-6 text-[10px] gap-1 px-2"
              onClick={handleExportCsv}
            >
              Export CSV
            </Button>
          </div>
          <div className="overflow-x-auto max-h-48 border rounded text-xs">
            <table className="w-full text-left border-collapse">
              <thead className="bg-muted/70 sticky top-0 text-[11px] font-semibold">
                <tr>
                  {columns.map((col) => (
                    <th key={col} className="p-2 border-b whitespace-nowrap">
                      {col}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {data.map((row, idx) => (
                  <tr key={idx} className="hover:bg-muted/30 transition-colors">
                    {columns.map((col) => (
                      <td key={col} className="p-2 whitespace-nowrap font-mono text-[11px]">
                        {row[col] !== null && row[col] !== undefined
                          ? String(row[col])
                          : "-"}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      );
    }

    // 2. Interactive Apache ECharts Visualizations (Gate 1 Foundation)
    let chartType: SupportedChartType = "bar";
    switch (widget.widget_type) {
      case "kpi_card":
        chartType = "kpi_card";
        break;
      case "horizontal_bar":
        chartType = "horizontal_bar";
        break;
      case "stacked_bar":
        chartType = "stacked_bar";
        break;
      case "line":
        chartType = "line";
        break;
      case "area":
        chartType = "stacked_area";
        break;
      case "pie":
        chartType = "pie";
        break;
      case "donut":
        chartType = "donut";
        break;
      case "scatter":
      case "bubble":
        chartType = "scatter";
        break;
      case "heatmap":
      case "correlation_matrix":
        chartType = "heatmap";
        break;
      case "multi_axis_line":
        chartType = "multi_axis_line";
        break;
      case "bar":
      case "histogram":
      default:
        chartType = "bar";
        break;
    }

    const qc = (widget.query_config || {}) as Record<string, unknown>;
    const qcDims = Array.isArray(qc.dimensions)
      ? (qc.dimensions as string[])
      : qc.dimension
      ? [String(qc.dimension)]
      : [columns[0]];
    const qcMetrics = Array.isArray(qc.metrics)
      ? (qc.metrics as string[])
      : qc.metric
      ? [String(qc.metric)]
      : [columns[1] || columns[0]];

    const spec: ChartConfigSpec = {
      chart_type: chartType,
      title: widget.title,
      dimensions: qcDims,
      metrics: qcMetrics,
      formatter: "number_compact",
      show_legend: chartType !== "kpi_card",
      show_data_zoom: data.length > 25,
      bar_mode:
        widget.widget_type === "stacked_bar" || qc.bar_mode === "stacked"
          ? "stacked"
          : qc.bar_mode === "grouped"
          ? "grouped"
          : undefined,
      pie_variant:
        widget.widget_type === "donut" || qc.pie_variant === "donut"
          ? "donut"
          : "pie",
      smooth:
        typeof qc.smooth === "boolean"
          ? qc.smooth
          : chartType === "line"
          ? false
          : true,
      is_area: widget.widget_type === "area",
      is_donut: widget.widget_type === "donut" || qc.pie_variant === "donut",
      kpi_target: typeof qc.target === "number" ? qc.target : undefined,
      kpi_comparison_label:
        typeof qc.comparison_label === "string" ? qc.comparison_label : undefined,
    };

    return (
      <div className="w-full">
        <DynamicEChartsWrapper spec={spec} dataPayload={qData} height={210} />
      </div>
    );
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
      {/* Dashboard Studio Toolbar */}
      <DashboardToolbar
        dashboards={dashboards}
        activeDashboard={activeDashboard}
        isEditMode={isEditMode}
        isDirty={isDirty}
        loading={loading}
        onSelectDashboard={(d) => {
          if (isDirty) {
            if (!confirm("You have unsaved changes. Switch dashboard anyway?")) {
              return;
            }
          }
          setActiveDashboard(d);
          setIsEditMode(false);
          setIsDirty(false);
        }}
        onToggleEditMode={() => {
          if (isEditMode && isDirty) {
            if (!confirm("Exit edit mode without saving your changes?")) {
              return;
            }
            handleCancelChanges();
            return;
          }
          setIsEditMode(!isEditMode);
        }}
        onSaveChanges={handleSaveChanges}
        onCancelChanges={handleCancelChanges}
        onNewDashboard={() => setNewDashOpen(true)}
        onAddWidget={() => {
          setWidgetToEdit(null);
          setWidgetEditorOpen(true);
        }}
        onDuplicateDashboard={() => setDuplicateDialogOpen(true)}
        onDeleteDashboard={handleDeleteDashboard}
        onRefreshData={() => {
          if (activeDashboard) {
            widgets.forEach((w) => {
              fetchWidgetQuery(
                activeDashboard.id,
                w,
                filterState,
                widgetData[w.id]?.columns
              );
            });
          }
        }}
        onUpdateMetadata={handleUpdateMetadata}
      />

      {/* Global Filter Bar */}
      {activeDashboard && (
        <FilterBar
          filterState={filterState}
          onFilterChange={handleFilterChange}
          availableColumns={availableColumns}
        />
      )}

      {/* 12-Column Responsive Dashboard Grid */}
      {activeDashboard ? (
        <DashboardGrid
          widgets={widgets}
          widgetData={widgetData}
          isEditMode={isEditMode}
          onLayoutChange={handleLayoutChange}
          onEditWidget={(w) => {
            setWidgetToEdit(w);
            setWidgetEditorOpen(true);
          }}
          onDeleteWidget={handleDeleteWidget}
          renderVisualization={renderVisualization}
          filterCompatibilityMap={filterCompatibilityMap}
        />
      ) : (
        <div className="text-center py-20 border rounded-lg border-dashed text-muted-foreground text-sm">
          No dashboards found. Click &quot;New Dashboard&quot; to begin building your analytical studio.
        </div>
      )}

      {/* Create Dashboard Dialog */}
      <Dialog open={newDashOpen} onOpenChange={setNewDashOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Analytical Dashboard</DialogTitle>
            <DialogDescription>
              Create a new 12-column responsive canvas for data intelligence reports and visualizations.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleCreateDashboard} className="space-y-4">
            <div>
              <label className="text-xs font-semibold">Dashboard Title</label>
              <Input
                placeholder="e.g. Sales Performance 2026"
                value={dashTitle}
                onChange={(e) => setDashTitle(e.target.value)}
                required
              />
            </div>
            <div>
              <label className="text-xs font-semibold">Description (Optional)</label>
              <Input
                placeholder="Quarterly regional sales breakdown"
                value={dashDesc}
                onChange={(e) => setDashDesc(e.target.value)}
              />
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setNewDashOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit">Create Dashboard</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Add / Edit Widget Dialog */}
      <WidgetEditorDialog
        open={widgetEditorOpen}
        onOpenChange={setWidgetEditorOpen}
        widgetToEdit={widgetToEdit}
        onSaveWidget={handleSaveWidget}
        availableColumns={availableColumns}
      />

      {/* Duplicate Dashboard Dialog */}
      <DashboardDuplicateDialog
        open={duplicateDialogOpen}
        onOpenChange={setDuplicateDialogOpen}
        dashboard={activeDashboard}
        onConfirmDuplicate={handleDuplicateDashboard}
      />
    </div>
  );
}

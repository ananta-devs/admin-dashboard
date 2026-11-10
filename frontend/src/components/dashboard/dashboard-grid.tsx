"use client";

import * as React from "react";
import {
  GripVertical,
  Maximize2,
  Minimize2,
  MoveLeft,
  MoveRight,
  Settings,
  Trash2,
  Sliders,
  AlertCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Widget, QueryResult } from "./dashboard-types";

/**
 * Model A — Ordered Responsive Grid Layout Engine
 *
 * Product Architecture Contract (DEF-05 Resolution):
 * - Widgets are arranged in a deterministic, sequential sort_order.
 * - Width spans are responsive: desktop (12 cols), tablet (6 cols), mobile (1 col).
 * - Dragging reorders widgets cleanly using CSS Grid flow rather than arbitrary
 *   fixed 2D pixel coordinates, preventing overlapping and responsive layout collapse.
 * - Touchscreens and stylus interactions are supported via standard PointerEvents (DEF-04).
 */

interface DashboardGridProps {
  widgets: Widget[];
  widgetData?: Record<string, QueryResult>;
  isEditMode: boolean;
  onLayoutChange: (updatedWidgets: Widget[]) => void;
  onEditWidget: (widget: Widget) => void;
  onDeleteWidget: (widgetId: string) => void;
  renderVisualization: (widget: Widget) => React.ReactNode;
  filterCompatibilityMap?: Record<string, { isCompatible: boolean; reason?: string }>;
}

export function DashboardGrid({
  widgets,
  isEditMode,
  onLayoutChange,
  onEditWidget,
  onDeleteWidget,
  renderVisualization,
  filterCompatibilityMap = {},
}: DashboardGridProps) {
  const [draggedWidgetId, setDraggedWidgetId] = React.useState<string | null>(null);
  const [dragOverIndex, setDragOverIndex] = React.useState<number | null>(null);
  const activePointerIdRef = React.useRef<number | null>(null);

  // Drag and Drop (Native HTML5 with DragEvent)
  const handleDragStart = (e: React.DragEvent<HTMLDivElement>, widgetId: string) => {
    if (!isEditMode) return;
    setDraggedWidgetId(widgetId);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", widgetId);
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>, index: number) => {
    if (!isEditMode || !draggedWidgetId) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOverIndex(index);
  };

  const handleDragEnd = () => {
    setDraggedWidgetId(null);
    setDragOverIndex(null);
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>, targetIndex: number) => {
    e.preventDefault();
    if (!isEditMode || !draggedWidgetId) return;

    const sourceIndex = widgets.findIndex((w) => w.id === draggedWidgetId);
    if (sourceIndex === -1 || sourceIndex === targetIndex) {
      handleDragEnd();
      return;
    }

    const reordered = [...widgets];
    const [moved] = reordered.splice(sourceIndex, 1);
    reordered.splice(targetIndex, 0, moved);

    // Recompute sort_order and basic coordinates
    const updated = reordered.map((w, idx) => ({
      ...w,
      sort_order: idx,
      position_x: (idx % 2) * 6,
      position_y: Math.floor(idx / 2) * 4,
    }));

    onLayoutChange(updated);
    handleDragEnd();
  };

  // Touch & Pointer-based Dragging Support (DEF-04)
  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>, widgetId: string) => {
    if (!isEditMode || e.button !== 0) return;
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
      activePointerIdRef.current = e.pointerId;
      setDraggedWidgetId(widgetId);
    } catch {
      // Fallback gracefully if pointer capture is unsupported
    }
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!isEditMode || !draggedWidgetId || activePointerIdRef.current !== e.pointerId) return;
    const elem = document.elementFromPoint(e.clientX, e.clientY);
    const gridItem = elem?.closest("[data-grid-index]");
    if (gridItem) {
      const targetIdx = parseInt(gridItem.getAttribute("data-grid-index") || "-1", 10);
      if (targetIdx >= 0 && targetIdx < widgets.length) {
        setDragOverIndex(targetIdx);
      }
    }
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!isEditMode || !draggedWidgetId || activePointerIdRef.current !== e.pointerId) return;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {}
    activePointerIdRef.current = null;

    if (dragOverIndex !== null) {
      const sourceIndex = widgets.findIndex((w) => w.id === draggedWidgetId);
      if (sourceIndex !== -1 && sourceIndex !== dragOverIndex) {
        const reordered = [...widgets];
        const [moved] = reordered.splice(sourceIndex, 1);
        reordered.splice(dragOverIndex, 0, moved);

        const updated = reordered.map((w, idx) => ({
          ...w,
          sort_order: idx,
          position_x: (idx % 2) * 6,
          position_y: Math.floor(idx / 2) * 4,
        }));
        onLayoutChange(updated);
      }
    }
    setDraggedWidgetId(null);
    setDragOverIndex(null);
  };

  const handlePointerCancel = (e: React.PointerEvent<HTMLDivElement>) => {
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {}
    activePointerIdRef.current = null;
    setDraggedWidgetId(null);
    setDragOverIndex(null);
  };

  // Keyboard Accessible Repositioning
  const moveWidget = (index: number, direction: "left" | "right") => {
    if (!isEditMode) return;
    const targetIndex = direction === "left" ? index - 1 : index + 1;
    if (targetIndex < 0 || targetIndex >= widgets.length) return;

    const reordered = [...widgets];
    const [moved] = reordered.splice(index, 1);
    reordered.splice(targetIndex, 0, moved);

    const updated = reordered.map((w, idx) => ({
      ...w,
      sort_order: idx,
    }));
    onLayoutChange(updated);
  };

  // Resize column span (3, 4, 6, 8, 12)
  const cycleWidth = (widget: Widget) => {
    if (!isEditMode) return;
    const spans = [6, 12, 4, 8, 3];
    const current = widget.width || 6;
    const currentIndex = spans.indexOf(current);
    const nextWidth = spans[(currentIndex + 1) % spans.length];

    const updated = widgets.map((w) =>
      w.id === widget.id ? { ...w, width: nextWidth } : w
    );
    onLayoutChange(updated);
  };

  if (widgets.length === 0) {
    return (
      <div className="text-center py-20 border rounded-lg border-dashed text-muted-foreground text-sm">
        This dashboard has no widgets yet. Click &quot;Add Widget&quot; in the toolbar to configure a chart or KPI card.
      </div>
    );
  }

  return (
    <div
      role="region"
      aria-label="Dashboard widgets grid"
      className="grid grid-cols-1 md:grid-cols-6 lg:grid-cols-12 gap-6"
    >
      {widgets.map((widget, idx) => {
        const isDragging = draggedWidgetId === widget.id;
        const isDragTarget = dragOverIndex === idx && !isDragging;
        const widthSpan = widget.width || 6;
        const localFilterCount = widget.query_config?.local_filters?.length || 0;
        const compat = filterCompatibilityMap[widget.id];

        // Map column span to Tailwind class
        let colSpanClass = "lg:col-span-6 md:col-span-6 col-span-1";
        if (widthSpan === 12) colSpanClass = "lg:col-span-12 md:col-span-6 col-span-1";
        else if (widthSpan === 8) colSpanClass = "lg:col-span-8 md:col-span-6 col-span-1";
        else if (widthSpan === 4) colSpanClass = "lg:col-span-4 md:col-span-3 col-span-1";
        else if (widthSpan === 3) colSpanClass = "lg:col-span-3 md:col-span-3 col-span-1";

        return (
          <div
            key={widget.id}
            data-testid={`grid-item-${widget.id}`}
            data-grid-index={idx}
            className={`${colSpanClass} transition-all duration-150 ${
              isDragging ? "opacity-40 scale-95" : ""
            } ${
              isDragTarget ? "border-2 border-primary border-dashed rounded-lg p-0.5" : ""
            }`}
            draggable={isEditMode}
            onDragStart={(e) => handleDragStart(e, widget.id)}
            onDragOver={(e) => handleDragOver(e, idx)}
            onDragEnd={handleDragEnd}
            onDrop={(e) => handleDrop(e, idx)}
          >
            <Card className="h-full shadow-xs flex flex-col justify-between overflow-hidden">
              <CardHeader className="p-3 pb-2 flex flex-row items-center justify-between border-b border-border/40 bg-muted/20">
                <div className="flex items-center gap-2 overflow-hidden mr-2">
                  {isEditMode && (
                    <div
                      className="cursor-grab active:cursor-grabbing text-muted-foreground hover:text-foreground touch-none p-1 -m-1 select-none"
                      title="Drag to reposition widget"
                      onPointerDown={(e) => handlePointerDown(e, widget.id)}
                      onPointerMove={handlePointerMove}
                      onPointerUp={handlePointerUp}
                      onPointerCancel={handlePointerCancel}
                    >
                      <GripVertical className="h-4 w-4 pointer-events-none" />
                    </div>
                  )}
                  <div className="truncate">
                    <CardTitle className="text-sm font-semibold truncate flex items-center gap-1.5">
                      <span>{widget.title}</span>
                      <Badge variant="secondary" className="text-[10px] font-normal uppercase shrink-0">
                        {widget.widget_type.replace("_", " ")}
                      </Badge>
                      {localFilterCount > 0 && (
                        <Badge
                          variant="outline"
                          className="text-[10px] py-0 px-1.5 bg-primary/10 border-primary/20 text-primary shrink-0"
                          title={`${localFilterCount} local filters active`}
                        >
                          <Sliders className="h-2.5 w-2.5 mr-0.5 inline" />
                          {localFilterCount}
                        </Badge>
                      )}
                      {compat && !compat.isCompatible && (
                        <Badge
                          variant="outline"
                          className="text-[10px] py-0 px-1.5 border-amber-500/30 text-amber-600 dark:text-amber-400 shrink-0"
                          title={compat.reason || "Global filter not applicable to this widget"}
                        >
                          <AlertCircle className="h-2.5 w-2.5 mr-0.5 inline" />
                          Filter Inactive
                        </Badge>
                      )}
                    </CardTitle>
                    <CardDescription className="text-[11px] truncate text-muted-foreground">
                      Source: {widget.source_type} ({widget.source_id.slice(0, 8)}...)
                    </CardDescription>
                  </div>
                </div>

                {/* Card Controls */}
                <div className="flex items-center gap-1 shrink-0">
                  {isEditMode ? (
                    <>
                      {/* Keyboard Move Left */}
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-muted-foreground hover:text-foreground"
                        title="Move left"
                        aria-label={`Move ${widget.title} left`}
                        disabled={idx === 0}
                        onClick={() => moveWidget(idx, "left")}
                      >
                        <MoveLeft className="h-3.5 w-3.5" />
                      </Button>

                      {/* Keyboard Move Right */}
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-muted-foreground hover:text-foreground"
                        title="Move right"
                        aria-label={`Move ${widget.title} right`}
                        disabled={idx === widgets.length - 1}
                        onClick={() => moveWidget(idx, "right")}
                      >
                        <MoveRight className="h-3.5 w-3.5" />
                      </Button>

                      {/* Resize Width Cycle */}
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-muted-foreground hover:text-foreground"
                        title={`Current span: ${widthSpan} cols. Click to cycle.`}
                        aria-label={`Resize width for ${widget.title}`}
                        onClick={() => cycleWidth(widget)}
                      >
                        {widthSpan === 12 ? (
                          <Minimize2 className="h-3.5 w-3.5" />
                        ) : (
                          <Maximize2 className="h-3.5 w-3.5" />
                        )}
                      </Button>

                      {/* Configure Widget */}
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-muted-foreground hover:text-foreground"
                        title="Configure widget properties and local filters"
                        aria-label={`Configure ${widget.title}`}
                        onClick={() => onEditWidget(widget)}
                      >
                        <Settings className="h-3.5 w-3.5" />
                      </Button>

                      {/* Delete Widget */}
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-muted-foreground hover:text-destructive"
                        title="Remove widget"
                        aria-label={`Delete ${widget.title}`}
                        onClick={() => onDeleteWidget(widget.id)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </>
                  ) : (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 text-muted-foreground hover:text-foreground"
                      title="Configure widget"
                      aria-label={`Configure ${widget.title}`}
                      onClick={() => onEditWidget(widget)}
                    >
                      <Settings className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </div>
              </CardHeader>
              <CardContent className="p-3 flex-1 flex flex-col justify-center">
                {renderVisualization(widget)}
              </CardContent>
            </Card>
          </div>
        );
      })}
    </div>
  );
}

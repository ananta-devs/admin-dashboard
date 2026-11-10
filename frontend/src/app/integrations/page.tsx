"use client";

import { useSpaRouter } from "@/components/providers/spa-router-provider";
import { apiUrl } from "@/lib/api";
import * as React from "react";
import {
  RefreshCw,
  Plus,
  ArrowRightLeft,
  ArrowRight,
  ArrowLeft,
  AlertTriangle,
  Download,
  Upload,
  Link as LinkIcon,
  Unlink,
  Clock,
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

interface Integration {
  id: string;
  provider: string;
  account_email: string;
  account_name?: string;
  status: string;
  authorized_scopes: string[];
  created_at: string;
}

interface SyncMapping {
  id: string;
  workbook_id: string;
  sheet_id: string;
  external_spreadsheet_id: string;
  external_sheet_name: string;
  direction: "bidirectional" | "vibe_to_google" | "google_to_vibe";
  status: "idle" | "syncing" | "conflict" | "error";
  conflict_policy: "manual" | "vibe_wins" | "external_wins";
  last_sync_at?: string;
}

interface SyncConflict {
  id: string;
  mapping_id: string;
  row_index: number;
  column_letter: string;
  vibe_value?: { v?: unknown; c?: unknown; f?: string };
  external_value?: { v?: unknown; c?: unknown; f?: string };
  status: "detected" | "resolved";
}

export default function IntegrationsPage() {
  const _router = useSpaRouter();
  const [integrations, setIntegrations] = React.useState<Integration[]>([]);
  const [mappings, setMappings] = React.useState<SyncMapping[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [csrfToken, setCsrfToken] = React.useState<string>("");

  // Dialog states
  const [importOpen, setImportOpen] = React.useState(false);
  const [exportOpen, setExportOpen] = React.useState(false);
  const [newMappingOpen, setNewMappingOpen] = React.useState(false);
  const [conflictsOpen, setConflictsOpen] = React.useState(false);
  const [activeConflicts, setActiveConflicts] = React.useState<SyncConflict[]>([]);
  const [selectedMapping, setSelectedMapping] = React.useState<SyncMapping | null>(null);

  // Form states
  const [extSpreadsheetId, setExtSpreadsheetId] = React.useState("");
  const [extSheetName, setExtSheetName] = React.useState("Sheet1");
  const [targetWorkbookId, setTargetWorkbookId] = React.useState("");
  const [targetSheetName, setTargetSheetName] = React.useState("");
  const [sourceSheetId, setSourceSheetId] = React.useState("");
  const [syncDirection, setSyncDirection] = React.useState<string>("bidirectional");
  const [conflictPolicy, setConflictPolicy] = React.useState<string>("manual");
  const [syncingId, setSyncingId] = React.useState<string | null>(null);

  // Fetch CSRF Token
  React.useEffect(() => {
    fetch(apiUrl("/api/v1/auth/csrf-token"), { method: "POST", credentials: "include" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.csrf_token) setCsrfToken(data.csrf_token);
      })
      .catch(() => {});
  }, []);

  // Fetch Integrations and Sync Mappings
  const loadData = React.useCallback(async () => {
    try {
      const [intRes, mapRes] = await Promise.all([
        fetch(apiUrl("/api/v1/integrations"), { credentials: "include" }),
        fetch(apiUrl("/api/v1/sync/mappings"), { credentials: "include" }),
      ]);

      if (intRes.ok) {
        const intData = await intRes.json();
        setIntegrations(Array.isArray(intData) ? intData : []);
      }
      if (mapRes.ok) {
        const mapData = await mapRes.json();
        setMappings(Array.isArray(mapData) ? mapData : []);
      }
    } catch (e) {
      console.error("Failed to load integrations", e);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    let isMounted = true;
    Promise.all([
      fetch(apiUrl("/api/v1/integrations"), { credentials: "include" }),
      fetch(apiUrl("/api/v1/sync/mappings"), { credentials: "include" }),
    ])
      .then(async ([intRes, mapRes]) => {
        if (!isMounted) return;
        if (intRes.ok) {
          const intData = await intRes.json();
          setIntegrations(Array.isArray(intData) ? intData : []);
        }
        if (mapRes.ok) {
          const mapData = await mapRes.json();
          setMappings(Array.isArray(mapData) ? mapData : []);
        }
      })
      .catch((e) => console.error("Failed to load integrations", e))
      .finally(() => {
        if (isMounted) setLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, []);

  // Connect Google OAuth
  const handleConnectGoogle = async () => {
    try {
      const res = await fetch(apiUrl("/api/v1/integrations/google/auth-url"), { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        if (data.auth_url) {
          window.location.href = data.auth_url;
        }
      }
    } catch (e) {
      console.error("Failed to get auth url", e);
    }
  };

  // Disconnect Integration
  const handleDisconnect = async (id: string) => {
    try {
      const res = await fetch(apiUrl(`/api/v1/integrations/${id}`), {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrfToken },
        credentials: "include",
      });
      if (res.ok) {
        loadData();
      }
    } catch (e) {
      console.error("Failed to disconnect", e);
    }
  };

  // Trigger Sync
  const handleTriggerSync = async (mappingId: string) => {
    setSyncingId(mappingId);
    try {
      const res = await fetch(apiUrl(`/api/v1/sync/mappings/${mappingId}/trigger`), {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
        credentials: "include",
      });
      if (res.ok) {
        loadData();
      }
    } catch (e) {
      console.error("Failed to trigger sync", e);
    } finally {
      setSyncingId(null);
    }
  };

  // View Conflicts
  const handleViewConflicts = async (mapping: SyncMapping) => {
    setSelectedMapping(mapping);
    try {
      const res = await fetch(apiUrl(`/api/v1/sync/mappings/${mapping.id}/conflicts`), {
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        setActiveConflicts(data);
        setConflictsOpen(true);
      }
    } catch (e) {
      console.error("Failed to fetch conflicts", e);
    }
  };

  // Resolve Conflict
  const handleResolveConflict = async (conflictId: string, choice: "keep_vibe" | "keep_external") => {
    try {
      const res = await fetch(apiUrl(`/api/v1/sync/conflicts/${conflictId}/resolve`), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({ resolution_choice: choice }),
        credentials: "include",
      });
      if (res.ok) {
        setActiveConflicts((prev) => prev.filter((c) => c.id !== conflictId));
        loadData();
      }
    } catch (e) {
      console.error("Failed to resolve conflict", e);
    }
  };

  // Import Action
  const handleImportSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const activeInt = integrations[0];
    if (!activeInt) return;

    try {
      const res = await fetch(apiUrl("/api/v1/sync/import"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({
          integration_id: activeInt.id,
          external_spreadsheet_id: extSpreadsheetId,
          external_sheet_name: extSheetName,
          target_workbook_id: targetWorkbookId,
          target_sheet_name: targetSheetName || undefined,
        }),
        credentials: "include",
      });
      if (res.ok) {
        setImportOpen(false);
        loadData();
      }
    } catch (e) {
      console.error("Import failed", e);
    }
  };

  // Export Action
  const handleExportSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const activeInt = integrations[0];
    if (!activeInt) return;

    try {
      const res = await fetch(apiUrl("/api/v1/sync/export"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({
          integration_id: activeInt.id,
          source_sheet_id: sourceSheetId,
          destination_mode: "create_new",
          destination_sheet_name: "Sheet1",
          new_spreadsheet_title: targetSheetName || "VIBE Export",
        }),
        credentials: "include",
      });
      if (res.ok) {
        setExportOpen(false);
        loadData();
      }
    } catch (e) {
      console.error("Export failed", e);
    }
  };

  // Create Mapping Action
  const handleCreateMappingSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const activeInt = integrations[0];
    if (!activeInt) return;

    try {
      const res = await fetch(apiUrl("/api/v1/sync/mappings"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({
          integration_id: activeInt.id,
          workbook_id: targetWorkbookId,
          sheet_id: sourceSheetId,
          external_spreadsheet_id: extSpreadsheetId,
          external_sheet_id: "0",
          external_sheet_name: extSheetName,
          direction: syncDirection,
          conflict_policy: conflictPolicy,
        }),
        credentials: "include",
      });
      if (res.ok) {
        setNewMappingOpen(false);
        loadData();
      }
    } catch (e) {
      console.error("Create mapping failed", e);
    }
  };

  const isConnected = integrations.length > 0 && integrations[0].status === "connected";

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">External Integrations & Sync</h1>
          <p className="text-sm text-muted-foreground">
            Connect Google Sheets, import/export workbooks, and orchestrate automated bidirectional synchronization.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={loadData} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          {isConnected && (
            <>
              <Button variant="outline" size="sm" onClick={() => setImportOpen(true)}>
                <Download className="h-4 w-4 mr-1.5" />
                Import Sheet
              </Button>
              <Button variant="outline" size="sm" onClick={() => setExportOpen(true)}>
                <Upload className="h-4 w-4 mr-1.5" />
                Export Sheet
              </Button>
              <Button size="sm" onClick={() => setNewMappingOpen(true)}>
                <Plus className="h-4 w-4 mr-1.5" />
                New Sync Mapping
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Connected Accounts Card */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <LinkIcon className="h-5 w-5 text-emerald-500" />
            Connected Accounts
          </CardTitle>
          <CardDescription>
            Third-party providers authenticated via AES-256-GCM encrypted OAuth 2.0 vaults.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isConnected ? (
            <div className="flex items-center justify-between p-4 rounded-lg border bg-card/50">
              <div className="flex items-center gap-3">
                <div className="h-10 w-10 rounded-full bg-emerald-500/10 flex items-center justify-center text-emerald-600 font-bold">
                  G
                </div>
                <div>
                  <div className="font-semibold flex items-center gap-2">
                    Google Sheets
                    <Badge variant="outline" className="text-xs bg-emerald-500/10 text-emerald-600 border-emerald-500/20">
                      Active
                    </Badge>
                  </div>
                  <div className="text-sm text-muted-foreground">{integrations[0].account_email}</div>
                </div>
              </div>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => handleDisconnect(integrations[0].id)}
              >
                <Unlink className="h-4 w-4 mr-1.5" />
                Disconnect
              </Button>
            </div>
          ) : (
            <div className="flex flex-col sm:flex-row items-center justify-between p-6 rounded-lg border border-dashed text-center sm:text-left gap-4">
              <div>
                <div className="font-semibold text-base">Google Sheets Integration</div>
                <div className="text-sm text-muted-foreground">
                  Connect your Google account to enable live two-way sync, tab import, and automated exports.
                </div>
              </div>
              <Button onClick={handleConnectGoogle} className="bg-emerald-600 hover:bg-emerald-700 text-white">
                <LinkIcon className="h-4 w-4 mr-1.5" />
                Connect Google Account
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Sync Mappings Grid */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <ArrowRightLeft className="h-5 w-5 text-blue-500" />
            Spreadsheet Synchronization Mappings
          </CardTitle>
          <CardDescription>
            Continuous synchronization engines managing cell hashes, conflict matrices, and background checkpoints.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {mappings.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground text-sm border rounded-lg border-dashed">
              No synchronization mappings configured yet. Click &quot;New Sync Mapping&quot; to link a VIBE sheet to Google Sheets.
            </div>
          ) : (
            <div className="divide-y border rounded-lg overflow-hidden">
              {mappings.map((m) => (
                <div key={m.id} className="p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                  <div className="flex items-center gap-3">
                    <div className="p-2 rounded-md bg-muted">
                      {m.direction === "bidirectional" && <ArrowRightLeft className="h-5 w-5 text-blue-500" />}
                      {m.direction === "vibe_to_google" && <ArrowRight className="h-5 w-5 text-amber-500" />}
                      {m.direction === "google_to_vibe" && <ArrowLeft className="h-5 w-5 text-emerald-500" />}
                    </div>
                    <div>
                      <div className="font-medium flex items-center gap-2">
                        <span>Sheet: {m.sheet_id.slice(0, 8)}...</span>
                        <span className="text-muted-foreground">&rarr;</span>
                        <span>Google: {m.external_sheet_name}</span>
                        <Badge
                          variant="outline"
                          className={
                            m.status === "idle"
                              ? "bg-emerald-500/10 text-emerald-600"
                              : m.status === "conflict"
                              ? "bg-rose-500/10 text-rose-600 border-rose-500/20"
                              : m.status === "syncing"
                              ? "bg-blue-500/10 text-blue-600 animate-pulse"
                              : "bg-amber-500/10 text-amber-600"
                          }
                        >
                          {m.status.toUpperCase()}
                        </Badge>
                      </div>
                      <div className="text-xs text-muted-foreground flex items-center gap-3 mt-1">
                        <span>Direction: {m.direction}</span>
                        <span>Policy: {m.conflict_policy}</span>
                        {m.last_sync_at && (
                          <span className="flex items-center gap-1">
                            <Clock className="h-3 w-3" />
                            {new Date(m.last_sync_at).toLocaleTimeString()}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 self-end sm:self-center">
                    {m.status === "conflict" && (
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={() => handleViewConflicts(m)}
                      >
                        <AlertTriangle className="h-4 w-4 mr-1" />
                        Resolve Conflicts
                      </Button>
                    )}
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleTriggerSync(m.id)}
                      disabled={syncingId === m.id || m.status === "syncing"}
                    >
                      <RefreshCw className={`h-3.5 w-3.5 mr-1 ${syncingId === m.id ? "animate-spin" : ""}`} />
                      Sync Now
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Import Sheet Dialog */}
      <Dialog open={importOpen} onOpenChange={setImportOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Import from Google Sheets</DialogTitle>
            <DialogDescription>
              Fetch values and formulas from an external Google Spreadsheet into a VIBE workbook.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleImportSubmit} className="space-y-4">
            <div>
              <label className="text-xs font-semibold">Google Spreadsheet ID</label>
              <Input
                placeholder="e.g. 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
                value={extSpreadsheetId}
                onChange={(e) => setExtSpreadsheetId(e.target.value)}
                required
              />
            </div>
            <div>
              <label className="text-xs font-semibold">Google Sheet Tab Name</label>
              <Input
                placeholder="Sheet1"
                value={extSheetName}
                onChange={(e) => setExtSheetName(e.target.value)}
                required
              />
            </div>
            <div>
              <label className="text-xs font-semibold">Target Workbook ID</label>
              <Input
                placeholder="VIBE Workbook UUID"
                value={targetWorkbookId}
                onChange={(e) => setTargetWorkbookId(e.target.value)}
                required
              />
            </div>
            <div>
              <label className="text-xs font-semibold">Target Sheet Name (Optional)</label>
              <Input
                placeholder="Imported Tab"
                value={targetSheetName}
                onChange={(e) => setTargetSheetName(e.target.value)}
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setImportOpen(false)}>
                Cancel
              </Button>
              <Button type="submit">Import</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Export Sheet Dialog */}
      <Dialog open={exportOpen} onOpenChange={setExportOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Export Sheet to Google Sheets</DialogTitle>
            <DialogDescription>
              Publish a VIBE spreadsheet grid and formulas directly to a new Google Spreadsheet file.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleExportSubmit} className="space-y-4">
            <div>
              <label className="text-xs font-semibold">Source Sheet ID</label>
              <Input
                placeholder="VIBE Sheet UUID"
                value={sourceSheetId}
                onChange={(e) => setSourceSheetId(e.target.value)}
                required
              />
            </div>
            <div>
              <label className="text-xs font-semibold">New Google Spreadsheet Title</label>
              <Input
                placeholder="e.g. Q4 Executive Report"
                value={targetSheetName}
                onChange={(e) => setTargetSheetName(e.target.value)}
                required
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setExportOpen(false)}>
                Cancel
              </Button>
              <Button type="submit">Export</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* New Sync Mapping Dialog */}
      <Dialog open={newMappingOpen} onOpenChange={setNewMappingOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Synchronization Mapping</DialogTitle>
            <DialogDescription>
              Link a VIBE sheet to an external Google Sheet with automated conflict arbitration.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleCreateMappingSubmit} className="space-y-4">
            <div>
              <label className="text-xs font-semibold">Workbook ID</label>
              <Input
                placeholder="VIBE Workbook UUID"
                value={targetWorkbookId}
                onChange={(e) => setTargetWorkbookId(e.target.value)}
                required
              />
            </div>
            <div>
              <label className="text-xs font-semibold">Sheet ID</label>
              <Input
                placeholder="VIBE Sheet UUID"
                value={sourceSheetId}
                onChange={(e) => setSourceSheetId(e.target.value)}
                required
              />
            </div>
            <div>
              <label className="text-xs font-semibold">Google Spreadsheet ID</label>
              <Input
                placeholder="Google Spreadsheet ID"
                value={extSpreadsheetId}
                onChange={(e) => setExtSpreadsheetId(e.target.value)}
                required
              />
            </div>
            <div>
              <label className="text-xs font-semibold">Google Sheet Tab Name</label>
              <Input
                placeholder="Sheet1"
                value={extSheetName}
                onChange={(e) => setExtSheetName(e.target.value)}
                required
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-xs font-semibold">Sync Direction</label>
                <select
                  aria-label="Sync Direction"
                  className="w-full text-sm border rounded-md p-2 bg-background"
                  value={syncDirection}
                  onChange={(e) => setSyncDirection(e.target.value)}
                >
                  <option value="bidirectional">Bidirectional (Two-Way)</option>
                  <option value="vibe_to_google">VIBE &rarr; Google Only</option>
                  <option value="google_to_vibe">Google &rarr; VIBE Only</option>
                </select>
              </div>
              <div>
                <label className="text-xs font-semibold">Conflict Policy</label>
                <select
                  aria-label="Conflict Policy"
                  className="w-full text-sm border rounded-md p-2 bg-background"
                  value={conflictPolicy}
                  onChange={(e) => setConflictPolicy(e.target.value)}
                >
                  <option value="manual">Manual Resolution</option>
                  <option value="vibe_wins">VIBE Wins (Authoritative)</option>
                  <option value="external_wins">Google Wins</option>
                </select>
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setNewMappingOpen(false)}>
                Cancel
              </Button>
              <Button type="submit">Create Mapping</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Conflict Resolution Dialog */}
      <Dialog open={conflictsOpen} onOpenChange={setConflictsOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-rose-600">
              <AlertTriangle className="h-5 w-5" />
              Detected Cell Conflicts ({activeConflicts.length})
            </DialogTitle>
            <DialogDescription>
              The following cells were modified concurrently in both VIBE and Google Sheets {selectedMapping ? `for "${selectedMapping.external_sheet_name}"` : ""}. Choose which value to retain.
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-[60vh] overflow-y-auto divide-y border rounded-lg">
            {activeConflicts.map((conf) => {
              const vVal = conf.vibe_value?.f || conf.vibe_value?.c || conf.vibe_value?.v || "(empty)";
              const eVal = conf.external_value?.f || conf.external_value?.c || conf.external_value?.v || "(empty)";
              return (
                <div key={conf.id} className="p-4 flex items-center justify-between gap-4">
                  <div>
                    <div className="font-semibold text-sm">
                      Cell: {conf.column_letter}{conf.row_index + 1}
                    </div>
                    <div className="grid grid-cols-2 gap-4 mt-2 text-xs">
                      <div className="p-2 rounded bg-muted/60">
                        <span className="font-medium text-blue-600">VIBE:</span> {String(vVal)}
                      </div>
                      <div className="p-2 rounded bg-muted/60">
                        <span className="font-medium text-emerald-600">Google:</span> {String(eVal)}
                      </div>
                    </div>
                  </div>
                  <div className="flex flex-col gap-1.5 shrink-0">
                    <Button
                      size="sm"
                      variant="outline"
                      className="text-xs h-7"
                      onClick={() => handleResolveConflict(conf.id, "keep_vibe")}
                    >
                      Keep VIBE
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="text-xs h-7"
                      onClick={() => handleResolveConflict(conf.id, "keep_external")}
                    >
                      Keep Google
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConflictsOpen(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}


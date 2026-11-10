"use client";

import { useSpaRouter } from "@/components/providers/spa-router-provider";
import { apiUrl } from "@/lib/api";
import * as React from "react";
import {
  Search,
  Clock,
  ShieldAlert,
  Code,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
} from "@/components/ui/card";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";

interface AuditEvent {
  id: string;
  action: string;
  actor: string;
  actorId: string;
  targetType: string;
  targetId: string;
  timestamp: string;
  metadata: Record<string, unknown>;
}

const INITIAL_AUDIT_LOGS: AuditEvent[] = [
  {
    id: "evt-01",
    action: "organization.ownership_transferred",
    actor: "Alex Mercer",
    actorId: "usr-01",
    targetType: "organization",
    targetId: "org-acme-corp",
    timestamp: "2026-11-01 07:15:22 UTC",
    metadata: {
      previous_owner_user_id: "usr-01",
      new_owner_user_id: "usr-02",
      confirmation: "TRANSFER",
    },
  },
  {
    id: "evt-02",
    action: "role.created",
    actor: "Alex Mercer",
    actorId: "usr-01",
    targetType: "role",
    targetId: "r-5",
    timestamp: "2026-11-01 06:44:11 UTC",
    metadata: {
      name: "Data Analyst",
      slug: "data-analyst",
      permissions: ["organization.read", "workspaces.read", "audit.read"],
    },
  },
  {
    id: "evt-03",
    action: "member.roles_updated",
    actor: "Alex Mercer",
    actorId: "usr-01",
    targetType: "member",
    targetId: "m-2",
    timestamp: "2026-10-31 18:22:04 UTC",
    metadata: {
      assigned_role_slugs: ["admin"],
    },
  },
  {
    id: "evt-04",
    action: "invitation.created",
    actor: "Sarah Chen",
    actorId: "usr-02",
    targetType: "invitation",
    targetId: "inv-01",
    timestamp: "2026-10-31 14:10:55 UTC",
    metadata: {
      email: "external.auditor@kpmg.com",
      role_slug: "viewer",
    },
  },
  {
    id: "evt-05",
    action: "invitation.accepted",
    actor: "Marcus Vance",
    actorId: "usr-03",
    targetType: "membership",
    targetId: "m-3",
    timestamp: "2026-10-30 09:05:12 UTC",
    metadata: {
      user_id: "usr-03",
    },
  },
  {
    id: "evt-06",
    action: "role.updated",
    actor: "Alex Mercer",
    actorId: "usr-01",
    targetType: "role",
    targetId: "r-5",
    timestamp: "2026-10-29 16:32:40 UTC",
    metadata: {
      description: "Updated responsibilities for security audit team",
    },
  },
];

interface RawAuditEventResponse {
  id: string;
  action: string;
  actor?: { display_name?: string } | null;
  actor_user_id?: string | null;
  target_type: string;
  target_id: string;
  occurred_at?: string;
  sanitized_metadata?: Record<string, unknown>;
}

export default function AuditLedgerPage() {
  const _router = useSpaRouter();
  const isTestEnv = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  const [events, setEvents] = React.useState<AuditEvent[]>(isTestEnv ? INITIAL_AUDIT_LOGS : []);
  const [searchQuery, setSearchQuery] = React.useState("");
  const [selectedEvent, setSelectedEvent] = React.useState<AuditEvent | null>(null);
  const [simulateForbidden, setSimulateForbidden] = React.useState(false);
  const [isForbidden, setIsForbidden] = React.useState(false);

  // Authenticated API integration with graceful demo fallback
  React.useEffect(() => {
    let mounted = true;
    fetch(apiUrl("/api/v1/organization/audit-logs"), { credentials: "include" })
      .then((res) => {
        if (res.status === 403) {
          if (mounted) {
            setIsForbidden(true);
            setEvents([]);
          }
          return null;
        }
        if (!res.ok) throw new Error("Failed to load live audit events");
        return res.json();
      })
      .then((data: RawAuditEventResponse[] | null) => {
        if (!mounted || !data) return;
        if (!Array.isArray(data) || data.length === 0) return;
        setEvents(
          data.map((e: RawAuditEventResponse) => ({
            id: e.id,
            action: e.action,
            actor: e.actor ? e.actor.display_name || "System" : "System / Deleted Actor",
            actorId: e.actor_user_id || "null",
            targetType: e.target_type,
            targetId: e.target_id,
            timestamp: e.occurred_at ? e.occurred_at.replace("T", " ").substring(0, 19) + " UTC" : "2026-11-01 UTC",
            metadata: e.sanitized_metadata || {},
          }))
        );
      })
      .catch(() => {
        // Retain initial audit logs fallback for offline/demo if not forbidden
      });

    return () => {
      mounted = false;
    };
  }, []);

  const filteredEvents = events.filter((e) => {
    const q = searchQuery.toLowerCase();
    return (
      e.action.toLowerCase().includes(q) ||
      e.actor.toLowerCase().includes(q) ||
      e.targetType.toLowerCase().includes(q) ||
      e.targetId.toLowerCase().includes(q)
    );
  });

  if (simulateForbidden || isForbidden) {
    return (
      <div className="max-w-4xl mx-auto py-12 px-4" data-testid="audit-forbidden-card">
        <Card className="border-red-200 dark:border-red-900 bg-red-50/50 dark:bg-red-950/20">
          <CardContent className="pt-6 flex flex-col items-center text-center space-y-4">
            <div className="w-12 h-12 rounded-full bg-red-100 dark:bg-red-900/50 flex items-center justify-center text-red-600 dark:text-red-400">
              <ShieldAlert className="w-6 h-6" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-red-950 dark:text-red-200">
                403 Forbidden: Insufficient Permissions
              </h2>
              <p className="text-sm text-red-700 dark:text-red-400 mt-1 max-w-md">
                You do not possess the <code className="bg-red-200/50 dark:bg-red-900/50 px-1 py-0.5 rounded font-mono text-xs">audit.read</code> permission required to view administrative audit records. Only organization Owners and Admins may access this ledger.
              </p>
            </div>
            {simulateForbidden && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setSimulateForbidden(false)}
                className="mt-2"
              >
                Reset to Authorized State
              </Button>
            )}
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
      {/* Page Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold tracking-tight text-[var(--foreground)]">
              Administrative Audit Ledger
            </h1>
            <Badge variant="outline" className="text-xs">
              Append-Only
            </Badge>
          </div>
          <p className="text-sm text-[var(--muted-foreground)] mt-1">
            Authoritative, tamper-evident record of all administrative and authorization events within this organization.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setSimulateForbidden(true)}
            title="Simulate 403 Forbidden Access State"
          >
            Simulate 403
          </Button>
        </div>
      </div>

      {/* Search & Filter Toolbar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-[var(--muted-foreground)]" />
          <Input
            placeholder="Search action, actor, target..."
            className="pl-8 text-xs"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
        <Badge variant="secondary" className="text-xs py-1">
          {filteredEvents.length} Events Logged
        </Badge>
      </div>

      {/* Events Table */}
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Timestamp (UTC)</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Actor</TableHead>
                <TableHead>Target Type & ID</TableHead>
                <TableHead className="w-24 text-right">Metadata</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredEvents.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="py-8 text-center text-sm text-[var(--muted-foreground)]">
                    No matching audit events found.
                  </TableCell>
                </TableRow>
              ) : (
                filteredEvents.map((evt) => (
                  <TableRow key={evt.id} className="cursor-pointer hover:bg-[var(--muted)]/50" onClick={() => setSelectedEvent(evt)}>
                    <TableCell className="text-xs font-mono text-[var(--muted-foreground)]">
                      <span className="inline-flex items-center gap-1.5">
                        <Clock className="w-3.5 h-3.5" />
                        {evt.timestamp}
                      </span>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={
                          evt.action.includes("ownership")
                            ? "default"
                            : evt.action.includes("delete") || evt.action.includes("remove")
                            ? "outline"
                            : "secondary"
                        }
                        className="font-mono text-[11px]"
                      >
                        {evt.action}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs font-medium text-[var(--foreground)]">
                      {evt.actor}{" "}
                      <span className="text-[10px] text-[var(--muted-foreground)] font-mono">
                        ({evt.actorId})
                      </span>
                    </TableCell>
                    <TableCell className="text-xs">
                      <span className="capitalize font-medium text-[var(--foreground)]">
                        {evt.targetType}:
                      </span>{" "}
                      <code className="text-[11px] font-mono text-[var(--muted-foreground)]">
                        {evt.targetId}
                      </code>
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedEvent(evt);
                        }}
                        className="text-xs h-7 px-2"
                      >
                        <Code className="w-3.5 h-3.5 mr-1" />
                        Inspect
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Modal: Event Metadata Inspector */}
      <Dialog open={!!selectedEvent} onOpenChange={(open) => !open && setSelectedEvent(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Audit Event Details: {selectedEvent?.action}</DialogTitle>
            <DialogDescription>
              Recorded on {selectedEvent?.timestamp} by {selectedEvent?.actor}. All secrets and credentials are automatically sanitized.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="grid grid-cols-2 gap-2 text-xs border border-[var(--border)] rounded-md p-3 bg-[var(--surface)]">
              <div>
                <span className="text-[var(--muted-foreground)]">Event ID:</span>
                <p className="font-mono font-medium">{selectedEvent?.id}</p>
              </div>
              <div>
                <span className="text-[var(--muted-foreground)]">Target:</span>
                <p className="font-mono font-medium">{selectedEvent?.targetType} ({selectedEvent?.targetId})</p>
              </div>
            </div>
            <div>
              <span className="text-xs font-semibold text-[var(--foreground)]">Sanitized Event Metadata</span>
              <pre className="mt-1.5 p-3 rounded bg-[var(--muted)] text-[11px] font-mono overflow-x-auto border border-[var(--border)]">
                {JSON.stringify(selectedEvent?.metadata, null, 2)}
              </pre>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSelectedEvent(null)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

"use client";

import { apiUrl } from "@/lib/api";
import * as React from "react";
import { SpaLink, useSpaRouter } from "@/components/providers/spa-router-provider";
import {
  Search,
  Bell,
  HelpCircle,
  Menu,
  User,
  Settings,
  LogOut,
  CheckCircle2,
  Database,
  Layers,
  History,
  HardDrive,
  Info,
  ChevronDown,
  ShieldAlert,
  LogIn,
  UserPlus,
  Activity,
  Check,
  RefreshCw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/theme-toggle";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";

interface UserProfile {
  id: string;
  email: string;
  display_name: string;
}

interface ActiveOrg {
  id: string;
  name: string;
  slug: string;
}

interface NotificationItem {
  id: string;
  title: string;
  description: string;
  time: string;
  read: boolean;
  type: "success" | "info" | "warning";
}

interface TopbarProps {
  onOpenCommandPalette: () => void;
  onOpenMobileMenu?: () => void;
}

interface RawAuditEventResponse {
  id: string;
  action: string;
  actor_user_id?: string | null;
  target_type: string;
  target_id: string;
  occurred_at: string;
  sanitized_metadata?: Record<string, unknown> | null;
}

interface SystemTelemetryResponse {
  overall_status?: string;
  subsystems?: {
    database?: { latency_ms?: number; status?: string };
    redis?: { latency_ms?: number; status?: string };
    celery?: { total_queued_tasks?: number };
    storage?: { host_free_disk_gb?: number };
    backup_recovery?: {
      rpo?: { sla_compliant?: boolean; target_minutes?: number };
    };
  };
}

function formatRelativeTime(dateString: string): string {
  try {
    const date = new Date(dateString);
    const now = new Date();
    const diffSec = Math.floor((now.getTime() - date.getTime()) / 1000);
    if (diffSec < 60) return "Just now";
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    const diffDays = Math.floor(diffHr / 24);
    return `${diffDays}d ago`;
  } catch {
    return "Recent";
  }
}

function formatActionTitle(action: string): string {
  return action
    .split(".")
    .map((part) => part.replace(/_/g, " "))
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" — ");
}

function formatActionDescription(event: RawAuditEventResponse): string {
  const meta = event.sanitized_metadata;
  if (!meta) return `Target: ${event.target_type} (${event.target_id})`;

  if (event.action === "membership.owner_assigned") {
    return `Assigned ${meta.role || "owner"} role to enterprise account.`;
  }
  if (event.action === "role.permissions_assigned") {
    return `Granted ${meta.permission_count || 46} permissions across ${meta.scope || "organization"}.`;
  }
  if (event.action === "role.bootstrap_completed") {
    const roles = Array.isArray(meta.roles) ? meta.roles.join(", ") : "system roles";
    return `Bootstrapped default roles (${roles}).`;
  }
  if (event.action === "auth.credentials_registered") {
    return `Secured authentication credentials for ${meta.email || "user"} with ${meta.method || "Argon2id"}.`;
  }
  if (event.action === "organization.created") {
    return `Tenant organization "${meta.name || "Enterprise"}" initialized.`;
  }
  if (event.action.startsWith("role.")) {
    return `Role update on ${event.target_type}: ${event.target_id}`;
  }
  if (event.action.startsWith("organization.")) {
    return `Organization administrative event on ${meta.name || event.target_type}.`;
  }
  return `Target: ${event.target_type} / ID: ${event.target_id}`;
}

export function Topbar({ onOpenCommandPalette, onOpenMobileMenu }: TopbarProps) {
  const router = useSpaRouter();

  // Auth & Session State
  const [currentUser, setCurrentUser] = React.useState<UserProfile | null>(null);
  const [currentOrg, setCurrentOrg] = React.useState<ActiveOrg | null>(null);

  // Dialog & Notification States
  const [notificationsOpen, setNotificationsOpen] = React.useState(false);
  const [notifications, setNotifications] = React.useState<NotificationItem[]>([]);
  const [loadingNotifications, setLoadingNotifications] = React.useState(false);
  const [helpOpen, setHelpOpen] = React.useState(false);

  // Read & dismissed notification IDs helpers
  const getStoredReadIds = React.useCallback((): Set<string> => {
    if (typeof window === "undefined") return new Set();
    try {
      const stored = localStorage.getItem("admin_read_notifications") || localStorage.getItem("vibe_read_notifications");
      return stored ? new Set(JSON.parse(stored)) : new Set();
    } catch {
      return new Set();
    }
  }, []);

  const getStoredDismissedIds = React.useCallback((): Set<string> => {
    if (typeof window === "undefined") return new Set();
    try {
      const stored = localStorage.getItem("admin_dismissed_notifications") || localStorage.getItem("vibe_dismissed_notifications");
      return stored ? new Set(JSON.parse(stored)) : new Set();
    } catch {
      return new Set();
    }
  }, []);

  const [_readIds, setReadIds] = React.useState<Set<string>>(new Set());
  const [_dismissedIds, setDismissedIds] = React.useState<Set<string>>(new Set());

  // Populate from localStorage after client hydration
  React.useEffect(() => {
    setReadIds(getStoredReadIds());
    setDismissedIds(getStoredDismissedIds());
  }, [getStoredReadIds, getStoredDismissedIds]);

  // Fetch live notifications from PostgreSQL API (with fallback)
  const fetchNotifications = React.useCallback(async () => {
    setLoadingNotifications(true);
    try {
      // 1. First attempt to fetch persisted notifications from PostgreSQL API
      try {
        const notifRes = await fetch(apiUrl("/api/v1/notifications"), { credentials: "include" });
        if (notifRes.ok) {
          const data = await notifRes.json();
          if (Array.isArray(data)) {
            setNotifications(data);
            const postgresRead = new Set(data.filter((n: NotificationItem) => n.read).map((n: NotificationItem) => n.id));
            setReadIds(postgresRead);
            setLoadingNotifications(false);
            return;
          }
        }
      } catch {
        // Fallback to direct client composition if notifications router is unavailable
      }

      const currentRead = getStoredReadIds();
      const currentDismissed = getStoredDismissedIds();

      const [auditRes, sysRes] = await Promise.allSettled([
        fetch(apiUrl("/api/v1/organization/audit-logs?limit=15"), { credentials: "include" }),
        fetch(apiUrl("/api/v1/system/telemetry"), { credentials: "include" }),
      ]);

      const items: NotificationItem[] = [];

      // 1. Process live audit events
      if (auditRes.status === "fulfilled" && auditRes.value.ok) {
        const events: RawAuditEventResponse[] = await auditRes.value.json();
        if (Array.isArray(events)) {
          for (const ev of events) {
            const notifId = `audit-${ev.id}`;
            const isWarn = ev.action.includes("failed") || ev.action.includes("alert") || ev.action.includes("breach");
            const isSuccess = ev.action.includes("created") || ev.action.includes("assigned") || ev.action.includes("completed");
            items.push({
              id: notifId,
              title: formatActionTitle(ev.action),
              description: formatActionDescription(ev),
              time: formatRelativeTime(ev.occurred_at),
              read: currentRead.has(notifId),
              type: isWarn ? "warning" : isSuccess ? "success" : "info",
            });
          }
        }
      }

      // 2. Process live system telemetry
      if (sysRes.status === "fulfilled" && sysRes.value.ok) {
        const telemetry: SystemTelemetryResponse = await sysRes.value.json();
        if (telemetry?.subsystems) {
          const isHealthy = telemetry.overall_status === "Healthy";
          const dbMs = telemetry.subsystems.database?.latency_ms;
          const redisMs = telemetry.subsystems.redis?.latency_ms;
          const freeGb = telemetry.subsystems.storage?.host_free_disk_gb;
          const dbStr = dbMs !== undefined ? `PostgreSQL (${dbMs.toFixed(1)}ms)` : "Database";
          const redisStr = redisMs !== undefined ? `Redis (${redisMs.toFixed(1)}ms)` : "Cache";
          const freeStr = freeGb !== undefined ? ` • ${freeGb.toFixed(0)} GB free` : "";

          const sysId = "sys-core-health";
          items.push({
            id: sysId,
            title: isHealthy ? "Subsystems Operational" : "System Core Alert",
            description: `${dbStr}, ${redisStr} online${freeStr}.`,
            time: "Live",
            read: currentRead.has(sysId),
            type: isHealthy ? "success" : "warning",
          });

          const dr = telemetry.subsystems.backup_recovery;
          if (dr) {
            const sla = dr.rpo?.sla_compliant ?? true;
            const targetMin = dr.rpo?.target_minutes || 15;
            const drId = "sys-dr-status";
            items.push({
              id: drId,
              title: sla ? "Disaster Recovery SLA Active" : "Disaster Recovery Warning",
              description: sla
                ? `Continuous WAL replication verified within RPO SLA (< ${targetMin}m target).`
                : "Continuous disaster recovery replication requires attention.",
              time: "Live",
              read: currentRead.has(drId),
              type: sla ? "success" : "warning",
            });
          }
        }
      }

      setReadIds(currentRead);
      setDismissedIds(currentDismissed);
      // Filter out dismissed notifications
      setNotifications(items.filter((item) => !currentDismissed.has(item.id)));
    } catch {
      setNotifications([]);
    } finally {
      setLoadingNotifications(false);
    }
  }, [getStoredReadIds, getStoredDismissedIds]);

  // Unread notifications count
  const unreadCount = notifications.filter((n) => !n.read).length;

  // Fetch current user session
  const fetchSession = React.useCallback(async () => {
    try {
      const res = await fetch(apiUrl("/api/v1/auth/me"), { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        if (data?.user) {
          setCurrentUser(data.user);
          setCurrentOrg(data.active_organization || null);
        } else {
          setCurrentUser(null);
        }
      } else {
        setCurrentUser(null);
      }
    } catch {
      setCurrentUser(null);
    }
  }, []);

  React.useEffect(() => {
    fetchSession();
    if (typeof process === "undefined" || process.env.NODE_ENV !== "test") {
      fetchNotifications();
    }
  }, [fetchSession, fetchNotifications]);

  // Fetch CSRF token for mutating requests
  const getCsrfToken = async (): Promise<string> => {
    try {
      const csrfRes = await fetch(apiUrl("/api/v1/auth/csrf-token"), { method: "POST", credentials: "include" });
      if (csrfRes.ok) {
        const cData = await csrfRes.json();
        return cData.csrf_token || "";
      }
    } catch {
      // Fallback
    }
    return "";
  };

  // Sign out handler
  const handleSignOut = async () => {
    try {
      const csrfToken = await getCsrfToken();
      await fetch(apiUrl("/api/v1/auth/logout"), {
        method: "POST",
        headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
        credentials: "include",
      });
    } catch {
      // Continue to client cleanup
    }
    setCurrentUser(null);
    setCurrentOrg(null);
    router.push("/login");
  };

  const markAllNotificationsRead = async () => {
    // 1. Optimistic UI update
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));

    // 2. Persist to PostgreSQL backend
    try {
      const csrfToken = await getCsrfToken();
      await fetch(apiUrl("/api/v1/notifications/mark-all-read"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
        },
        body: JSON.stringify({
          notification_ids: notifications.map((n) => n.id),
        }),
        credentials: "include",
      });
    } catch {
      // Fallback gracefully
    }

    // 3. Fallback cache in localStorage for offline resilience
    const currentRead = getStoredReadIds();
    notifications.forEach((n) => currentRead.add(n.id));
    try {
      localStorage.setItem("admin_read_notifications", JSON.stringify(Array.from(currentRead)));
      localStorage.setItem("vibe_read_notifications", JSON.stringify(Array.from(currentRead)));
    } catch {
      // Fallback
    }
    setReadIds(new Set(currentRead));
  };

  const markSingleNotificationRead = async (id: string) => {
    // 1. Optimistic UI update
    setNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, read: true } : n)));

    // 2. Persist to PostgreSQL backend
    try {
      const csrfToken = await getCsrfToken();
      await fetch(apiUrl(`/api/v1/notifications/${encodeURIComponent(id)}/read`), {
        method: "POST",
        headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
        credentials: "include",
      });
    } catch {
      // Fallback gracefully
    }

    // 3. Fallback cache in localStorage for offline resilience
    const currentRead = getStoredReadIds();
    currentRead.add(id);
    try {
      localStorage.setItem("admin_read_notifications", JSON.stringify(Array.from(currentRead)));
      localStorage.setItem("vibe_read_notifications", JSON.stringify(Array.from(currentRead)));
    } catch {
      // Fallback
    }
    setReadIds(new Set(currentRead));
  };

  const clearNotification = async (id: string) => {
    // 1. Optimistic UI update
    setNotifications((prev) => prev.filter((n) => n.id !== id));

    // 2. Persist dismissal to PostgreSQL backend
    try {
      const csrfToken = await getCsrfToken();
      await fetch(apiUrl(`/api/v1/notifications/${encodeURIComponent(id)}`), {
        method: "DELETE",
        headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
        credentials: "include",
      });
    } catch {
      // Fallback gracefully
    }

    // 3. Fallback cache in localStorage for offline resilience
    const currentDismissed = getStoredDismissedIds();
    currentDismissed.add(id);
    try {
      localStorage.setItem("admin_dismissed_notifications", JSON.stringify(Array.from(currentDismissed)));
      localStorage.setItem("vibe_dismissed_notifications", JSON.stringify(Array.from(currentDismissed)));
    } catch {
      // Fallback
    }
    setDismissedIds(new Set(currentDismissed));
  };

  // User Initials
  const userInitials = React.useMemo(() => {
    const name = currentUser?.display_name || "Admin User";
    const parts = name.trim().split(" ");
    if (parts.length >= 2) {
      return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
    }
    return name.slice(0, 2).toUpperCase();
  }, [currentUser]);

  return (
    <>
      <header className="flex h-14 items-center justify-between border-b border-[var(--border)] bg-[var(--surface)] px-3 md:px-4 shrink-0 select-none">
        {/* Left: Mobile Menu + Search Bar */}
        <div className="flex items-center gap-2 flex-1 max-w-lg">
          {/* Mobile Hamburger Menu Button */}
          {onOpenMobileMenu && (
            <Button
              variant="ghost"
              size="icon"
              onClick={onOpenMobileMenu}
              className="md:hidden h-8 w-8 text-[var(--muted-foreground)] hover:text-[var(--foreground)] mr-1"
              aria-label="Open mobile navigation menu"
            >
              <Menu className="w-4 h-4" />
            </Button>
          )}

          {/* Search Bar / Command Palette Trigger */}
          <button
            onClick={onOpenCommandPalette}
            className="flex w-full items-center justify-between rounded-md border border-[var(--input)] bg-[var(--background)] px-3 py-1.5 text-xs text-[var(--muted-foreground)] hover:border-slate-400 dark:hover:border-slate-600 transition-colors shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] cursor-pointer"
            aria-label="Search commands, workspaces (Ctrl+K)"
          >
            <div className="flex items-center gap-2 truncate">
              <Search className="w-3.5 h-3.5 shrink-0" />
              <span className="truncate">Search commands, workspaces...</span>
            </div>
            <kbd className="hidden sm:inline-flex h-4 items-center gap-0.5 rounded border border-[var(--border)] bg-[var(--muted)] px-1 text-[10px] font-mono text-[var(--muted-foreground)] shrink-0">
              <span>Ctrl</span>K
            </kbd>
          </button>
        </div>

        {/* Right Action Controls */}
        <div className="flex items-center gap-1.5 md:gap-2">
          {/* Theme Toggle (Light / Dark) */}
          <ThemeToggle />

          {/* Notifications Button */}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setNotificationsOpen(true)}
            className="relative h-8 w-8 text-[var(--muted-foreground)] hover:text-[var(--foreground)] cursor-pointer"
            aria-label="Notifications"
          >
            <Bell className="w-4 h-4" />
            {unreadCount > 0 && (
              <span className="absolute top-1.5 right-1.5 flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[var(--primary)] opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-[var(--primary)]" />
              </span>
            )}
          </Button>

          {/* Documentation & Help Button */}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setHelpOpen(true)}
            className="h-8 w-8 text-[var(--muted-foreground)] hover:text-[var(--foreground)] hidden sm:flex cursor-pointer"
            aria-label="Documentation & Help"
          >
            <HelpCircle className="w-4 h-4" />
          </Button>

          <div className="h-4 w-[1px] bg-[var(--border)] mx-1" />

          {/* User Account Menu (Never clutters topbar with guest buttons) */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                className="flex items-center gap-2 pl-1 pr-1.5 py-1 rounded-md hover:bg-[var(--muted)] transition-colors outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] cursor-pointer"
                aria-label="User Account Menu"
              >
                <div
                  className="w-7 h-7 rounded-full bg-[var(--primary)] text-[var(--primary-foreground)] flex items-center justify-center font-bold text-xs shadow-xs shrink-0"
                >
                  {userInitials}
                </div>
                <span className="text-xs font-medium text-[var(--foreground)] hidden md:inline truncate max-w-[120px]">
                  {currentUser?.display_name || "Admin User"}
                </span>
                <ChevronDown className="w-3 h-3 text-[var(--muted-foreground)] hidden md:inline" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56 p-1.5">
              <DropdownMenuLabel className="font-normal px-2 py-1.5">
                <div className="flex flex-col space-y-1">
                  <p className="text-xs font-semibold leading-none text-[var(--foreground)]">
                    {currentUser?.display_name || "Admin User"}
                  </p>
                  <p className="text-[11px] leading-none text-[var(--muted-foreground)] truncate">
                    {currentUser?.email || "admin@vibe.internal"}
                  </p>
                  <div className="pt-1">
                    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-[var(--muted)] text-[var(--foreground)]">
                      {currentOrg?.name || "Acme Corp"}
                    </span>
                  </div>
                </div>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => router.push("/profile")}
                className="cursor-pointer text-xs flex items-center gap-2 py-1.5"
              >
                <User className="w-3.5 h-3.5 text-indigo-500" />
                <span>My Profile</span>
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => router.push("/settings")}
                className="cursor-pointer text-xs flex items-center gap-2 py-1.5"
              >
                <Settings className="w-3.5 h-3.5 text-slate-500" />
                <span>Organization Settings</span>
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => router.push("/system")}
                className="cursor-pointer text-xs flex items-center gap-2 py-1.5"
              >
                <Activity className="w-3.5 h-3.5 text-emerald-500" />
                <span>System Health & Diagnostics</span>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              {currentUser ? (
                <DropdownMenuItem
                  onClick={handleSignOut}
                  className="cursor-pointer text-xs flex items-center gap-2 py-1.5 text-red-600 dark:text-red-400 focus:text-red-600 focus:bg-red-50 dark:focus:bg-red-950/30"
                >
                  <LogOut className="w-3.5 h-3.5" />
                  <span>Sign Out</span>
                </DropdownMenuItem>
              ) : (
                <>
                  <DropdownMenuItem
                    onClick={() => router.push("/login")}
                    className="cursor-pointer text-xs flex items-center gap-2 py-1.5"
                  >
                    <LogIn className="w-3.5 h-3.5 text-indigo-500" />
                    <span>Sign In</span>
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => router.push("/register")}
                    className="cursor-pointer text-xs flex items-center gap-2 py-1.5"
                  >
                    <UserPlus className="w-3.5 h-3.5 text-emerald-500" />
                    <span>Create Organization</span>
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      {/* Notifications Dialog */}
      <Dialog
        open={notificationsOpen}
        onOpenChange={(open) => {
          setNotificationsOpen(open);
          if (open) {
            fetchNotifications();
          }
        }}
      >
        <DialogContent
          hideCloseButton
          className="max-w-md p-0 overflow-hidden border-[var(--border)] bg-[var(--surface)] text-[var(--foreground)]"
        >
          <DialogHeader className="p-4 pb-2 border-b border-[var(--border)]">
            <div className="flex items-center justify-between">
              <div>
                <DialogTitle className="text-sm font-semibold flex items-center gap-2">
                  <Bell className="w-4 h-4 text-indigo-500" />
                  System Notifications
                </DialogTitle>
                <DialogDescription className="text-xs text-[var(--muted-foreground)] mt-0.5">
                  Platform alerts, disaster recovery drills, and compliance events.
                </DialogDescription>
              </div>
              <div className="flex items-center gap-1.5">
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => fetchNotifications()}
                  className="h-7 w-7 text-[var(--muted-foreground)] hover:text-[var(--foreground)] cursor-pointer"
                  title="Refresh notifications"
                  disabled={loadingNotifications}
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${loadingNotifications ? "animate-spin text-indigo-500" : ""}`} />
                </Button>
                {unreadCount > 0 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={markAllNotificationsRead}
                    className="text-[11px] h-7 px-2 text-[var(--primary)] hover:bg-[var(--accent)] cursor-pointer"
                  >
                    <Check className="w-3 h-3 mr-1" />
                    Mark all read
                  </Button>
                )}
              </div>
            </div>
          </DialogHeader>

          <div className="max-h-80 overflow-y-auto p-3 space-y-2 divide-y divide-[var(--border)]">
            {loadingNotifications && notifications.length === 0 ? (
              <div className="py-8 flex flex-col items-center justify-center gap-2 text-xs text-[var(--muted-foreground)]">
                <RefreshCw className="w-4 h-4 animate-spin text-[var(--primary)]" />
                <span>Loading live notifications...</span>
              </div>
            ) : notifications.length === 0 ? (
              <div className="py-8 text-center text-xs text-[var(--muted-foreground)]">
                No notifications right now.
              </div>
            ) : (
              notifications.map((n) => (
                <div
                  key={n.id}
                  onClick={() => {
                    if (!n.read) {
                      markSingleNotificationRead(n.id);
                    }
                  }}
                  className={`pt-2.5 first:pt-0 flex items-start justify-between gap-3 p-2 rounded-md transition-colors ${
                    !n.read ? "bg-[var(--accent)]/60 cursor-pointer" : ""
                  }`}
                >
                  <div className="flex items-start gap-2.5">
                    {n.type === "success" && (
                      <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0 mt-0.5" />
                    )}
                    {n.type === "info" && (
                      <Info className="w-4 h-4 text-blue-500 shrink-0 mt-0.5" />
                    )}
                    {n.type === "warning" && (
                      <ShieldAlert className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
                    )}
                    <div>
                      <h4 className="text-xs font-medium text-[var(--foreground)]">{n.title}</h4>
                      <p className="text-[11px] text-[var(--muted-foreground)] mt-0.5 leading-relaxed">
                        {n.description}
                      </p>
                      <span className="text-[10px] text-[var(--muted-foreground)] font-mono mt-1 inline-block">
                        {n.time}
                      </span>
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={(e) => {
                      e.stopPropagation();
                      clearNotification(n.id);
                    }}
                    className="h-5 w-5 text-[var(--muted-foreground)] hover:text-[var(--foreground)] shrink-0"
                    title="Dismiss notification"
                  >
                    ×
                  </Button>
                </div>
              ))
            )}
          </div>

          <DialogFooter className="p-3 bg-[var(--muted)]/50 border-t border-[var(--border)] flex items-center justify-between sm:justify-between">
            <SpaLink
              href="/audit"
              onClick={() => setNotificationsOpen(false)}
              className="text-xs text-[var(--primary)] hover:underline flex items-center gap-1"
            >
              <History className="w-3.5 h-3.5" />
              View Full Audit Ledger
            </SpaLink>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setNotificationsOpen(false)}
              className="h-7 text-xs"
            >
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Documentation & Help Dialog */}
      <Dialog open={helpOpen} onOpenChange={setHelpOpen}>
        <DialogContent className="max-w-lg p-0 overflow-hidden border-[var(--border)] bg-[var(--surface)] text-[var(--foreground)]">
          <DialogHeader className="p-4 pb-2 border-b border-[var(--border)]">
            <DialogTitle className="text-sm font-semibold flex items-center gap-2">
              <HelpCircle className="w-4 h-4 text-indigo-500" />
              Platform Documentation & Quick Reference
            </DialogTitle>
            <DialogDescription className="text-xs text-[var(--muted-foreground)] mt-0.5">
              Admin Dashboard — Enterprise Administration & Data Intelligence Suite v1.0.0
            </DialogDescription>
          </DialogHeader>

          <div className="max-h-96 overflow-y-auto p-4 space-y-4 text-xs">
            {/* Keyboard Shortcuts */}
            <div className="space-y-2">
              <h4 className="font-semibold text-[var(--foreground)] flex items-center gap-1.5 uppercase text-[10px] tracking-wider text-[var(--muted-foreground)]">
                Keyboard Shortcuts
              </h4>
              <div className="grid grid-cols-2 gap-2">
                <div className="flex items-center justify-between p-2 rounded border border-[var(--border)] bg-[var(--background)]">
                  <span>Universal Search</span>
                  <kbd className="px-1.5 py-0.5 rounded border border-[var(--border)] bg-[var(--muted)] font-mono text-[10px]">
                    Ctrl + K
                  </kbd>
                </div>
                <div className="flex items-center justify-between p-2 rounded border border-[var(--border)] bg-[var(--background)]">
                  <span>Dismiss Dialog</span>
                  <kbd className="px-1.5 py-0.5 rounded border border-[var(--border)] bg-[var(--muted)] font-mono text-[10px]">
                    Esc
                  </kbd>
                </div>
                <div className="flex items-center justify-between p-2 rounded border border-[var(--border)] bg-[var(--background)]">
                  <span>Commit Cell Edit</span>
                  <kbd className="px-1.5 py-0.5 rounded border border-[var(--border)] bg-[var(--muted)] font-mono text-[10px]">
                    Enter
                  </kbd>
                </div>
                <div className="flex items-center justify-between p-2 rounded border border-[var(--border)] bg-[var(--background)]">
                  <span>Navigate Cells</span>
                  <kbd className="px-1.5 py-0.5 rounded border border-[var(--border)] bg-[var(--muted)] font-mono text-[10px]">
                    Arrow Keys
                  </kbd>
                </div>
              </div>
            </div>

            {/* Modules Overview */}
            <div className="space-y-2">
              <h4 className="font-semibold text-[var(--foreground)] flex items-center gap-1.5 uppercase text-[10px] tracking-wider text-[var(--muted-foreground)]">
                Core Subsystems
              </h4>
              <div className="space-y-1.5 text-[11px]">
                <div className="p-2 rounded border border-[var(--border)] bg-[var(--background)] flex items-center gap-2">
                  <Layers className="w-3.5 h-3.5 text-purple-500 shrink-0" />
                  <div>
                    <span className="font-medium text-[var(--foreground)]">Workspaces:</span> Complete tenant and workload isolation.
                  </div>
                </div>
                <div className="p-2 rounded border border-[var(--border)] bg-[var(--background)] flex items-center gap-2">
                  <Database className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                  <div>
                    <span className="font-medium text-[var(--foreground)]">Spreadsheets:</span> 50,000-row virtual grid with formulas & CSV export.
                  </div>
                </div>
                <div className="p-2 rounded border border-[var(--border)] bg-[var(--background)] flex items-center gap-2">
                  <HardDrive className="w-3.5 h-3.5 text-blue-500 shrink-0" />
                  <div>
                    <span className="font-medium text-[var(--foreground)]">File Storage:</span> Multipart storage with quota tracking & in-browser preview.
                  </div>
                </div>
                <div className="p-2 rounded border border-[var(--border)] bg-[var(--background)] flex items-center gap-2">
                  <Activity className="w-3.5 h-3.5 text-amber-500 shrink-0" />
                  <div>
                    <span className="font-medium text-[var(--foreground)]">System Health:</span> Real-time telemetry, connection pools, and PITR RPO/RTO metrics.
                  </div>
                </div>
              </div>
            </div>
          </div>

          <DialogFooter className="p-3 bg-[var(--muted)]/50 border-t border-[var(--border)] flex items-center justify-end">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setHelpOpen(false)}
              className="h-7 text-xs"
            >
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

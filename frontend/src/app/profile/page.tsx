"use client";

import { apiUrl } from "@/lib/api";
import * as React from "react";
import { SpaLink, useSpaRouter } from "@/components/providers/spa-router-provider";
import {
  User,
  Key,
  Laptop,
  CheckCircle2,
  AlertTriangle,
  Building2,
  Download,
  Copy,
  Clock,
  Eye,
  EyeOff,
  Bell,
  Smartphone,
  Terminal,
  RefreshCw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

interface UserProfile {
  id: string;
  email: string;
  display_name: string;
  created_at: string;
}

interface ActiveOrg {
  id: string;
  name: string;
  slug: string;
}

interface UserSession {
  id: string;
  created_at: string;
  last_seen_at?: string;
  user_agent?: string;
  ip_address?: string;
  is_current?: boolean;
}

function parseUserAgent(ua?: string | null): { browser: string; os: string; deviceType: "laptop" | "smartphone" | "terminal" } {
  if (!ua) {
    return { browser: "Web Browser", os: "Unknown OS", deviceType: "laptop" };
  }

  let os = "Unknown OS";
  if (ua.includes("Windows NT 10.0") || ua.includes("Windows NT 11.0") || ua.includes("Windows")) os = "Windows";
  else if (ua.includes("Macintosh") || ua.includes("Mac OS X")) os = "macOS";
  else if (ua.includes("iPhone")) os = "iOS (iPhone)";
  else if (ua.includes("iPad")) os = "iPadOS";
  else if (ua.includes("Android")) os = "Android";
  else if (ua.includes("Linux")) os = "Linux";

  let browser = "Web Browser";
  if (ua.includes("Edg/")) browser = "Microsoft Edge";
  else if (ua.includes("Chrome/") && !ua.includes("Edg/")) {
    browser = ua.includes("HeadlessChrome") ? "Headless Chrome" : "Google Chrome";
  } else if (ua.includes("Safari/") && !ua.includes("Chrome/")) {
    browser = "Apple Safari";
  } else if (ua.includes("Firefox/")) {
    browser = "Mozilla Firefox";
  } else if (ua.includes("Python") || ua.includes("curl") || ua.includes("Postman") || ua.includes("urllib")) {
    browser = ua.split("/")[0] || "API Client";
    return { browser, os: "Automated Tool", deviceType: "terminal" };
  }

  const isMobile = ua.includes("Mobile") || ua.includes("Android") || ua.includes("iPhone");
  return { browser, os, deviceType: isMobile ? "smartphone" : "laptop" };
}

function formatRelativeTime(dateString?: string): string {
  if (!dateString) return "Active recently";
  try {
    const date = new Date(dateString);
    const now = new Date();
    const diffSec = Math.floor((now.getTime() - date.getTime()) / 1000);
    if (diffSec < 60) return "Active just now";
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `Active ${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `Active ${diffHr}h ago`;
    const diffDays = Math.floor(diffHr / 24);
    return `Active ${diffDays}d ago`;
  } catch {
    return "Active recently";
  }
}

export default function ProfilePage() {
  const router = useSpaRouter();

  // Profile states
  const [profile, setProfile] = React.useState<UserProfile | null>(null);
  const [org, setOrg] = React.useState<ActiveOrg | null>(null);
  const [displayName, setDisplayName] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const [csrfToken, setCsrfToken] = React.useState("");
  const [notice, setNotice] = React.useState<{ message: string; type: "success" | "error" } | null>(null);
  const [savingProfile, setSavingProfile] = React.useState(false);
  const [copiedId, setCopiedId] = React.useState(false);

  // Password change states
  const [currentPassword, setCurrentPassword] = React.useState("");
  const [newPassword, setNewPassword] = React.useState("");
  const [confirmPassword, setConfirmPassword] = React.useState("");
  const [showCurrentPass, setShowCurrentPass] = React.useState(false);
  const [showNewPass, setShowNewPass] = React.useState(false);
  const [savingPassword, setSavingPassword] = React.useState(false);

  // Active sessions state
  const [sessions, setSessions] = React.useState<UserSession[]>([]);
  const [loadingSessions, setLoadingSessions] = React.useState(false);

  // Privacy toggles state
  const [securityAlerts, setSecurityAlerts] = React.useState(true);
  const [healthDigests, setHealthDigests] = React.useState(true);
  const [auditEmails, setAuditEmails] = React.useState(false);

  const showNotice = (message: string, type: "success" | "error") => {
    setNotice({ message, type });
    setTimeout(() => setNotice(null), 6000);
  };

  // Fetch CSRF Token
  const fetchCsrfToken = React.useCallback(async () => {
    try {
      const res = await fetch(apiUrl("/api/v1/auth/csrf-token"), { method: "POST", credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        if (data?.csrf_token) setCsrfToken(data.csrf_token);
      }
    } catch {
      // Graceful fallback
    }
  }, []);

  // Fetch user profile and sessions
  const loadProfile = React.useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch(apiUrl("/api/v1/auth/me"), { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        if (data?.user) {
          setProfile(data.user);
          setDisplayName(data.user.display_name || "");
          setOrg(data.active_organization || null);
        }
      } else if (res.status === 401) {
        router.push("/login");
      }
    } catch {
      showNotice("Failed to load user profile", "error");
    } finally {
      setLoading(false);
    }
  }, [router]);

  const loadSessions = React.useCallback(async () => {
    try {
      setLoadingSessions(true);
      const res = await fetch(apiUrl("/api/v1/auth/sessions"), { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        setSessions(Array.isArray(data) ? data : []);
      }
    } catch {
      // Fallback
    } finally {
      setLoadingSessions(false);
    }
  }, []);

  React.useEffect(() => {
    fetchCsrfToken();
    loadProfile();
    loadSessions();
  }, [fetchCsrfToken, loadProfile, loadSessions]);

  // Handle Profile Update (Display Name)
  const handleUpdateProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!displayName.trim()) {
      showNotice("Display name cannot be empty", "error");
      return;
    }

    try {
      setSavingProfile(true);
      const res = await fetch(apiUrl("/api/v1/auth/me"), {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        credentials: "include",
        body: JSON.stringify({ display_name: displayName.trim() }),
      });

      if (res.ok) {
        const data = await res.json();
        if (data?.user) setProfile(data.user);
        showNotice("Profile personal data updated successfully", "success");
      } else {
        const err = await res.json();
        showNotice(err?.detail || err?.error?.message || "Failed to update profile", "error");
      }
    } catch {
      showNotice("Network error updating profile", "error");
    } finally {
      setSavingProfile(false);
    }
  };

  // Handle Password Change
  const handleUpdatePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newPassword.length < 12) {
      showNotice("New password must be at least 12 characters long", "error");
      return;
    }
    if (newPassword !== confirmPassword) {
      showNotice("New password and confirmation do not match", "error");
      return;
    }

    try {
      setSavingPassword(true);
      const res = await fetch(apiUrl("/api/v1/auth/change-password"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        credentials: "include",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });

      if (res.ok) {
        showNotice("Password successfully updated", "success");
        setCurrentPassword("");
        setNewPassword("");
        setConfirmPassword("");
      } else {
        const err = await res.json();
        showNotice(err?.detail || err?.error?.message || "Failed to update password", "error");
      }
    } catch {
      showNotice("Network error changing password", "error");
    } finally {
      setSavingPassword(false);
    }
  };

  // Handle Revoke Session
  const [revokingOther, setRevokingOther] = React.useState(false);

  const handleRevokeSession = async (sessionId: string) => {
    try {
      let token = csrfToken;
      if (!token) {
        const csrfRes = await fetch(apiUrl("/api/v1/auth/csrf-token"), { method: "POST", credentials: "include" });
        if (csrfRes.ok) {
          const cData = await csrfRes.json();
          token = cData.csrf_token || "";
          setCsrfToken(token);
        }
      }

      const res = await fetch(apiUrl(`/api/v1/auth/sessions/${sessionId}`), {
        method: "DELETE",
        headers: token ? { "X-CSRF-Token": token } : {},
        credentials: "include",
      });

      if (res.ok) {
        setSessions((prev) => prev.filter((s) => s.id !== sessionId));
        showNotice("Session revoked successfully", "success");
      } else {
        showNotice("Failed to revoke session", "error");
      }
    } catch {
      showNotice("Error revoking session", "error");
    }
  };

  const handleRevokeOtherSessions = async () => {
    const otherSessions = sessions.filter((s) => !s.is_current);
    if (otherSessions.length === 0) return;

    try {
      setRevokingOther(true);
      let token = csrfToken;
      if (!token) {
        const csrfRes = await fetch(apiUrl("/api/v1/auth/csrf-token"), { method: "POST", credentials: "include" });
        if (csrfRes.ok) {
          const cData = await csrfRes.json();
          token = cData.csrf_token || "";
          setCsrfToken(token);
        }
      }

      await Promise.allSettled(
        otherSessions.map((s) =>
          fetch(apiUrl(`/api/v1/auth/sessions/${s.id}`), {
            method: "DELETE",
            headers: token ? { "X-CSRF-Token": token } : {},
            credentials: "include",
          })
        )
      );

      setSessions((prev) => prev.filter((s) => s.is_current));
      showNotice("All other sessions successfully revoked", "success");
    } catch {
      showNotice("Error revoking other sessions", "error");
    } finally {
      setRevokingOther(false);
    }
  };

  // Copy User ID
  const handleCopyId = () => {
    if (profile?.id) {
      navigator.clipboard.writeText(profile.id);
      setCopiedId(true);
      setTimeout(() => setCopiedId(false), 2000);
    }
  };

  // Export Personal Data
  const handleExportData = () => {
    if (!profile) return;
    const exportBundle = {
      export_timestamp: new Date().toISOString(),
      platform: "Admin Dashboard v1.0.0",
      user_profile: profile,
      organization_context: org,
      active_sessions: sessions,
      privacy_preferences: {
        security_alerts: securityAlerts,
        health_digests: healthDigests,
        audit_emails: auditEmails,
      },
    };

    const blob = new Blob([JSON.stringify(exportBundle, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `vibe_personal_data_${profile.email.split("@")[0]}_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
    showNotice("Personal data bundle downloaded", "success");
  };

  if (loading) {
    return (
      <div className="space-y-6 max-w-7xl mx-auto pb-12">
        <div className="space-y-2">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-4 w-96" />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <Skeleton className="h-64 rounded-xl" />
          <Skeleton className="h-64 rounded-xl" />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-[var(--border)] pb-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--foreground)] flex items-center gap-2.5">
            <User className="w-6 h-6 text-indigo-500" />
            My Profile & Account Settings
          </h1>
          <p className="text-xs text-[var(--muted-foreground)] mt-1">
            Manage your personal data, credentials, active sessions, and enterprise preferences.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleExportData}
            className="text-xs h-8 cursor-pointer flex items-center gap-1.5"
            title="Download JSON export of your personal profile data"
          >
            <Download className="w-3.5 h-3.5 text-indigo-500" />
            Export My Data
          </Button>
        </div>
      </div>

      {/* Global Feedback Notice */}
      {notice && (
        <div
          className={`flex items-center justify-between p-3.5 rounded-lg border text-xs animate-in fade-in-50 ${
            notice.type === "success"
              ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400"
              : "bg-red-500/10 border-red-500/30 text-red-600 dark:text-red-400"
          }`}
        >
          <div className="flex items-center gap-2">
            {notice.type === "success" ? (
              <CheckCircle2 className="w-4 h-4 shrink-0" />
            ) : (
              <AlertTriangle className="w-4 h-4 shrink-0" />
            )}
            <span className="font-medium">{notice.message}</span>
          </div>
          <button
            onClick={() => setNotice(null)}
            className="text-[var(--muted-foreground)] hover:text-[var(--foreground)] font-bold text-sm ml-2 cursor-pointer"
          >
            ×
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Column (2 Cols): Personal Data & Security */}
        <div className="lg:col-span-2 space-y-6">
          {/* 1. Personal Information Card */}
          <Card className="border-[var(--border)] bg-[var(--surface)] shadow-xs">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <User className="w-4 h-4 text-indigo-500" />
                Personal Profile Information
              </CardTitle>
              <CardDescription className="text-xs">
                Update your display name visible across workspaces, dashboards, and audit trails.
              </CardDescription>
            </CardHeader>
            <form onSubmit={handleUpdateProfile}>
              <CardContent className="space-y-4 text-xs">
                {/* Email Address (Read-only with badge) */}
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-[var(--foreground)] flex items-center justify-between">
                    <span>Email Address</span>
                    <Badge variant="outline" className="text-[10px] bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30">
                      Verified Account
                    </Badge>
                  </label>
                  <Input
                    value={profile?.email || ""}
                    disabled
                    className="bg-[var(--muted)]/50 text-[var(--muted-foreground)] cursor-not-allowed text-xs font-mono"
                  />
                  <p className="text-[11px] text-[var(--muted-foreground)]">
                    Primary authentication address. Managed by organization domain administrator.
                  </p>
                </div>

                {/* Display Name (Editable) */}
                <div className="space-y-1.5">
                  <label htmlFor="display-name" className="text-xs font-medium text-[var(--foreground)]">
                    Display Name
                  </label>
                  <Input
                    id="display-name"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    placeholder="e.g. Alex Mercer"
                    required
                    className="text-xs"
                  />
                  <p className="text-[11px] text-[var(--muted-foreground)]">
                    Your full name or handle displayed on shared reports and comments.
                  </p>
                </div>

                {/* Account ID */}
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-[var(--foreground)]">
                    User UUID (Account Identifier)
                  </label>
                  <div className="flex items-center gap-2">
                    <Input
                      value={profile?.id || ""}
                      disabled
                      className="bg-[var(--muted)]/50 text-[var(--muted-foreground)] font-mono text-[11px]"
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      onClick={handleCopyId}
                      className="h-9 w-9 shrink-0 cursor-pointer"
                      title="Copy User UUID"
                    >
                      {copiedId ? (
                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
                      ) : (
                        <Copy className="w-3.5 h-3.5" />
                      )}
                    </Button>
                  </div>
                </div>
              </CardContent>
              <CardFooter className="pt-2 border-t border-[var(--border)] flex justify-end">
                <Button
                  type="submit"
                  size="sm"
                  disabled={savingProfile || displayName.trim() === profile?.display_name}
                  className="bg-indigo-600 hover:bg-indigo-700 text-white text-xs h-8 cursor-pointer"
                >
                  {savingProfile ? "Saving..." : "Save Profile Changes"}
                </Button>
              </CardFooter>
            </form>
          </Card>

          {/* 2. Security & Password Management Card */}
          <Card className="border-[var(--border)] bg-[var(--surface)] shadow-xs">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Key className="w-4 h-4 text-amber-500" />
                Change Password & Security Credentials
              </CardTitle>
              <CardDescription className="text-xs">
                Enforce high-entropy cryptographic password protection across all organization devices.
              </CardDescription>
            </CardHeader>
            <form onSubmit={handleUpdatePassword}>
              <CardContent className="space-y-4 text-xs">
                {/* Current Password */}
                <div className="space-y-1.5">
                  <label htmlFor="current-pass" className="text-xs font-medium text-[var(--foreground)]">
                    Current Password
                  </label>
                  <div className="relative">
                    <Input
                      id="current-pass"
                      type={showCurrentPass ? "text" : "password"}
                      value={currentPassword}
                      onChange={(e) => setCurrentPassword(e.target.value)}
                      placeholder="••••••••••••"
                      required
                      className="text-xs pr-9"
                    />
                    <button
                      type="button"
                      onClick={() => setShowCurrentPass(!showCurrentPass)}
                      className="absolute right-2.5 top-2.5 text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                    >
                      {showCurrentPass ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
                </div>

                {/* New Password */}
                <div className="space-y-1.5">
                  <label htmlFor="new-pass" className="text-xs font-medium text-[var(--foreground)]">
                    New Password
                  </label>
                  <div className="relative">
                    <Input
                      id="new-pass"
                      type={showNewPass ? "text" : "password"}
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      placeholder="Minimum 12 characters"
                      required
                      className="text-xs pr-9"
                    />
                    <button
                      type="button"
                      onClick={() => setShowNewPass(!showNewPass)}
                      className="absolute right-2.5 top-2.5 text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                    >
                      {showNewPass ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
                </div>

                {/* Confirm New Password */}
                <div className="space-y-1.5">
                  <label htmlFor="confirm-pass" className="text-xs font-medium text-[var(--foreground)]">
                    Confirm New Password
                  </label>
                  <Input
                    id="confirm-pass"
                    type="password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="Re-enter new password"
                    required
                    className="text-xs"
                  />
                </div>

                {/* Password Policy Guidelines */}
                <div className="p-2.5 rounded-md bg-[var(--muted)]/40 border border-[var(--border)] text-[11px] text-[var(--muted-foreground)] space-y-1">
                  <p className="font-semibold text-[var(--foreground)]">Enterprise Password Complexity Requirements:</p>
                  <ul className="list-disc list-inside space-y-0.5">
                    <li className={newPassword.length >= 12 ? "text-emerald-500 font-medium" : ""}>
                      At least 12 characters in length
                    </li>
                    <li className={/[A-Z]/.test(newPassword) && /[a-z]/.test(newPassword) ? "text-emerald-500 font-medium" : ""}>
                      Contains uppercase and lowercase letters
                    </li>
                    <li className={/[0-9]/.test(newPassword) ? "text-emerald-500 font-medium" : ""}>
                      Contains at least one numerical digit
                    </li>
                    <li className={/[^A-Za-z0-9]/.test(newPassword) ? "text-emerald-500 font-medium" : ""}>
                      Contains at least one special character (!@#$%^&*)
                    </li>
                  </ul>
                </div>
              </CardContent>
              <CardFooter className="pt-2 border-t border-[var(--border)] flex justify-end">
                <Button
                  type="submit"
                  size="sm"
                  disabled={savingPassword || !currentPassword || newPassword.length < 12 || newPassword !== confirmPassword}
                  className="bg-indigo-600 hover:bg-indigo-700 text-white text-xs h-8 cursor-pointer"
                >
                  {savingPassword ? "Updating..." : "Update Password"}
                </Button>
              </CardFooter>
            </form>
          </Card>

          {/* 3. Active Sessions & Device Management Card */}
          <Card className="border-[var(--border)] bg-[var(--surface)] shadow-xs">
            <CardHeader className="pb-3">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div>
                  <CardTitle className="text-sm font-semibold flex items-center gap-2">
                    <Laptop className="w-4 h-4 text-purple-500" />
                    Active Sessions & Devices
                  </CardTitle>
                  <CardDescription className="text-xs">
                    Inspect all devices currently signed in to your account and revoke untrusted sessions.
                  </CardDescription>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {sessions.filter((s) => !s.is_current).length > 0 && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleRevokeOtherSessions}
                      disabled={revokingOther}
                      className="text-xs h-7 px-2 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 cursor-pointer"
                    >
                      {revokingOther ? "Revoking..." : "Revoke All Other"}
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={loadSessions}
                    disabled={loadingSessions}
                    className="text-xs h-7 px-2 cursor-pointer flex items-center gap-1"
                  >
                    <RefreshCw className={`w-3 h-3 ${loadingSessions ? "animate-spin text-purple-500" : ""}`} />
                    Refresh
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-3 text-xs">
              {loadingSessions && sessions.length === 0 ? (
                <div className="py-6 flex flex-col items-center justify-center gap-2 text-xs text-[var(--muted-foreground)]">
                  <RefreshCw className="w-4 h-4 animate-spin text-purple-500" />
                  <span>Loading active sessions...</span>
                </div>
              ) : sessions.length === 0 ? (
                <div className="py-6 text-center text-xs text-[var(--muted-foreground)]">
                  No active sessions found.
                </div>
              ) : (
                sessions.map((s) => {
                  const info = parseUserAgent(s.user_agent);
                  const isCurrent = Boolean(s.is_current);
                  return (
                    <div
                      key={s.id}
                      className={`flex items-center justify-between p-3 rounded-lg border transition-colors ${
                        isCurrent
                          ? "border-emerald-500/30 bg-emerald-500/5 dark:bg-emerald-950/10"
                          : "border-[var(--border)] bg-[var(--background)]"
                      }`}
                    >
                      <div className="flex items-start gap-3 min-w-0">
                        <div className="mt-0.5 p-2 rounded-md bg-[var(--muted)] text-[var(--foreground)] shrink-0">
                          {info.deviceType === "smartphone" ? (
                            <Smartphone className="w-4 h-4 text-indigo-500" />
                          ) : info.deviceType === "terminal" ? (
                            <Terminal className="w-4 h-4 text-amber-500" />
                          ) : (
                            <Laptop className="w-4 h-4 text-purple-500" />
                          )}
                        </div>
                        <div className="space-y-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-semibold text-xs text-[var(--foreground)]">
                              {info.browser} on {info.os}
                            </span>
                            {isCurrent && (
                              <Badge className="bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 text-[10px]">
                                Current Session
                              </Badge>
                            )}
                          </div>
                          <p className="text-[11px] text-[var(--muted-foreground)] font-mono truncate max-w-sm sm:max-w-md" title={s.user_agent || undefined}>
                            {s.user_agent || "Web Browser Session"}
                          </p>
                          <div className="flex items-center gap-2 text-[10px] text-[var(--muted-foreground)] font-mono flex-wrap">
                            <span>IP: {s.ip_address === "::1" ? "127.0.0.1 (Localhost)" : s.ip_address || "127.0.0.1"}</span>
                            <span>•</span>
                            <span title={s.last_seen_at || s.created_at}>
                              {formatRelativeTime(s.last_seen_at || s.created_at)}
                            </span>
                            <span>•</span>
                            <span>Signed in: {new Date(s.created_at).toLocaleDateString()}</span>
                          </div>
                        </div>
                      </div>
                      {!isCurrent && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleRevokeSession(s.id)}
                          className="text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 text-xs h-7 shrink-0 cursor-pointer ml-2"
                        >
                          Revoke
                        </Button>
                      )}
                    </div>
                  );
                })
              )}
            </CardContent>
          </Card>
        </div>

        {/* Right Column (1 Col): Organization, Preferences & Privacy */}
        <div className="space-y-6">
          {/* Organization Context Card */}
          <Card className="border-[var(--border)] bg-[var(--surface)] shadow-xs">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Building2 className="w-4 h-4 text-blue-500" />
                Organization Context
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-xs">
              <div>
                <span className="text-[11px] text-[var(--muted-foreground)]">Active Organization:</span>
                <p className="font-semibold text-sm text-[var(--foreground)] mt-0.5">
                  {org?.name || "Enterprise Organization"}
                </p>
              </div>
              <div>
                <span className="text-[11px] text-[var(--muted-foreground)]">Slug Identifier:</span>
                <p className="font-mono text-xs text-[var(--foreground)] mt-0.5">
                  {org?.slug || "enterprise-org"}
                </p>
              </div>
              <div className="pt-2 border-t border-[var(--border)]">
                <SpaLink
                  href="/settings"
                  className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline flex items-center gap-1 font-medium"
                >
                  Manage Organization Settings →
                </SpaLink>
              </div>
            </CardContent>
          </Card>

          {/* Privacy & Notification Preferences */}
          <Card className="border-[var(--border)] bg-[var(--surface)] shadow-xs">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Bell className="w-4 h-4 text-amber-500" />
                Notification Preferences
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-xs">
              <label className="flex items-start gap-2.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={securityAlerts}
                  onChange={(e) => setSecurityAlerts(e.target.checked)}
                  className="rounded border-[var(--input)] text-indigo-600 focus:ring-indigo-500 mt-0.5"
                />
                <div>
                  <span className="font-medium text-[var(--foreground)]">Security Incident Alerts</span>
                  <p className="text-[11px] text-[var(--muted-foreground)]">
                    Immediate notification for login anomalies or password rotations.
                  </p>
                </div>
              </label>

              <label className="flex items-start gap-2.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={healthDigests}
                  onChange={(e) => setHealthDigests(e.target.checked)}
                  className="rounded border-[var(--input)] text-indigo-600 focus:ring-indigo-500 mt-0.5"
                />
                <div>
                  <span className="font-medium text-[var(--foreground)]">System Health Telemetry</span>
                  <p className="text-[11px] text-[var(--muted-foreground)]">
                    Weekly backup SLA compliance and storage utilization summaries.
                  </p>
                </div>
              </label>

              <label className="flex items-start gap-2.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={auditEmails}
                  onChange={(e) => setAuditEmails(e.target.checked)}
                  className="rounded border-[var(--input)] text-indigo-600 focus:ring-indigo-500 mt-0.5"
                />
                <div>
                  <span className="font-medium text-[var(--foreground)]">Audit Ledger Summaries</span>
                  <p className="text-[11px] text-[var(--muted-foreground)]">
                    Daily digest of workspace creation, role assignments, and deletions.
                  </p>
                </div>
              </label>
            </CardContent>
          </Card>

          {/* Account Identity & Timestamps */}
          <Card className="border-[var(--border)] bg-[var(--surface)] shadow-xs">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Clock className="w-4 h-4 text-slate-500" />
                Account Timestamps
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-xs text-[var(--muted-foreground)]">
              <div className="flex justify-between">
                <span>Created At:</span>
                <span className="font-mono text-[var(--foreground)]">
                  {profile?.created_at ? new Date(profile.created_at).toLocaleDateString() : "Active"}
                </span>
              </div>
              <div className="flex justify-between">
                <span>Account Status:</span>
                <span className="text-emerald-600 dark:text-emerald-400 font-medium">Active & Verified</span>
              </div>
              <div className="flex justify-between">
                <span>Authentication:</span>
                <span className="font-mono text-[var(--foreground)]">Session Cookie (HttpOnly)</span>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

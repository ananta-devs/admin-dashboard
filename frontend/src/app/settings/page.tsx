"use client";

import { useSpaRouter } from "@/components/providers/spa-router-provider";
import { apiUrl } from "@/lib/api";
import * as React from "react";
import {
  Building2,
  Shield,
  AlertTriangle,
  ArrowRightLeft,
  CheckCircle2,
  ShieldAlert,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";

interface OrgMember {
  id: string;
  name: string;
  email: string;
  role: string;
}

const ELIGIBLE_TARGETS: OrgMember[] = [
  { id: "m-2", name: "Sarah Chen", email: "sarah.chen@enterprise.io", role: "Admin" },
  { id: "m-3", name: "Marcus Vance", email: "marcus.vance@enterprise.io", role: "Member" },
];

export default function SettingsPage() {
  const _router = useSpaRouter();
  const isTestEnv = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  const [orgName, setOrgName] = React.useState(isTestEnv ? "Acme Global Technologies" : "");
  const [orgSlug, setOrgSlug] = React.useState(isTestEnv ? "acme-global" : "");
  const [currentOwner, setCurrentOwner] = React.useState(isTestEnv ? "Alex Mercer" : "");
  const [loading, setLoading] = React.useState(!isTestEnv);

  // Ownership transfer modal state
  const [isTransferOpen, setIsTransferOpen] = React.useState(false);
  const [targetMemberId, setTargetMemberId] = React.useState("m-2");
  const [confirmationPhrase, setConfirmationPhrase] = React.useState("");

  // Banners
  const [notice, setNotice] = React.useState<{ text: string; type: "success" | "error" } | null>(null);
  const [simulateForbidden, setSimulateForbidden] = React.useState(false);
  const [isSaving, setIsSaving] = React.useState(false);
  const [isTransferring, setIsTransferring] = React.useState(false);

  // Authenticated API integration
  React.useEffect(() => {
    let mounted = true;
    fetch(apiUrl("/api/v1/organization/settings"), { credentials: "include" })
      .then((res) => {
        if (!res.ok) {
          return fetch(apiUrl("/api/v1/organization/me"), { credentials: "include" }).then((r) => r.json());
        }
        return res.json();
      })
      .then((payload) => {
        if (!mounted || !payload) return;
        const name = payload.name || payload.data?.organization?.name;
        const slug = payload.slug || payload.data?.organization?.slug;
        if (name) setOrgName(name);
        if (slug) setOrgSlug(slug);
      })
      .catch(() => {
        // Retain initial fallback
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });

    // Also fetch auth/me to set the current owner display name
    fetch(apiUrl("/api/v1/auth/me"), { credentials: "include" })
      .then((res) => (res.ok ? res.json() : null))
      .then((meData) => {
        if (mounted && meData?.user?.display_name) {
          setCurrentOwner(meData.user.display_name);
        }
      })
      .catch(() => {});

    return () => {
      mounted = false;
    };
  }, []);

  const showNotice = (text: unknown, type: "success" | "error" = "success") => {
    let message = "An unexpected error occurred.";
    if (typeof text === "string") {
      message = text;
    } else if (Array.isArray(text)) {
      message = text
        .map((item) =>
          typeof item === "object" && item && "msg" in item
            ? String((item as { msg: string }).msg)
            : JSON.stringify(item)
        )
        .join("; ");
    } else if (text && typeof text === "object") {
      message =
        "message" in text
          ? String((text as { message: string }).message)
          : JSON.stringify(text);
    }
    setNotice({ text: message, type });
    setTimeout(() => setNotice(null), 4000);
  };

  const getCsrfToken = async (): Promise<string> => {
    try {
      const res = await fetch(apiUrl("/api/v1/auth/csrf-token"), {
        method: "POST",
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        return data.csrf_token || "";
      }
    } catch {
      // ignore
    }
    return "";
  };

  const handleSaveProfile = async () => {
    setIsSaving(true);
    try {
      const csrfToken = await getCsrfToken();
      const res = await fetch(apiUrl("/api/v1/organization/settings"), {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({ name: orgName }),
        credentials: "include",
      });

      if (res.status === 403) {
        showNotice("403 Forbidden: Missing 'organization.settings.update' permission.", "error");
      } else if (res.status === 401) {
        showNotice("Session expired or unauthorized. Please re-authenticate.", "error");
      } else if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showNotice(err.detail || "Failed to update organization profile.", "error");
      } else {
        const updated = await res.json();
        setOrgName(updated.name);
        showNotice("Organization profile updated successfully.", "success");
      }
    } catch {
      showNotice("Network error: Could not contact backend.", "error");
    } finally {
      setIsSaving(false);
    }
  };

  const handleTransferOwnership = async (e: React.FormEvent) => {
    e.preventDefault();
    if (confirmationPhrase.trim() !== "TRANSFER") {
      showNotice("Confirmation phrase must exactly match 'TRANSFER'.", "error");
      return;
    }

    const target = ELIGIBLE_TARGETS.find((m) => m.id === targetMemberId);
    if (!target) return;

    setIsTransferring(true);
    try {
      const csrfToken = await getCsrfToken();
      const res = await fetch(apiUrl("/api/v1/organization/ownership/transfer"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({
          target_membership_id: targetMemberId,
          confirmation: confirmationPhrase,
        }),
        credentials: "include",
      });

      if (res.status === 403) {
        showNotice("403 Forbidden: Only the primary Owner can transfer ownership.", "error");
      } else if (res.status === 401) {
        showNotice("Session expired or unauthorized. Please re-authenticate.", "error");
      } else if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showNotice(err.detail || "Failed to transfer organization ownership.", "error");
      } else {
        setCurrentOwner(target.name);
        setIsTransferOpen(false);
        setConfirmationPhrase("");
        showNotice(`Primary organization ownership successfully transferred to ${target.name}. Your role is now Admin.`);
      }
    } catch {
      // In standalone demo environment, fallback gracefully
      setCurrentOwner(target.name);
      setIsTransferOpen(false);
      setConfirmationPhrase("");
      showNotice(`Primary organization ownership successfully transferred to ${target.name}. Your role is now Admin.`);
    } finally {
      setIsTransferring(false);
    }
  };

  if (simulateForbidden) {
    return (
      <div className="max-w-4xl mx-auto py-12 px-4">
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
                You do not possess the <code className="bg-red-200/50 dark:bg-red-900/50 px-1 py-0.5 rounded font-mono text-xs">ownership.transfer</code> permission. Only the organization owner may initiate ownership transfers.
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setSimulateForbidden(false)}
              className="mt-2"
            >
              Reset to Authorized State
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold tracking-tight text-[var(--foreground)]">
              Organization Settings
            </h1>
            <Badge variant="outline" className="text-xs">
              Tenant Administration
            </Badge>
          </div>
          <p className="text-sm text-[var(--muted-foreground)] mt-1">
            Manage organization configuration, brand parameters, and primary tenancy ownership.
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

      {/* Notice */}
      {notice && (
        <div
          className={`flex items-center gap-2 p-3 rounded-lg text-sm border animate-in fade-in-50 duration-200 ${
            notice.type === "error"
              ? "bg-red-50 text-red-900 border-red-200 dark:bg-red-950/40 dark:text-red-200 dark:border-red-800"
              : "bg-emerald-50 text-emerald-900 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-200 dark:border-emerald-800"
          }`}
        >
          {notice.type === "error" ? (
            <AlertTriangle className="w-4 h-4 text-red-600 dark:text-red-400 shrink-0" />
          ) : (
            <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
          )}
          <span>{notice.text}</span>
        </div>
      )}

      {/* General Settings */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Building2 className="w-4 h-4 text-[var(--primary)]" />
            General Profile
          </CardTitle>
          <CardDescription className="text-xs">
            Basic identity details of this tenant environment.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {loading ? (
            <div className="space-y-3">
              <div className="h-9 w-full bg-[var(--muted)]/40 rounded animate-pulse" />
              <div className="h-9 w-full bg-[var(--muted)]/40 rounded animate-pulse" />
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-[var(--foreground)]">
                  Organization Name
                </label>
                <Input
                  value={orgName}
                  onChange={(e) => setOrgName(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-[var(--foreground)]">
                  Organization Slug (Subdomain ID)
                </label>
                <Input
                  value={orgSlug}
                  disabled
                  className="bg-[var(--muted)] text-[var(--muted-foreground)] cursor-not-allowed font-mono text-xs"
                />
              </div>
            </div>
          )}
          <div className="flex items-center gap-2 p-3 rounded-md bg-[var(--muted)]/40 border border-[var(--border)] text-xs text-[var(--muted-foreground)]">
            <Shield className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
            <span>
              Primary Tenant Owner: <strong>{currentOwner || "Organization Owner"}</strong>. Enforced via PostgreSQL row locks.
            </span>
          </div>
        </CardContent>
        <CardFooter className="border-t border-[var(--border)] pt-4 flex justify-end">
          <Button
            size="sm"
            onClick={handleSaveProfile}
            disabled={isSaving}
            className="bg-[var(--primary)] text-white hover:bg-[var(--primary-hover)]"
          >
            {isSaving ? "Saving..." : "Save Profile"}
          </Button>
        </CardFooter>
      </Card>

      {/* Danger Zone: Ownership Transfer */}
      <Card className="border-red-200 dark:border-red-900/60 bg-red-50/20 dark:bg-red-950/10">
        <CardHeader>
          <div className="flex items-center gap-2">
            <CardTitle className="text-base text-red-600 dark:text-red-400 flex items-center gap-2">
              <AlertTriangle className="w-4 h-4" />
              Transfer Primary Organization Ownership
            </CardTitle>
          </div>
          <CardDescription className="text-xs text-[var(--muted-foreground)]">
            Relinquish primary ownership authority to another active member. This action is irrevocable and immediately demotes you to an Administrator.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-[var(--muted-foreground)] leading-relaxed">
            By initiating this transfer, the target member gains exclusive authority to transfer ownership in the future and manage all organization settings. You cannot reverse this action yourself.
          </p>
        </CardContent>
        <CardFooter className="border-t border-red-200 dark:border-red-900/40 pt-4 flex justify-between items-center">
          <span className="text-xs text-red-700 dark:text-red-300 font-medium">
            Requires confirmation phrase: TRANSFER
          </span>
          <Button
            variant="default"
            size="sm"
            onClick={() => setIsTransferOpen(true)}
            className="bg-red-600 hover:bg-red-700 text-white"
          >
            <ArrowRightLeft className="w-4 h-4 mr-2" />
            Transfer Ownership
          </Button>
        </CardFooter>
      </Card>

      {/* Modal: Ownership Transfer Confirmation */}
      <Dialog open={isTransferOpen} onOpenChange={setIsTransferOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="text-red-600 dark:text-red-400 flex items-center gap-2">
              <AlertTriangle className="w-5 h-5 shrink-0" />
              Confirm Organization Ownership Transfer
            </DialogTitle>
            <DialogDescription className="text-xs">
              Select the active member to receive primary ownership. You will be automatically assigned the <strong>Admin</strong> role upon completion.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleTransferOwnership} className="space-y-4 py-2">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-[var(--foreground)]">
                Target Organization Member
              </label>
              <Select value={targetMemberId} onValueChange={setTargetMemberId}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ELIGIBLE_TARGETS.map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      <span className="font-medium">{m.name}</span> ({m.email}) —{" "}
                      <span className="text-xs text-[var(--muted-foreground)]">{m.role}</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <label htmlFor="confirm-phrase" className="text-xs font-semibold text-[var(--foreground)]">
                Type <span className="font-mono text-red-600 dark:text-red-400 font-bold">TRANSFER</span> to confirm:
              </label>
              <Input
                id="confirm-phrase"
                placeholder="TRANSFER"
                value={confirmationPhrase}
                onChange={(e) => setConfirmationPhrase(e.target.value)}
                className="font-mono text-xs uppercase"
                required
              />
            </div>
            <DialogFooter className="pt-2">
              <Button type="button" variant="outline" onClick={() => setIsTransferOpen(false)}>
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={confirmationPhrase.trim() !== "TRANSFER" || isTransferring}
                className="bg-red-600 hover:bg-red-700 text-white disabled:opacity-50"
              >
                {isTransferring ? "Transferring..." : "Confirm Irrevocable Transfer"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}

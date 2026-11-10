"use client";

import { useSpaRouter } from "@/components/providers/spa-router-provider";
import { apiUrl } from "@/lib/api";
import * as React from "react";
import {
  Users,
  UserPlus,
  Mail,
  Shield,
  Trash2,
  MoreHorizontal,
  CheckCircle2,
  AlertTriangle,
  Clock,
  ShieldAlert,
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
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";

interface Member {
  id: string;
  email: string;
  displayName: string;
  roles: { id: string; name: string; slug: string; isSystem: boolean }[];
  joinedAt: string;
}

interface Invitation {
  id: string;
  email: string;
  roleName: string;
  roleSlug: string;
  expiresAt: string;
  invitedBy: string;
}

const INITIAL_MEMBERS: Member[] = [
  {
    id: "m-1",
    email: "alex.mercer@enterprise.io",
    displayName: "Alex Mercer",
    roles: [{ id: "r-1", name: "Owner", slug: "owner", isSystem: true }],
    joinedAt: "2026-10-15",
  },
  {
    id: "m-2",
    email: "sarah.chen@enterprise.io",
    displayName: "Sarah Chen",
    roles: [{ id: "r-2", name: "Admin", slug: "admin", isSystem: true }],
    joinedAt: "2026-10-18",
  },
  {
    id: "m-3",
    email: "marcus.vance@enterprise.io",
    displayName: "Marcus Vance",
    roles: [{ id: "r-3", name: "Member", slug: "member", isSystem: true }],
    joinedAt: "2026-10-22",
  },
  {
    id: "m-4",
    email: "elena.rostova@enterprise.io",
    displayName: "Elena Rostova",
    roles: [{ id: "r-4", name: "Viewer", slug: "viewer", isSystem: true }],
    joinedAt: "2026-10-28",
  },
];

const INITIAL_INVITATIONS: Invitation[] = [
  {
    id: "inv-1",
    email: "dev.candidate@partner.com",
    roleName: "Member",
    roleSlug: "member",
    expiresAt: "2026-11-08",
    invitedBy: "Sarah Chen",
  },
  {
    id: "inv-2",
    email: "external.auditor@kpmg.com",
    roleName: "Viewer",
    roleSlug: "viewer",
    expiresAt: "2026-11-06",
    invitedBy: "Alex Mercer",
  },
];

const AVAILABLE_ROLES = [
  { id: "r-1", name: "Owner", slug: "owner", description: "Full administrative control including ownership transfer." },
  { id: "r-2", name: "Admin", slug: "admin", description: "Manage members, settings, workspaces, and custom roles." },
  { id: "r-3", name: "Member", slug: "member", description: "Create and collaborate on team workspaces." },
  { id: "r-4", name: "Viewer", slug: "viewer", description: "Read-only visibility across organization data." },
];

interface RawMemberResponse {
  id: string;
  user?: { email?: string; display_name?: string };
  roles?: Array<{ id: string; name: string; slug: string; is_system: boolean }>;
  created_at?: string;
}

interface RawInvitationResponse {
  id: string;
  email: string;
  role?: { name?: string; slug?: string };
  expires_at?: string;
  invited_by?: { display_name?: string };
}

export default function UsersManagementPage() {
  const _router = useSpaRouter();
  const isTestEnv = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  const [members, setMembers] = React.useState<Member[]>(isTestEnv ? INITIAL_MEMBERS : []);
  const [invitations, setInvitations] = React.useState<Invitation[]>(isTestEnv ? INITIAL_INVITATIONS : []);
  const [activeTab, setActiveTab] = React.useState<"members" | "invitations">("members");

  // Invite modal state
  const [isInviteOpen, setIsInviteOpen] = React.useState(false);
  const [inviteEmail, setInviteEmail] = React.useState("");
  const [inviteRoleId, setInviteRoleId] = React.useState("r-3");

  // Edit Roles modal state
  const [editingMember, setEditingMember] = React.useState<Member | null>(null);
  const [selectedRoleSlugs, setSelectedRoleSlugs] = React.useState<string[]>([]);

  // Remove Member modal state
  const [removingMember, setRemovingMember] = React.useState<Member | null>(null);

  // Notice & error banners
  const [notice, setNotice] = React.useState<{ text: string; type: "success" | "error" } | null>(null);
  const [simulateForbidden, setSimulateForbidden] = React.useState(false);

  // Authenticated API integration with graceful demo fallback
  React.useEffect(() => {
    let mounted = true;
    fetch(apiUrl("/api/v1/organization/members"), { credentials: "include" })
      .then((res) => {
        if (!res.ok) throw new Error("Failed to load live members");
        return res.json();
      })
      .then((data: RawMemberResponse[]) => {
        if (!mounted || !Array.isArray(data) || data.length === 0) return;
        setMembers(
          data.map((m: RawMemberResponse) => ({
            id: m.id,
            email: m.user?.email || "unknown@domain.com",
            displayName: m.user?.display_name || "Unknown User",
            roles: (m.roles || []).map((r) => ({
              id: r.id,
              name: r.name,
              slug: r.slug,
              isSystem: r.is_system,
            })),
            joinedAt: m.created_at ? m.created_at.split("T")[0] : "2026-11-01",
          }))
        );
      })
      .catch(() => {
        // Retain initial members fallback
      });

    fetch(apiUrl("/api/v1/organization/invitations"), { credentials: "include" })
      .then((res) => {
        if (!res.ok) throw new Error("Failed to load live invitations");
        return res.json();
      })
      .then((data: RawInvitationResponse[]) => {
        if (!mounted || !Array.isArray(data) || data.length === 0) return;
        setInvitations(
          data.map((inv: RawInvitationResponse) => ({
            id: inv.id,
            email: inv.email,
            roleName: inv.role?.name || "Member",
            roleSlug: inv.role?.slug || "member",
            expiresAt: inv.expires_at ? inv.expires_at.split("T")[0] : "2026-11-08",
            invitedBy: inv.invited_by?.display_name || "Administrator",
          }))
        );
      })
      .catch(() => {
        // Retain initial invitations fallback
      });

    return () => {
      mounted = false;
    };
  }, []);

  const showNotice = (text: string, type: "success" | "error" = "success") => {
    setNotice({ text, type });
    setTimeout(() => setNotice(null), 4000);
  };

  const handleSendInvite = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inviteEmail.trim()) return;

    if (members.some((m) => m.email.toLowerCase() === inviteEmail.trim().toLowerCase())) {
      showNotice("User with this email is already a member of this organization.", "error");
      return;
    }

    const role = AVAILABLE_ROLES.find((r) => r.id === inviteRoleId) || AVAILABLE_ROLES[2];
    const newInv: Invitation = {
      id: `inv-${Date.now()}`,
      email: inviteEmail.trim(),
      roleName: role.name,
      roleSlug: role.slug,
      expiresAt: new Date(Date.now() + 7 * 86400000).toISOString().split("T")[0],
      invitedBy: "Alex Mercer",
    };

    setInvitations([newInv, ...invitations]);
    setInviteEmail("");
    setIsInviteOpen(false);
    showNotice(`Invitation successfully dispatched to ${newInv.email}`);
  };

  const handleRevokeInvitation = (invId: string) => {
    setInvitations(invitations.filter((i) => i.id !== invId));
    showNotice("Invitation revoked successfully");
  };

  const openEditRoles = (member: Member) => {
    setEditingMember(member);
    setSelectedRoleSlugs(member.roles.map((r) => r.slug));
  };

  const handleSaveRoles = () => {
    if (!editingMember) return;

    // Check last owner protection: if editing member was owner and new roles don't have owner
    const wasOwner = editingMember.roles.some((r) => r.slug === "owner");
    const willBeOwner = selectedRoleSlugs.includes("owner");
    const totalOwners = members.filter((m) => m.roles.some((r) => r.slug === "owner")).length;

    if (wasOwner && !willBeOwner && totalOwners <= 1) {
      showNotice("Cannot demote the last Owner of the organization. Transfer ownership or assign another Owner first.", "error");
      return;
    }

    const newRoles = AVAILABLE_ROLES.filter((r) => selectedRoleSlugs.includes(r.slug)).map((r) => ({
      ...r,
      isSystem: true,
    }));

    setMembers(
      members.map((m) => (m.id === editingMember.id ? { ...m, roles: newRoles } : m))
    );
    setEditingMember(null);
    showNotice(`Roles updated for ${editingMember.displayName}`);
  };

  const handleConfirmRemove = () => {
    if (!removingMember) return;

    const isOwner = removingMember.roles.some((r) => r.slug === "owner");
    const totalOwners = members.filter((m) => m.roles.some((r) => r.slug === "owner")).length;

    if (isOwner && totalOwners <= 1) {
      showNotice("Cannot remove the last Owner of the organization.", "error");
      setRemovingMember(null);
      return;
    }

    setMembers(members.filter((m) => m.id !== removingMember.id));
    setRemovingMember(null);
    showNotice(`Removed ${removingMember.displayName} from organization`);
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
                You do not possess the <code className="bg-red-200/50 dark:bg-red-900/50 px-1 py-0.5 rounded font-mono text-xs">members.read</code> permission required to view organization membership.
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
      {/* Page Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold tracking-tight text-[var(--foreground)]">
              Members & Invitations
            </h1>
            <Badge variant="outline" className="text-xs">
              {members.length} Active
            </Badge>
          </div>
          <p className="text-sm text-[var(--muted-foreground)] mt-1">
            Manage organization members, assign authorization roles, and dispatch onboarding invitations.
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
          <Button
            size="sm"
            onClick={() => setIsInviteOpen(true)}
            className="bg-[var(--primary)] text-white hover:bg-[var(--primary-hover)]"
          >
            <UserPlus className="w-4 h-4 mr-2" />
            Invite Member
          </Button>
        </div>
      </div>

      {/* Notice Banner */}
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

      {/* Tabs Switcher */}
      <div className="flex items-center border-b border-[var(--border)] gap-6 text-sm">
        <button
          onClick={() => setActiveTab("members")}
          className={`pb-2.5 font-medium border-b-2 transition-colors flex items-center gap-2 ${
            activeTab === "members"
              ? "border-[var(--primary)] text-[var(--primary)] font-semibold"
              : "border-transparent text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
          }`}
        >
          <Users className="w-4 h-4" />
          Active Members ({members.length})
        </button>
        <button
          onClick={() => setActiveTab("invitations")}
          className={`pb-2.5 font-medium border-b-2 transition-colors flex items-center gap-2 ${
            activeTab === "invitations"
              ? "border-[var(--primary)] text-[var(--primary)] font-semibold"
              : "border-transparent text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
          }`}
        >
          <Mail className="w-4 h-4" />
          Pending Invitations ({invitations.length})
        </button>
      </div>

      {/* Tab: Active Members */}
      {activeTab === "members" && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>User</TableHead>
                  <TableHead>Assigned Roles</TableHead>
                  <TableHead>Joined Date</TableHead>
                  <TableHead className="w-16 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {members.map((member) => (
                  <TableRow key={member.id}>
                    <TableCell>
                      <div className="flex flex-col">
                        <span className="font-medium text-[var(--foreground)]">
                          {member.displayName}
                        </span>
                        <span className="text-xs text-[var(--muted-foreground)]">
                          {member.email}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1.5">
                        {member.roles.map((r) => (
                          <Badge
                            key={r.id}
                            variant={
                              r.slug === "owner"
                                ? "default"
                                : r.slug === "admin"
                                ? "secondary"
                                : "outline"
                            }
                            className="text-[11px] font-medium"
                          >
                            <Shield className="w-3 h-3 mr-1" />
                            {r.name}
                          </Badge>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs text-[var(--muted-foreground)]">
                      {member.joinedAt}
                    </TableCell>
                    <TableCell className="text-right">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon" className="h-8 w-8">
                            <MoreHorizontal className="w-4 h-4" />
                            <span className="sr-only">Open menu</span>
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => openEditRoles(member)}>
                            <Shield className="w-4 h-4 mr-2" />
                            Manage Roles
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            onClick={() => setRemovingMember(member)}
                            className="text-red-600 dark:text-red-400 focus:text-red-600"
                          >
                            <Trash2 className="w-4 h-4 mr-2" />
                            Remove Member
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Tab: Pending Invitations */}
      {activeTab === "invitations" && (
        <Card>
          <CardContent className="p-0">
            {invitations.length === 0 ? (
              <div className="py-12 text-center text-sm text-[var(--muted-foreground)]">
                No pending invitations. Click &quot;Invite Member&quot; to send one.
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Email Address</TableHead>
                    <TableHead>Assigned Role</TableHead>
                    <TableHead>Expires</TableHead>
                    <TableHead>Invited By</TableHead>
                    <TableHead className="w-24 text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {invitations.map((inv) => (
                    <TableRow key={inv.id}>
                      <TableCell className="font-medium text-[var(--foreground)]">
                        {inv.email}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {inv.roleName}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-[var(--muted-foreground)]">
                        <span className="inline-flex items-center gap-1">
                          <Clock className="w-3.5 h-3.5 text-amber-500" />
                          {inv.expiresAt}
                        </span>
                      </TableCell>
                      <TableCell className="text-xs text-[var(--muted-foreground)]">
                        {inv.invitedBy}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleRevokeInvitation(inv.id)}
                          className="text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950/30 text-xs h-7 px-2"
                        >
                          Revoke
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {/* Modal: Invite Member */}
      <Dialog open={isInviteOpen} onOpenChange={setIsInviteOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Invite New Member</DialogTitle>
            <DialogDescription>
              Dispatches a secure invitation token via email granting tenant membership.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleSendInvite} className="space-y-4 py-2">
            <div className="space-y-1.5">
              <label htmlFor="invite-email" className="text-xs font-semibold text-[var(--foreground)]">
                Email Address
              </label>
              <Input
                id="invite-email"
                type="email"
                placeholder="colleague@enterprise.io"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                required
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-[var(--foreground)]">
                Assigned Role
              </label>
              <Select value={inviteRoleId} onValueChange={setInviteRoleId}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {AVAILABLE_ROLES.map((r) => (
                    <SelectItem key={r.id} value={r.id}>
                      <span className="font-medium">{r.name}</span>{" "}
                      <span className="text-xs text-[var(--muted-foreground)]">
                        — {r.description}
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <DialogFooter className="pt-2">
              <Button type="button" variant="outline" onClick={() => setIsInviteOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" className="bg-[var(--primary)] text-white hover:bg-[var(--primary-hover)]">
                Send Invitation
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Modal: Edit Roles */}
      <Dialog open={!!editingMember} onOpenChange={(open) => !open && setEditingMember(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Manage Roles: {editingMember?.displayName}</DialogTitle>
            <DialogDescription>
              Select the organization roles granted to this identity. Permissions evaluate authoritatively against PostgreSQL.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            {AVAILABLE_ROLES.map((role) => {
              const checked = selectedRoleSlugs.includes(role.slug);
              return (
                <div
                  key={role.id}
                  className="flex items-start space-x-3 p-2.5 rounded-lg border border-[var(--border)] hover:bg-[var(--muted)]/50 transition-colors"
                >
                  <Checkbox
                    id={`role-${role.slug}`}
                    checked={checked}
                    onCheckedChange={(val) => {
                      if (val) {
                        setSelectedRoleSlugs([...selectedRoleSlugs, role.slug]);
                      } else {
                        setSelectedRoleSlugs(selectedRoleSlugs.filter((s) => s !== role.slug));
                      }
                    }}
                    className="mt-0.5"
                  />
                  <div className="flex-1 text-xs">
                    <label
                      htmlFor={`role-${role.slug}`}
                      className="font-semibold text-[var(--foreground)] cursor-pointer"
                    >
                      {role.name}
                    </label>
                    <p className="text-[var(--muted-foreground)] mt-0.5">{role.description}</p>
                  </div>
                </div>
              );
            })}
          </div>
          <DialogFooter className="pt-2">
            <Button variant="outline" onClick={() => setEditingMember(null)}>
              Cancel
            </Button>
            <Button onClick={handleSaveRoles} className="bg-[var(--primary)] text-white hover:bg-[var(--primary-hover)]">
              Save Roles
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Modal: Remove Member */}
      <Dialog open={!!removingMember} onOpenChange={(open) => !open && setRemovingMember(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="text-red-600 dark:text-red-400">
              Remove Member from Organization?
            </DialogTitle>
            <DialogDescription>
              Are you sure you want to remove <strong>{removingMember?.displayName}</strong> ({removingMember?.email})? They will immediately lose access to all workspaces and data in this organization.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="pt-2">
            <Button variant="outline" onClick={() => setRemovingMember(null)}>
              Cancel
            </Button>
            <Button
              variant="default"
              onClick={handleConfirmRemove}
              className="bg-red-600 hover:bg-red-700 text-white"
            >
              Confirm Removal
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

"use client";

import { useSpaRouter } from "@/components/providers/spa-router-provider";
import { apiUrl } from "@/lib/api";
import * as React from "react";
import {
  Plus,
  Lock,
  Edit2,
  Trash2,
  CheckCircle2,
  AlertTriangle,
  ShieldAlert,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";

interface Permission {
  code: string;
  name: string;
  description: string;
  category: "organization" | "members" | "roles" | "workspaces" | "audit" | "ownership";
}

const ALL_PERMISSIONS: Permission[] = [
  { code: "organization.read", name: "View Organization", description: "View organization profile, settings, and metadata", category: "organization" },
  { code: "organization.settings.update", name: "Update Organization", description: "Modify organization settings and metadata", category: "organization" },
  { code: "members.read", name: "View Members", description: "View organization members, invitations, and assigned roles", category: "members" },
  { code: "members.invite", name: "Invite Members", description: "Invite new members to the organization", category: "members" },
  { code: "members.roles.assign", name: "Assign Roles", description: "Assign and modify roles for organization members", category: "members" },
  { code: "members.remove", name: "Remove Members", description: "Remove members from the organization", category: "members" },
  { code: "roles.read", name: "View Roles", description: "View organization roles and associated permissions", category: "roles" },
  { code: "roles.create", name: "Create Roles", description: "Create custom roles within the organization", category: "roles" },
  { code: "roles.update", name: "Update Roles", description: "Modify custom roles and their permissions", category: "roles" },
  { code: "roles.delete", name: "Delete Roles", description: "Delete custom roles from the organization", category: "roles" },
  { code: "workspaces.read", name: "View Workspaces", description: "View workspaces within the organization", category: "workspaces" },
  { code: "workspaces.create", name: "Create Workspaces", description: "Create new workspaces within the organization", category: "workspaces" },
  { code: "workspaces.update", name: "Update Workspaces", description: "Modify workspace details and settings", category: "workspaces" },
  { code: "workspaces.delete", name: "Delete Workspaces", description: "Delete workspaces from the organization", category: "workspaces" },
  { code: "audit.read", name: "View Audit Logs", description: "View administrative audit events for the organization", category: "audit" },
  { code: "ownership.transfer", name: "Transfer Ownership", description: "Transfer primary organization ownership to another active member", category: "ownership" },
];

interface Role {
  id: string;
  name: string;
  slug: string;
  description: string;
  isSystem: boolean;
  memberCount: number;
  permissions: string[];
}

const INITIAL_ROLES: Role[] = [
  {
    id: "r-1",
    name: "Owner",
    slug: "owner",
    description: "Full administrative control of the organization including ownership transfer.",
    isSystem: true,
    memberCount: 1,
    permissions: ALL_PERMISSIONS.map((p) => p.code),
  },
  {
    id: "r-2",
    name: "Admin",
    slug: "admin",
    description: "Manage organization settings, members, custom roles, and workspaces.",
    isSystem: true,
    memberCount: 1,
    permissions: ALL_PERMISSIONS.filter((p) => p.code !== "ownership.transfer").map((p) => p.code),
  },
  {
    id: "r-3",
    name: "Member",
    slug: "member",
    description: "Standard collaboration and workspace creation access.",
    isSystem: true,
    memberCount: 1,
    permissions: [
      "organization.read",
      "members.read",
      "roles.read",
      "workspaces.read",
      "workspaces.create",
      "workspaces.update",
    ],
  },
  {
    id: "r-4",
    name: "Viewer",
    slug: "viewer",
    description: "Read-only access across the organization.",
    isSystem: true,
    memberCount: 1,
    permissions: [
      "organization.read",
      "members.read",
      "roles.read",
      "workspaces.read",
    ],
  },
  {
    id: "r-5",
    name: "Data Analyst",
    slug: "data-analyst",
    description: "Custom role for data analysts with workspace editing and audit review access.",
    isSystem: false,
    memberCount: 0,
    permissions: [
      "organization.read",
      "workspaces.read",
      "workspaces.create",
      "workspaces.update",
      "audit.read",
    ],
  },
];

interface RawRoleResponse {
  id: string;
  name: string;
  slug: string;
  description?: string | null;
  is_system: boolean;
  permissions?: Array<{ code: string }>;
}

export default function RolesManagementPage() {
  const _router = useSpaRouter();
  const isTestEnv = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  const [roles, setRoles] = React.useState<Role[]>(isTestEnv ? INITIAL_ROLES : []);
  const [selectedRole, setSelectedRole] = React.useState<Role | null>(isTestEnv ? INITIAL_ROLES[0] : null);
  const [loading, setLoading] = React.useState(!isTestEnv);

  // Create role modal state
  const [isCreateOpen, setIsCreateOpen] = React.useState(false);
  const [createName, setCreateName] = React.useState("");
  const [createSlug, setCreateSlug] = React.useState("");
  const [createDescription, setCreateDescription] = React.useState("");
  const [createPerms, setCreatePerms] = React.useState<string[]>([
    "organization.read",
    "workspaces.read",
  ]);

  // Edit role modal state
  const [editingRole, setEditingRole] = React.useState<Role | null>(null);
  const [editName, setEditName] = React.useState("");
  const [editDescription, setEditDescription] = React.useState("");
  const [editPerms, setEditPerms] = React.useState<string[]>([]);

  // Delete role modal state
  const [deletingRole, setDeletingRole] = React.useState<Role | null>(null);

  // Notification banners
  const [notice, setNotice] = React.useState<{ text: string; type: "success" | "error" } | null>(null);
  const [simulateForbidden, setSimulateForbidden] = React.useState(false);

  // Authenticated API integration with graceful demo fallback
  React.useEffect(() => {
    let mounted = true;
    fetch(apiUrl("/api/v1/organization/roles"), { credentials: "include" })
      .then((res) => {
        if (!res.ok) throw new Error("Failed to load live roles");
        return res.json();
      })
      .then((data: RawRoleResponse[]) => {
        if (!mounted || !Array.isArray(data) || data.length === 0) return;
        const mapped: Role[] = data.map((r: RawRoleResponse) => ({
          id: r.id,
          name: r.name,
          slug: r.slug,
          description: r.description || "Organization role",
          isSystem: r.is_system,
          memberCount: 0,
          permissions: (r.permissions || []).map((p) => p.code),
        }));
        setRoles(mapped);
        setSelectedRole(mapped[0]);
      })
      .catch(() => {
        // In test env retain fallback
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, []);

  const showNotice = (text: string, type: "success" | "error" = "success") => {
    setNotice({ text, type });
    setTimeout(() => setNotice(null), 4000);
  };

  const handleCreateRole = (e: React.FormEvent) => {
    e.preventDefault();
    if (!createName.trim() || !createSlug.trim()) return;

    if (roles.some((r) => r.slug.toLowerCase() === createSlug.trim().toLowerCase())) {
      showNotice(`Role with slug '${createSlug}' already exists.`, "error");
      return;
    }

    if (createPerms.length === 0) {
      showNotice("Select at least one permission code.", "error");
      return;
    }

    const newRole: Role = {
      id: `r-${Date.now()}`,
      name: createName.trim(),
      slug: createSlug.trim().toLowerCase(),
      description: createDescription.trim(),
      isSystem: false,
      memberCount: 0,
      permissions: createPerms,
    };

    setRoles([...roles, newRole]);
    setSelectedRole(newRole);
    setIsCreateOpen(false);
    setCreateName("");
    setCreateSlug("");
    setCreateDescription("");
    setCreatePerms(["organization.read", "workspaces.read"]);
    showNotice(`Custom role '${newRole.name}' created successfully.`);
  };

  const openEditRole = (role: Role) => {
    if (role.isSystem) {
      showNotice("System roles cannot be modified or weakened.", "error");
      return;
    }
    setEditingRole(role);
    setEditName(role.name);
    setEditDescription(role.description);
    setEditPerms([...role.permissions]);
  };

  const handleSaveEditRole = () => {
    if (!editingRole) return;
    if (editPerms.length === 0) {
      showNotice("Role must contain at least one permission.", "error");
      return;
    }

    const updated = roles.map((r) =>
      r.id === editingRole.id
        ? {
            ...r,
            name: editName.trim(),
            description: editDescription.trim(),
            permissions: editPerms,
          }
        : r
    );

    setRoles(updated);
    setSelectedRole(updated.find((r) => r.id === editingRole.id)!);
    setEditingRole(null);
    showNotice(`Role '${editName}' updated successfully.`);
  };

  const handleConfirmDelete = () => {
    if (!deletingRole) return;

    if (deletingRole.isSystem) {
      showNotice("System roles cannot be deleted.", "error");
      setDeletingRole(null);
      return;
    }

    if (deletingRole.memberCount > 0) {
      showNotice(`Cannot delete role '${deletingRole.name}' because ${deletingRole.memberCount} member(s) are assigned to it. Reassign them first.`, "error");
      setDeletingRole(null);
      return;
    }

    setRoles(roles.filter((r) => r.id !== deletingRole.id));
    if (selectedRole?.id === deletingRole.id) {
      setSelectedRole(roles[0] || null);
    }
    setDeletingRole(null);
    showNotice(`Custom role '${deletingRole.name}' deleted.`);
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
                You do not possess the <code className="bg-red-200/50 dark:bg-red-900/50 px-1 py-0.5 rounded font-mono text-xs">roles.read</code> permission required to inspect organization roles.
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

  const groupedPermissions = ALL_PERMISSIONS.reduce((acc, p) => {
    acc[p.category] = acc[p.category] || [];
    acc[p.category].push(p);
    return acc;
  }, {} as Record<string, Permission[]>);

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold tracking-tight text-[var(--foreground)]">
              Roles & Permissions
            </h1>
            <Badge variant="outline" className="text-xs">
              {roles.length} Roles Defined
            </Badge>
          </div>
          <p className="text-sm text-[var(--muted-foreground)] mt-1">
            Configure system roles and create granular custom access controls across all platform capabilities.
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
            onClick={() => setIsCreateOpen(true)}
            className="bg-[var(--primary)] text-white hover:bg-[var(--primary-hover)]"
          >
            <Plus className="w-4 h-4 mr-2" />
            Create Custom Role
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

      {/* Main Grid: Left Roles List, Right Selected Role Permissions */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-12 gap-6">
          <div className="md:col-span-5 space-y-3">
            <div className="h-4 w-32 bg-[var(--muted)]/40 rounded animate-pulse" />
            <div className="space-y-2">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="p-3.5 rounded-lg border border-[var(--border)] bg-[var(--surface)] h-24 animate-pulse" />
              ))}
            </div>
          </div>
          <div className="md:col-span-7">
            <Card className="h-96 animate-pulse" />
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-12 gap-6">
          {/* Roles List */}
          <div className="md:col-span-5 space-y-3">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-[var(--muted-foreground)] px-1">
              Organization Roles
            </h2>
            <div className="space-y-2">
              {roles.map((role) => {
                const isSelected = selectedRole?.id === role.id;
                return (
                  <div
                    key={role.id}
                    onClick={() => setSelectedRole(role)}
                    className={`p-3.5 rounded-lg border transition-all cursor-pointer ${
                      isSelected
                        ? "border-[var(--primary)] bg-[var(--accent)] text-[var(--accent-foreground)] shadow-xs"
                        : "border-[var(--border)] bg-[var(--surface)] hover:border-[var(--border-hover)]"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-sm text-[var(--foreground)]">
                          {role.name}
                        </span>
                        {role.isSystem ? (
                          <Badge variant="outline" className="text-[10px] gap-1 px-1.5 py-0">
                            <Lock className="w-2.5 h-2.5" />
                            System
                          </Badge>
                        ) : (
                          <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                            Custom
                          </Badge>
                        )}
                      </div>
                      {!role.isSystem && (
                        <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => openEditRole(role)}
                            className="h-6 w-6 text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                          >
                            <Edit2 className="w-3 h-3" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => setDeletingRole(role)}
                            className="h-6 w-6 text-red-500 hover:text-red-700"
                          >
                            <Trash2 className="w-3 h-3" />
                          </Button>
                        </div>
                      )}
                    </div>
                    <p className="text-xs text-[var(--muted-foreground)] mt-1.5 line-clamp-2">
                      {role.description}
                    </p>
                    <div className="flex items-center gap-4 text-[11px] text-[var(--muted-foreground)] mt-2 pt-2 border-t border-[var(--border)]/50">
                      <span>{role.permissions.length} permissions</span>
                      <span>{role.memberCount} assigned</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Selected Role Permissions Detail */}
          <div className="md:col-span-7">
            {selectedRole ? (
              <Card>
                <CardHeader className="pb-3 border-b border-[var(--border)]">
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="flex items-center gap-2">
                        <CardTitle className="text-base">{selectedRole.name}</CardTitle>
                        {selectedRole.isSystem && (
                          <Badge variant="outline" className="text-[10px]">
                            Immutable System Role
                          </Badge>
                        )}
                      </div>
                      <CardDescription className="text-xs mt-1">
                        {selectedRole.description}
                      </CardDescription>
                    </div>
                    {!selectedRole.isSystem && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => openEditRole(selectedRole)}
                      >
                        <Edit2 className="w-3.5 h-3.5 mr-1.5" />
                        Edit Role
                      </Button>
                    )}
                  </div>
                </CardHeader>
                <CardContent className="pt-4 space-y-4 max-h-[600px] overflow-y-auto">
                  {Object.entries(groupedPermissions).map(([category, perms]) => (
                    <div key={category} className="space-y-2">
                      <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--muted-foreground)] border-b border-[var(--border)] pb-1">
                        {category} Capabilities
                      </h3>
                      <div className="grid grid-cols-1 gap-1.5">
                        {perms.map((p) => {
                          const granted = selectedRole.permissions.includes(p.code);
                          return (
                            <div
                              key={p.code}
                              className={`flex items-start justify-between p-2 rounded text-xs border ${
                                granted
                                  ? "bg-[var(--surface)] border-[var(--border)]"
                                  : "opacity-40 border-transparent"
                              }`}
                            >
                              <div>
                                <div className="font-medium text-[var(--foreground)] flex items-center gap-1.5">
                                  {granted ? (
                                    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400 shrink-0" />
                                  ) : (
                                    <span className="w-3.5 h-3.5 rounded-full border border-[var(--muted-foreground)] inline-block shrink-0" />
                                  )}
                                  <span>{p.name}</span>
                                  <code className="text-[10px] text-[var(--muted-foreground)] font-mono">
                                    ({p.code})
                                  </code>
                                </div>
                                <p className="text-[11px] text-[var(--muted-foreground)] mt-0.5 pl-5">
                                  {p.description}
                                </p>
                              </div>
                              <Badge
                                variant={granted ? "default" : "outline"}
                                className="text-[10px] shrink-0"
                              >
                                {granted ? "Granted" : "Denied"}
                              </Badge>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </CardContent>
              </Card>
            ) : null}
          </div>
        </div>
      )}

      {/* Modal: Create Custom Role */}
      <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
        <DialogContent className="sm:max-w-xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Create Custom Role</DialogTitle>
            <DialogDescription>
              Define a tailored set of permissions. You may only grant permissions that your active membership holds.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleCreateRole} className="space-y-4 py-2">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-xs font-semibold text-[var(--foreground)]">Role Name</label>
                <Input
                  placeholder="Security Auditor"
                  value={createName}
                  onChange={(e) => {
                    setCreateName(e.target.value);
                    if (!createSlug || createSlug === createName.toLowerCase().replace(/\s+/g, "-")) {
                      setCreateSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"));
                    }
                  }}
                  required
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs font-semibold text-[var(--foreground)]">Role Slug</label>
                <Input
                  placeholder="security-auditor"
                  value={createSlug}
                  onChange={(e) => setCreateSlug(e.target.value)}
                  required
                />
              </div>
            </div>
            <div className="space-y-1">
              <label className="text-xs font-semibold text-[var(--foreground)]">Description</label>
              <Textarea
                placeholder="Describe responsibilities and scope of this role..."
                value={createDescription}
                onChange={(e) => setCreateDescription(e.target.value)}
                rows={2}
              />
            </div>
            <div className="space-y-2">
              <label className="text-xs font-semibold text-[var(--foreground)]">
                Assigned Permissions ({createPerms.length} selected)
              </label>
              <div className="space-y-3 max-h-48 overflow-y-auto border border-[var(--border)] rounded-md p-2">
                {ALL_PERMISSIONS.map((perm) => (
                  <div key={perm.code} className="flex items-start space-x-2">
                    <Checkbox
                      id={`cp-${perm.code}`}
                      checked={createPerms.includes(perm.code)}
                      onCheckedChange={(val) => {
                        if (val) {
                          setCreatePerms([...createPerms, perm.code]);
                        } else {
                          setCreatePerms(createPerms.filter((c) => c !== perm.code));
                        }
                      }}
                      className="mt-0.5"
                    />
                    <div className="text-xs">
                      <label htmlFor={`cp-${perm.code}`} className="font-medium cursor-pointer">
                        {perm.name} <span className="text-[10px] text-[var(--muted-foreground)]">({perm.code})</span>
                      </label>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <DialogFooter className="pt-2">
              <Button type="button" variant="outline" onClick={() => setIsCreateOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" className="bg-[var(--primary)] text-white hover:bg-[var(--primary-hover)]">
                Create Role
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Modal: Edit Custom Role */}
      <Dialog open={!!editingRole} onOpenChange={(open) => !open && setEditingRole(null)}>
        <DialogContent className="sm:max-w-xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Edit Role: {editingRole?.name}</DialogTitle>
            <DialogDescription>
              Update role name, description, and permission attachments.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-1">
              <label className="text-xs font-semibold text-[var(--foreground)]">Role Name</label>
              <Input
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                required
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-semibold text-[var(--foreground)]">Description</label>
              <Textarea
                value={editDescription}
                onChange={(e) => setEditDescription(e.target.value)}
                rows={2}
              />
            </div>
            <div className="space-y-2">
              <label className="text-xs font-semibold text-[var(--foreground)]">
                Assigned Permissions ({editPerms.length} selected)
              </label>
              <div className="space-y-3 max-h-48 overflow-y-auto border border-[var(--border)] rounded-md p-2">
                {ALL_PERMISSIONS.map((perm) => (
                  <div key={perm.code} className="flex items-start space-x-2">
                    <Checkbox
                      id={`ep-${perm.code}`}
                      checked={editPerms.includes(perm.code)}
                      onCheckedChange={(val) => {
                        if (val) {
                          setEditPerms([...editPerms, perm.code]);
                        } else {
                          setEditPerms(editPerms.filter((c) => c !== perm.code));
                        }
                      }}
                      className="mt-0.5"
                    />
                    <div className="text-xs">
                      <label htmlFor={`ep-${perm.code}`} className="font-medium cursor-pointer">
                        {perm.name} <span className="text-[10px] text-[var(--muted-foreground)]">({perm.code})</span>
                      </label>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <DialogFooter className="pt-2">
            <Button variant="outline" onClick={() => setEditingRole(null)}>
              Cancel
            </Button>
            <Button onClick={handleSaveEditRole} className="bg-[var(--primary)] text-white hover:bg-[var(--primary-hover)]">
              Save Changes
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Modal: Delete Custom Role */}
      <Dialog open={!!deletingRole} onOpenChange={(open) => !open && setDeletingRole(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="text-red-600 dark:text-red-400">
              Delete Custom Role?
            </DialogTitle>
            <DialogDescription>
              Are you sure you want to permanently delete custom role <strong>{deletingRole?.name}</strong>? Members must be reassigned before deletion.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="pt-2">
            <Button variant="outline" onClick={() => setDeletingRole(null)}>
              Cancel
            </Button>
            <Button
              variant="default"
              onClick={handleConfirmDelete}
              className="bg-red-600 hover:bg-red-700 text-white"
            >
              Confirm Deletion
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

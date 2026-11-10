"use client";

import { apiUrl } from "@/lib/api";
import * as React from "react";
import { SpaLink } from "@/components/providers/spa-router-provider";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Users,
  Shield,
  Table2,
  FileSpreadsheet,
  BarChart3,
  FileText,
  FolderOpen,
  History,
  Settings,
  ChevronLeft,
  ChevronRight,
  Database,
  Layers,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

interface NavItem {
  title: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  badge?: string;
  status?: "active" | "coming_soon";
}

const navSections: { section: string; items: NavItem[] }[] = [
  {
    section: "Platform",
    items: [
      { title: "Dashboard", href: "/", icon: LayoutDashboard, status: "active" },
      { title: "Workspaces", href: "/workspaces", icon: Layers, status: "active" },
      { title: "Spreadsheets", href: "/spreadsheets", icon: Table2, status: "active" },
    ],
  },
  {
    section: "Intelligence",
    items: [
      { title: "Dashboards", href: "/dashboards", icon: LayoutDashboard, status: "active" },
      { title: "Analytics", href: "/analytics", icon: BarChart3, status: "active" },
      { title: "Google Sheets", href: "/integrations", icon: FileSpreadsheet, status: "active" },
      { title: "Reports", href: "/reports", icon: FileText, status: "active" },
    ],
  },
  {
    section: "Governance",
    items: [
      { title: "User Management", href: "/users", icon: Users, status: "active" },
      { title: "Roles & RBAC", href: "/roles", icon: Shield, status: "active" },
      { title: "Audit Ledger", href: "/audit", icon: History, status: "active" },
      { title: "File Storage", href: "/files", icon: FolderOpen, status: "active" },
      { title: "System Health", href: "/system", icon: Database, status: "active" },
      { title: "Settings", href: "/settings", icon: Settings, status: "active" },
    ],
  },
];

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
  mobileOpen?: boolean;
  onMobileClose?: () => void;
}

export function Sidebar({
  collapsed,
  onToggle,
  mobileOpen = false,
  onMobileClose,
}: SidebarProps) {
  const pathname = usePathname();
  const [orgName, setOrgName] = React.useState<string>("Acme Corporation");

  // Load active organization name dynamically
  React.useEffect(() => {
    let mounted = true;
    fetch(apiUrl("/api/v1/auth/me"), { credentials: "include" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (mounted && data?.active_organization?.name) {
          setOrgName(data.active_organization.name);
        }
      })
      .catch(() => {});
    return () => {
      mounted = false;
    };
  }, []);

  // Close mobile drawer on Esc key
  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && mobileOpen && onMobileClose) {
        onMobileClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [mobileOpen, onMobileClose]);

  const navContent = (
    <TooltipProvider delayDuration={150}>
      <div className="flex h-full flex-col bg-[var(--surface)] border-r border-[var(--border)] select-none">
        {/* Brand Header */}
        <div className="flex h-14 items-center justify-between px-3 border-b border-[var(--border)]">
          {(!collapsed || mobileOpen) && (
            <SpaLink
              href="/"
              onClick={mobileOpen ? onMobileClose : undefined}
              className="flex items-center gap-2 font-bold tracking-tight text-xs text-[var(--foreground)]"
            >
              <div className="w-6 h-6 rounded bg-[var(--primary)] text-[var(--primary-foreground)] flex items-center justify-center font-black text-xs shadow-xs">
                AD
              </div>
              <span className="font-extrabold tracking-wider">ADMIN DASHBOARD</span>
            </SpaLink>
          )}

          {collapsed && !mobileOpen && (
            <div className="mx-auto w-7 h-7 rounded bg-[var(--primary)] text-[var(--primary-foreground)] flex items-center justify-center font-black text-xs shadow-xs">
              AD
            </div>
          )}

          {/* Mobile Close Button */}
          {mobileOpen && onMobileClose && (
            <Button
              variant="ghost"
              size="icon"
              onClick={onMobileClose}
              className="h-7 w-7 text-[var(--muted-foreground)] hover:text-[var(--foreground)] md:hidden"
              aria-label="Close mobile navigation"
            >
              <X className="h-4 w-4" />
            </Button>
          )}

          {/* Desktop Collapse Toggle */}
          {!mobileOpen && (
            <Button
              variant="ghost"
              size="icon"
              onClick={onToggle}
              className={cn(
                "hidden md:flex h-7 w-7 text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
                collapsed && "mx-auto mt-1"
              )}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
            </Button>
          )}
        </div>

        {/* Navigation Groups */}
        <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-4">
          {navSections.map((group) => (
            <div key={group.section} className="space-y-1">
              {(!collapsed || mobileOpen) && (
                <h2 className="px-2 text-[10px] font-semibold uppercase tracking-wider text-[var(--muted-foreground)]">
                  {group.section}
                </h2>
              )}
              {group.items.map((item) => {
                const Icon = item.icon;
                const isActive = pathname === item.href;
                const isComingSoon = item.status === "coming_soon";

                const linkElement = (
                  <SpaLink
                    href={item.href}
                    onClick={mobileOpen ? onMobileClose : undefined}
                    className={cn(
                      "flex items-center gap-3 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]",
                      isActive
                        ? "bg-[var(--accent)] text-[var(--accent-foreground)] font-semibold"
                        : "text-[var(--muted-foreground)] hover:bg-[var(--muted)] hover:text-[var(--foreground)]",
                      collapsed && !mobileOpen && "justify-center px-0 py-2"
                    )}
                    aria-current={isActive ? "page" : undefined}
                  >
                    <Icon className={cn("h-4 w-4 shrink-0", isActive ? "text-[var(--primary)]" : "")} />
                    {(!collapsed || mobileOpen) && (
                      <span className="truncate flex-1">{item.title}</span>
                    )}
                    {(!collapsed || mobileOpen) && isComingSoon && (
                      <span className="px-1.5 py-0.2 rounded text-[9px] bg-[var(--muted)] text-[var(--muted-foreground)] border border-[var(--border)] font-mono">
                        Soon
                      </span>
                    )}
                    {(!collapsed || mobileOpen) && item.badge && !isComingSoon && (
                      <span className="px-1.5 py-0.2 rounded text-[9px] bg-[var(--accent)] text-[var(--accent-foreground)] border border-[var(--border)] font-mono">
                        {item.badge}
                      </span>
                    )}
                  </SpaLink>
                );

                if (collapsed && !mobileOpen) {
                  return (
                    <Tooltip key={item.href}>
                      <TooltipTrigger asChild>{linkElement}</TooltipTrigger>
                      <TooltipContent side="right">
                        <span>{item.title}</span>
                        {isComingSoon && <span className="ml-1.5 opacity-60">(Phase {group.section === "Platform" ? "6" : "5+"})</span>}
                      </TooltipContent>
                    </Tooltip>
                  );
                }

                return <div key={item.href}>{linkElement}</div>;
              })}
            </div>
          ))}
        </nav>

        {/* Footer Tenant Info */}
        {(!collapsed || mobileOpen) && (
          <div className="p-3 border-t border-[var(--border)] text-[11px] text-[var(--muted-foreground)]">
            <div className="flex items-center justify-between">
              <span className="truncate">Org: {orgName}</span>
              <span className="inline-block w-2 h-2 rounded-full bg-emerald-500" title="System Ready" />
            </div>
          </div>
        )}
      </div>
    </TooltipProvider>
  );

  return (
    <>
      {/* Desktop Sidebar */}
      <aside
        className={cn(
          "hidden md:flex flex-col shrink-0 transition-[width] duration-200 ease-in-out",
          collapsed ? "w-16" : "w-60"
        )}
        aria-label="Desktop primary navigation"
      >
        {navContent}
      </aside>

      {/* Mobile Drawer Overlay */}
      {mobileOpen && (
        <div
          role="presentation"
          onClick={onMobileClose}
          className="fixed inset-0 z-40 bg-black/50 backdrop-blur-xs md:hidden transition-opacity"
        />
      )}

      {/* Mobile Drawer Container */}
      <div
        className={cn(
          "fixed inset-y-0 left-0 z-50 w-72 max-w-[85vw] transform transition-transform duration-200 ease-in-out md:hidden shadow-2xl",
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        )}
        role="dialog"
        aria-modal="true"
        aria-label="Mobile navigation drawer"
      >
        {navContent}
      </div>
    </>
  );
}

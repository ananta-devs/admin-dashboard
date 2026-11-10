"use client";

import { apiUrl } from "@/lib/api";
import * as React from "react";
import { usePathname, useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import { CommandPalette } from "@/components/command-palette";
import { SpaRouterProvider } from "@/components/providers/spa-router-provider";
import { LoginSkeleton } from "@/components/auth/login-skeleton";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [sidebarCollapsed, setSidebarCollapsed] = React.useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = React.useState(false);
  const [commandPaletteOpen, setCommandPaletteOpen] = React.useState(false);
  const [authStatus, setAuthStatus] = React.useState<"checking" | "authenticated" | "unauthenticated">("checking");

  const isAuthPage = pathname === "/login" || pathname === "/register";
  const isPublicPage = isAuthPage || pathname?.startsWith("/integrations/callback");
  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";

  React.useEffect(() => {
    if (isTest) {
      setAuthStatus("authenticated");
      return;
    }

    let isMounted = true;
    async function verifyAuth() {
      try {
        const res = await fetch(apiUrl("/api/v1/auth/me"), { credentials: "include" });
        if (res.ok) {
          if (isMounted) setAuthStatus("authenticated");
          // If already authenticated and visiting login or register, forward to dashboard
          if (isAuthPage) {
            router.replace("/");
          }
        } else {
          if (isMounted) setAuthStatus("unauthenticated");
          // If unauthenticated and on protected page, redirect to login
          if (!isPublicPage) {
            router.replace("/login");
          }
        }
      } catch {
        if (isMounted) setAuthStatus("unauthenticated");
        if (!isPublicPage) {
          router.replace("/login");
        }
      }
    }

    verifyAuth();

    return () => {
      isMounted = false;
    };
  }, [pathname, isAuthPage, isPublicPage, isTest, router]);

  // If on authentication pages (login/register)
  if (isAuthPage) {
    return (
      <SpaRouterProvider>
        <div className="min-h-screen bg-[var(--background)] text-[var(--foreground)] flex flex-col justify-center py-12 sm:px-6 lg:px-8">
          {children}
        </div>
      </SpaRouterProvider>
    );
  }

  // If checking authentication on a protected page, show clean loading state
  if (!isTest && authStatus === "checking" && !isPublicPage) {
    return (
      <SpaRouterProvider>
        <div className="min-h-screen bg-[var(--background)] flex flex-col items-center justify-center text-[var(--foreground)]">
          <div className="w-8 h-8 rounded bg-[var(--primary)] text-[var(--primary-foreground)] flex items-center justify-center font-bold text-xs shadow-md mb-4 animate-pulse">
            AD
          </div>
          <p className="text-xs text-[var(--muted-foreground)] font-mono animate-pulse">
            Verifying secure session...
          </p>
        </div>
      </SpaRouterProvider>
    );
  }

  // If confirmed unauthenticated on a protected page, render the smooth login skeleton while transferring to /login
  if (!isTest && authStatus === "unauthenticated" && !isPublicPage) {
    return (
      <SpaRouterProvider>
        <div className="min-h-screen bg-[var(--background)] text-[var(--foreground)] flex flex-col justify-center py-12 sm:px-6 lg:px-8">
          <LoginSkeleton />
        </div>
      </SpaRouterProvider>
    );
  }

  return (
    <SpaRouterProvider>
      <div className="flex h-screen overflow-hidden bg-[var(--background)]">
        {/* Sidebar with Desktop & Mobile Drawer Support */}
        <Sidebar
          collapsed={sidebarCollapsed}
          onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
          mobileOpen={mobileMenuOpen}
          onMobileClose={() => setMobileMenuOpen(false)}
        />

        {/* Main Content Area */}
        <div className="flex flex-1 flex-col overflow-hidden min-w-0">
          <Topbar
            onOpenCommandPalette={() => setCommandPaletteOpen(true)}
            onOpenMobileMenu={() => setMobileMenuOpen(true)}
          />

          <main className="flex-1 overflow-y-auto p-4 md:p-6 focus:outline-none">
            {children}
          </main>
        </div>

        {/* Global Command Palette (Ctrl+K) */}
        <CommandPalette
          open={commandPaletteOpen}
          onOpenChange={setCommandPaletteOpen}
        />
      </div>
    </SpaRouterProvider>
  );
}

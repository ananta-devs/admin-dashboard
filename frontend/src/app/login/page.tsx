"use client";

import { apiUrl } from "@/lib/api";
import * as React from "react";
import { useSpaRouter } from "@/components/providers/spa-router-provider";
import {
  LogIn,
  Lock,
  Mail,
  Eye,
  EyeOff,
  AlertTriangle,
  CheckCircle2,
  Layers,
} from "lucide-react";
import { Button } from "@/components/ui/button";
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

export default function LoginPage() {
  const router = useSpaRouter();

  // Form states
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [rememberMe, setRememberMe] = React.useState(true);
  const [loading, setLoading] = React.useState(false);
  const [errorMessage, setErrorMessage] = React.useState<string | null>(null);

  // Forgot password modal
  const [forgotOpen, setForgotOpen] = React.useState(false);
  const [forgotEmail, setForgotEmail] = React.useState("");
  const [forgotSubmitting, setForgotSubmitting] = React.useState(false);
  const [forgotSuccess, setForgotSuccess] = React.useState(false);

  // Handle Login
  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);

    if (!email.trim() || !password) {
      setErrorMessage("Please enter both email and password.");
      return;
    }

    try {
      setLoading(true);
      const res = await fetch(apiUrl("/api/v1/auth/login"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          email: email.trim().toLowerCase(),
          password,
        }),
      });

      if (res.ok) {
        // Successfully authenticated - cookie set by backend
        router.push("/");
      } else {
        const data = await res.json().catch(() => null);
        const msg = data?.error?.message || data?.detail || "Invalid email or password. Please try again.";
        setErrorMessage(msg);
      }
    } catch {
      setErrorMessage("Network error connecting to authentication server. Please check your connection.");
    } finally {
      setLoading(false);
    }
  };

  // Handle Forgot Password
  const handleForgotPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!forgotEmail.trim()) return;

    try {
      setForgotSubmitting(true);
      const res = await fetch(apiUrl("/api/v1/auth/forgot-password"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: forgotEmail.trim().toLowerCase() }),
      });

      if (res.ok) {
        setForgotSuccess(true);
      } else {
        const data = await res.json().catch(() => null);
        setErrorMessage(data?.detail || "Failed to dispatch password reset link.");
      }
    } catch {
      setErrorMessage("Network error requesting password reset.");
    } finally {
      setForgotSubmitting(false);
    }
  };

  return (
    <div className="w-full max-w-md mx-auto">
      {/* Brand Header */}
      <div className="text-center mb-6">
        <div className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-[var(--primary)] text-[var(--primary-foreground)] shadow-md mb-3">
          <Layers className="w-6 h-6" />
        </div>
        <h1 className="text-2xl font-bold tracking-tight text-[var(--foreground)]">
          ADMIN DASHBOARD
        </h1>
        <p className="text-xs text-[var(--muted-foreground)] mt-1">
          Sign in to your enterprise data intelligence platform
        </p>
      </div>

      {/* Main Login Card */}
      <Card className="border-[var(--border)] bg-[var(--surface)] shadow-xl">
        <CardHeader className="pb-4">
          <CardTitle className="text-base font-semibold">Sign In</CardTitle>
          <CardDescription className="text-xs">
            Enter your organization credentials to access workspaces and dashboards.
          </CardDescription>
        </CardHeader>
        <form onSubmit={handleLogin}>
          <CardContent className="space-y-4 text-xs">
            {errorMessage && (
              <div className="p-3 rounded-lg border border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400 text-xs flex items-start gap-2 animate-in fade-in-50">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                <span className="font-medium leading-relaxed">{errorMessage}</span>
              </div>
            )}

            {/* Email Field */}
            <div className="space-y-1.5">
              <label htmlFor="email" className="text-xs font-medium text-[var(--foreground)]">
                Enterprise Email
              </label>
              <div className="relative">
                <Input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="name@organization.com"
                  required
                  autoFocus
                  className="text-xs pl-9"
                />
                <Mail className="w-4 h-4 text-[var(--muted-foreground)] absolute left-3 top-2.5" />
              </div>
            </div>

            {/* Password Field */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label htmlFor="password" className="text-xs font-medium text-[var(--foreground)]">
                  Password
                </label>
                <button
                  type="button"
                  onClick={() => {
                    setForgotEmail(email);
                    setForgotSuccess(false);
                    setForgotOpen(true);
                  }}
                  className="text-[11px] text-[var(--primary)] hover:underline cursor-pointer"
                >
                  Forgot password?
                </button>
              </div>
              <div className="relative">
                <Input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••••••"
                  required
                  className="text-xs pl-9 pr-9"
                />
                <Lock className="w-4 h-4 text-[var(--muted-foreground)] absolute left-3 top-2.5" />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-2.5 top-2.5 text-[var(--muted-foreground)] hover:text-[var(--foreground)] cursor-pointer"
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            {/* Remember Me */}
            <div className="flex items-center justify-between pt-1">
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(e) => setRememberMe(e.target.checked)}
                  className="rounded border-[var(--input)] text-[var(--primary)] focus:ring-[var(--ring)]"
                />
                <span className="text-[11px] text-[var(--muted-foreground)]">Remember session</span>
              </label>
            </div>
          </CardContent>

          <CardFooter className="flex flex-col space-y-3 pt-2">
            <Button
              type="submit"
              disabled={loading}
              className="w-full bg-[var(--primary)] hover:bg-[var(--primary-hover)] text-[var(--primary-foreground)] text-xs h-9 font-medium shadow-sm cursor-pointer"
            >
              {loading ? (
                <span className="flex items-center gap-2">
                  <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Authenticating...
                </span>
              ) : (
                <span className="flex items-center gap-1.5">
                  <LogIn className="w-4 h-4" />
                  Sign In
                </span>
              )}
            </Button>
          </CardFooter>
        </form>
      </Card>

      {/* Forgot Password Dialog */}
      <Dialog open={forgotOpen} onOpenChange={setForgotOpen}>
        <DialogContent className="max-w-md border-[var(--border)] bg-[var(--surface)] text-[var(--foreground)]">
          <DialogHeader>
            <DialogTitle className="text-sm font-semibold">Reset Password</DialogTitle>
            <DialogDescription className="text-xs text-[var(--muted-foreground)]">
              Enter your verified email address to receive password reset instructions.
            </DialogDescription>
          </DialogHeader>

          {forgotSuccess ? (
            <div className="p-4 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-600 dark:text-emerald-400 text-xs flex items-center gap-2.5 my-2">
              <CheckCircle2 className="w-5 h-5 shrink-0" />
              <div>
                <p className="font-semibold">Reset Link Dispatched</p>
                <p className="text-[11px] mt-0.5">
                  If an account matches that address, instructions have been sent.
                </p>
              </div>
            </div>
          ) : (
            <form onSubmit={handleForgotPassword} className="space-y-4 py-2">
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-[var(--foreground)]">Email Address</label>
                <Input
                  type="email"
                  value={forgotEmail}
                  onChange={(e) => setForgotEmail(e.target.value)}
                  placeholder="name@organization.com"
                  required
                  className="text-xs"
                />
              </div>
              <DialogFooter className="gap-2 sm:gap-0 pt-2">
                <Button type="button" variant="outline" size="sm" onClick={() => setForgotOpen(false)} className="text-xs">
                  Cancel
                </Button>
                <Button
                  type="submit"
                  size="sm"
                  disabled={forgotSubmitting || !forgotEmail.trim()}
                  className="bg-[var(--primary)] hover:bg-[var(--primary-hover)] text-[var(--primary-foreground)] text-xs"
                >
                  {forgotSubmitting ? "Sending..." : "Send Reset Link"}
                </Button>
              </DialogFooter>
            </form>
          )}

          {forgotSuccess && (
            <DialogFooter className="pt-2">
              <Button size="sm" onClick={() => setForgotOpen(false)} className="text-xs">
                Back to Sign In
              </Button>
            </DialogFooter>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

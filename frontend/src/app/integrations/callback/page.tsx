"use client";

import { apiUrl } from "@/lib/api";
import * as React from "react";
import { useSearchParams } from "next/navigation";
import { useSpaRouter } from "@/components/providers/spa-router-provider";
import { CheckCircle2, AlertCircle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";

function GoogleOAuthCallbackInner() {
  const router = useSpaRouter();
  const searchParams = useSearchParams();
  const [status, setStatus] = React.useState<"processing" | "success" | "error">("processing");
  const [errorMessage, setErrorMessage] = React.useState<string>("");

  React.useEffect(() => {
    async function exchangeOAuth() {
      const code = searchParams.get("code");
      const state = searchParams.get("state");

      if (!code || !state) {
        setStatus("error");
        setErrorMessage("Missing OAuth authorization code or state token in query parameters.");
        return;
      }

      try {
        const res = await fetch(apiUrl("/api/v1/integrations/google/callback"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code, state }),
          credentials: "include",
        });

        if (res.ok) {
          setStatus("success");
          setTimeout(() => {
            router.push("/integrations");
          }, 1500);
        } else {
          const errData = await res.json();
          setStatus("error");
          setErrorMessage(errData?.error?.message || "Failed to complete Google OAuth exchange.");
        }
      } catch (err: unknown) {
        setStatus("error");
        setErrorMessage((err as Error)?.message || "An unexpected error occurred during authorization.");
      }
    }

    exchangeOAuth();
  }, [searchParams, router]);

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] p-6 text-center">
      {status === "processing" && (
        <div className="flex flex-col items-center gap-4">
          <RefreshCw className="h-10 w-10 text-primary animate-spin" />
          <h2 className="text-xl font-bold">Connecting Google Sheets...</h2>
          <p className="text-sm text-muted-foreground">
            Exchanging authorization grant for AES-256-GCM encrypted access tokens.
          </p>
        </div>
      )}

      {status === "success" && (
        <div className="flex flex-col items-center gap-4">
          <CheckCircle2 className="h-12 w-12 text-emerald-500 animate-bounce" />
          <h2 className="text-xl font-bold text-emerald-600">Successfully Connected!</h2>
          <p className="text-sm text-muted-foreground">Redirecting to integrations management...</p>
        </div>
      )}

      {status === "error" && (
        <div className="flex flex-col items-center gap-4 max-w-md">
          <AlertCircle className="h-12 w-12 text-rose-500" />
          <h2 className="text-xl font-bold text-rose-600">Connection Failed</h2>
          <p className="text-sm text-muted-foreground">{errorMessage}</p>
          <Button onClick={() => router.push("/integrations")} className="mt-2">
            Back to Integrations
          </Button>
        </div>
      )}
    </div>
  );
}

export default function GoogleOAuthCallbackPage() {
  return (
    <React.Suspense
      fallback={
        <div className="flex flex-col items-center justify-center min-h-[60vh] p-6 text-center">
          <RefreshCw className="h-10 w-10 text-primary animate-spin" />
          <h2 className="text-xl font-bold mt-4">Loading authorization...</h2>
        </div>
      }
    >
      <GoogleOAuthCallbackInner />
    </React.Suspense>
  );
}

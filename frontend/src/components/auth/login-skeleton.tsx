import * as React from "react";
import { Card, CardHeader, CardContent, CardFooter } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export function LoginSkeleton() {
  return (
    <div className="w-full max-w-md mx-auto animate-in fade-in-50 duration-200">
      {/* Brand Header Skeleton */}
      <div className="text-center mb-6 flex flex-col items-center">
        <Skeleton className="w-12 h-12 rounded-xl mb-3" />
        <Skeleton className="h-7 w-48 mb-2" />
        <Skeleton className="h-4 w-64" />
      </div>

      {/* Login Card Skeleton */}
      <Card className="border-[var(--border)] bg-[var(--surface)] shadow-xl">
        <CardHeader className="pb-4 space-y-2">
          <Skeleton className="h-5 w-20" />
          <Skeleton className="h-3.5 w-full max-w-[280px]" />
        </CardHeader>

        <CardContent className="space-y-4">
          {/* Email input skeleton */}
          <div className="space-y-1.5">
            <Skeleton className="h-3.5 w-24" />
            <Skeleton className="h-9 w-full rounded-md" />
          </div>

          {/* Password input skeleton */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Skeleton className="h-3.5 w-16" />
              <Skeleton className="h-3 w-24" />
            </div>
            <Skeleton className="h-9 w-full rounded-md" />
          </div>

          {/* Remember session checkbox skeleton */}
          <div className="flex items-center gap-2 pt-1">
            <Skeleton className="h-3.5 w-3.5 rounded" />
            <Skeleton className="h-3 w-28" />
          </div>
        </CardContent>

        <CardFooter className="flex flex-col space-y-3 pt-2">
          {/* Sign In Button skeleton */}
          <Skeleton className="h-9 w-full rounded-md" />

          {/* Footer link skeleton */}
          <div className="flex items-center justify-center gap-1.5 pt-1">
            <Skeleton className="h-3 w-40" />
            <Skeleton className="h-3 w-20" />
          </div>
        </CardFooter>
      </Card>
    </div>
  );
}


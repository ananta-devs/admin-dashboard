"use client";

import { useSpaRouter } from "@/components/providers/spa-router-provider";
import * as React from "react";
import { BackupRecoveryCard } from "@/components/admin/BackupRecoveryCard";

export default function BackupsSettingsPage() {
  const _router = useSpaRouter();
  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Automated Backup & Disaster Recovery</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Monitor continuous physical streaming, Point-in-Time Recovery (PITR) RPO/RTO metrics, and isolated verification sandboxes.
        </p>
      </div>

      <BackupRecoveryCard />
    </div>
  );
}


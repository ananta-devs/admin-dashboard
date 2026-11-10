"use client";

import { useSpaRouter } from "@/components/providers/spa-router-provider";
import { apiUrl } from "@/lib/api";
import * as React from "react";
import {
  FolderOpen,
  Upload,
  Search,
  Download,
  Trash2,
  Eye,
  FileText,
  FileSpreadsheet,
  Image as ImageIcon,
  HardDrive,
  CheckCircle2,
  XCircle,
  AlertTriangle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";

interface StoredFileItem {
  id: string;
  organization_id: string;
  workspace_id?: string | null;
  uploaded_by_user_id?: string | null;
  filename: string;
  content_type: string;
  size_bytes: number;
  storage_path: string;
  checksum_sha256: string;
  folder: string;
  created_at: string;
  updated_at: string;
}

interface StorageQuota {
  organization_id: string;
  usage_bytes: number;
  quota_bytes: number;
  available_bytes: number;
  file_count: number;
  utilization_percent: number;
}

export default function FileStoragePage() {
  const _router = useSpaRouter();
  const [files, setFiles] = React.useState<StoredFileItem[]>([]);
  const [quota, setQuota] = React.useState<StorageQuota | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [search, setSearch] = React.useState("");
  const [typeFilter, setTypeFilter] = React.useState("all");
  const [csrfToken, setCsrfToken] = React.useState("");
  const [notice, setNotice] = React.useState<{ message: string; type: "success" | "error" } | null>(null);

  // Upload modal states
  const [isUploadOpen, setIsUploadOpen] = React.useState(false);
  const [uploadFile, setUploadFile] = React.useState<File | null>(null);
  const [uploadFolder, setUploadFolder] = React.useState("/");
  const [uploading, setUploading] = React.useState(false);

  // Preview modal states
  const [previewFile, setPreviewFile] = React.useState<StoredFileItem | null>(null);
  const [previewContent, setPreviewContent] = React.useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = React.useState(false);

  // Delete modal states
  const [deletingFile, setDeletingFile] = React.useState<StoredFileItem | null>(null);
  const [deleting, setDeleting] = React.useState(false);

  const showNotice = React.useCallback((message: string, type: "success" | "error") => {
    setNotice({ message, type });
    setTimeout(() => setNotice(null), 6000);
  }, []);

  const fetchCsrfToken = React.useCallback(async () => {
    try {
      const res = await fetch(apiUrl("/api/v1/auth/csrf-token"), { method: "POST", credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        if (data?.csrf_token) setCsrfToken(data.csrf_token);
      }
    } catch {
      // Fallback
    }
  }, []);

  const loadQuota = React.useCallback(async () => {
    try {
      const res = await fetch(apiUrl("/api/v1/files/quota/usage"), { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        setQuota(data);
      }
    } catch {
      // Fallback
    }
  }, []);

  const loadFiles = React.useCallback(async (query = "") => {
    try {
      setLoading(true);
      const url = query.trim()
        ? `/api/v1/files?search=${encodeURIComponent(query.trim())}`
        : "/api/v1/files";
      const res = await fetch(url, { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        setFiles(Array.isArray(data) ? data : []);
      } else {
        const err = await res.json().catch(() => ({}));
        showNotice(err?.error?.message || "Failed to load files.", "error");
      }
    } catch {
      showNotice("Network error loading files.", "error");
    } finally {
      setLoading(false);
    }
  }, [showNotice]);

  React.useEffect(() => {
    fetchCsrfToken();
    loadFiles();
    loadQuota();
  }, [fetchCsrfToken, loadFiles, loadQuota]);

  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setSearch(val);
    loadFiles(val);
  };

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      const selected = e.target.files[0];
      if (selected.size > 25 * 1024 * 1024) {
        showNotice("File size exceeds 25 MB limit.", "error");
        return;
      }
      setUploadFile(selected);
    }
  };

  // Convert File to Base64
  const fileToBase64 = (file: File): Promise<string> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.readAsDataURL(file);
      reader.onload = () => {
        const result = reader.result as string;
        const base64Data = result.split(",")[1];
        resolve(base64Data);
      };
      reader.onerror = (error) => reject(error);
    });
  };

  // Submit Upload
  const handleUploadSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!uploadFile) return;

    try {
      setUploading(true);
      const b64 = await fileToBase64(uploadFile);

      const payload = {
        filename: uploadFile.name,
        folder: uploadFolder.trim() || "/",
        content_base64: b64,
        content_type: uploadFile.type || "application/octet-stream",
      };

      const res = await fetch(apiUrl("/api/v1/files/upload"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        credentials: "include",
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showNotice(err?.error?.message || err?.detail || "Upload rejected.", "error");
        return;
      }

      showNotice(`File "${uploadFile.name}" uploaded successfully.`, "success");
      setIsUploadOpen(false);
      setUploadFile(null);
      setUploadFolder("/");
      await loadFiles(search);
      await loadQuota();
    } catch {
      showNotice("Network error during file upload.", "error");
    } finally {
      setUploading(false);
    }
  };

  // Delete File
  const handleDeleteFile = async () => {
    if (!deletingFile) return;

    try {
      setDeleting(true);
      const res = await fetch(apiUrl(`/api/v1/files/${deletingFile.id}`), {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrfToken },
        credentials: "include",
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showNotice(err?.error?.message || "Failed to delete file.", "error");
        return;
      }

      showNotice(`File "${deletingFile.filename}" deleted.`, "success");
      setDeletingFile(null);
      await loadFiles(search);
      await loadQuota();
    } catch {
      showNotice("Network error deleting file.", "error");
    } finally {
      setDeleting(false);
    }
  };

  // Open Preview
  const handleOpenPreview = async (file: StoredFileItem) => {
    setPreviewFile(file);
    setPreviewContent(null);
    setPreviewLoading(true);

    try {
      const res = await fetch(apiUrl(`/api/v1/files/${file.id}/preview`), { credentials: "include" });
      if (res.ok) {
        if (file.filename.endsWith(".csv") || file.filename.endsWith(".txt") || file.filename.endsWith(".json")) {
          const text = await res.text();
          setPreviewContent(text.substring(0, 10000));
        }
      }
    } catch {
      // Fallback
    } finally {
      setPreviewLoading(false);
    }
  };

  const getFileIcon = (filename: string) => {
    const ext = filename.split(".").pop()?.toLowerCase();
    if (ext === "csv" || ext === "xlsx" || ext === "xls" || ext === "parquet") {
      return <FileSpreadsheet className="h-5 w-5 text-emerald-500" />;
    }
    if (ext === "png" || ext === "jpg" || ext === "jpeg") {
      return <ImageIcon className="h-5 w-5 text-sky-500" />;
    }
    return <FileText className="h-5 w-5 text-violet-500" />;
  };

  const filteredFiles = files.filter((f) => {
    if (typeFilter === "all") return true;
    const ext = f.filename.split(".").pop()?.toLowerCase() || "";
    if (typeFilter === "csv") return ext === "csv" || ext === "parquet";
    if (typeFilter === "spreadsheet") return ext === "xlsx" || ext === "xls";
    if (typeFilter === "document") return ext === "pdf" || ext === "txt" || ext === "json";
    if (typeFilter === "image") return ext === "png" || ext === "jpg" || ext === "jpeg";
    return true;
  });

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
      {/* Notice Banner */}
      {notice && (
        <div
          role="alert"
          className={`flex items-center gap-3 px-4 py-3 rounded-lg border text-sm font-medium transition-all ${
            notice.type === "success"
              ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400"
              : "bg-rose-500/10 border-rose-500/30 text-rose-600 dark:text-rose-400"
          }`}
        >
          {notice.type === "success" ? <CheckCircle2 className="h-5 w-5 shrink-0" /> : <XCircle className="h-5 w-5 shrink-0" />}
          <span>{notice.message}</span>
        </div>
      )}

      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold tracking-tight text-[var(--foreground)]">File Storage</h1>
            <Badge variant="outline" className="border-[var(--border)] text-[var(--muted-foreground)]">
              {files.length} Files
            </Badge>
          </div>
          <p className="text-sm text-[var(--muted-foreground)]">
            Tenant-isolated dataset repository, verified checksum integrity, and automated quota enforcement.
          </p>
        </div>
        <Button onClick={() => setIsUploadOpen(true)} className="gap-2 shrink-0">
          <Upload className="h-4 w-4" />
          Upload File
        </Button>
      </div>

      {/* Authoritative Quota Progress Banner */}
      {quota && (
        <div className="p-4 rounded-xl border border-[var(--border)] bg-[var(--surface)] space-y-2">
          <div className="flex items-center justify-between text-xs font-semibold text-[var(--foreground)]">
            <div className="flex items-center gap-2">
              <HardDrive className="h-4 w-4 text-[var(--primary)]" />
              <span>Storage Quota Utilization</span>
            </div>
            <span>
              {formatBytes(quota.usage_bytes)} of {formatBytes(quota.quota_bytes)} used ({quota.utilization_percent}%)
            </span>
          </div>
          <div className="h-2 w-full rounded-full bg-[var(--muted)] overflow-hidden">
            <div
              className={`h-full transition-all rounded-full ${
                quota.utilization_percent > 85 ? "bg-rose-500" : quota.utilization_percent > 60 ? "bg-amber-500" : "bg-[var(--primary)]"
              }`}
              style={{ width: `${Math.min(100, quota.utilization_percent)}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-[11px] text-[var(--muted-foreground)] pt-0.5">
            <span>{quota.file_count} active files stored</span>
            <span>{formatBytes(quota.available_bytes)} remaining available</span>
          </div>
        </div>
      )}

      {/* Toolbar / Search & Filter */}
      <div className="flex flex-col sm:flex-row items-center gap-3">
        <div className="relative flex-1 max-w-md w-full">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--muted-foreground)]" />
          <Input
            value={search}
            onChange={handleSearchChange}
            placeholder="Search files by name..."
            className="pl-9 bg-[var(--surface)]"
          />
        </div>

        <div className="flex items-center gap-2 w-full sm:w-auto">
          <label htmlFor="file-type-filter" className="text-xs font-medium text-[var(--muted-foreground)] whitespace-nowrap">
            Type:
          </label>
          <select
            id="file-type-filter"
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="h-9 px-3 rounded-md border border-[var(--border)] bg-[var(--surface)] text-xs text-[var(--foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--ring)]"
          >
            <option value="all">All File Types</option>
            <option value="csv">Datasets (CSV / Parquet)</option>
            <option value="spreadsheet">Spreadsheets (XLSX / XLS)</option>
            <option value="document">Documents (PDF / TXT / JSON)</option>
            <option value="image">Images (PNG / JPG)</option>
          </select>
        </div>
      </div>

      {/* Files Table / List */}
      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-14 w-full rounded-xl" />
          ))}
        </div>
      ) : filteredFiles.length === 0 ? (
        <div className="flex flex-col items-center justify-center p-12 text-center rounded-xl border border-dashed border-[var(--border)] bg-[var(--surface)] space-y-3">
          <div className="p-3 rounded-full bg-[var(--muted)] text-[var(--muted-foreground)]">
            <FolderOpen className="h-8 w-8" />
          </div>
          <h3 className="font-semibold text-base text-[var(--foreground)]">No files stored</h3>
          <p className="text-xs text-[var(--muted-foreground)] max-w-sm">
            {search ? "No files match your search." : "Upload CSV datasets, Excel spreadsheets, or documents to store them securely."}
          </p>
          {!search && (
            <Button onClick={() => setIsUploadOpen(true)} size="sm" className="mt-2 gap-2">
              <Upload className="h-3.5 w-3.5" />
              Upload First File
            </Button>
          )}
        </div>
      ) : (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-[var(--muted)]/50 border-b border-[var(--border)] text-[var(--muted-foreground)] font-semibold uppercase tracking-wider text-[10px]">
                <tr>
                  <th className="py-3 px-4">Filename</th>
                  <th className="py-3 px-4">Folder</th>
                  <th className="py-3 px-4">Size</th>
                  <th className="py-3 px-4">Uploaded</th>
                  <th className="py-3 px-4">SHA-256 Checksum</th>
                  <th className="py-3 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border)]">
                {filteredFiles.map((file) => (
                  <tr key={file.id} className="hover:bg-[var(--muted)]/30 transition-colors">
                    <td className="py-3 px-4">
                      <div className="flex items-center gap-2.5">
                        {getFileIcon(file.filename)}
                        <span className="font-semibold text-[var(--foreground)] truncate max-w-[220px]" title={file.filename}>
                          {file.filename}
                        </span>
                      </div>
                    </td>
                    <td className="py-3 px-4 font-mono text-[11px] text-[var(--muted-foreground)]">
                      {file.folder}
                    </td>
                    <td className="py-3 px-4 font-mono text-[11px] text-[var(--foreground)]">
                      {formatBytes(file.size_bytes)}
                    </td>
                    <td className="py-3 px-4 text-[var(--muted-foreground)]">
                      {new Date(file.created_at).toLocaleDateString()}
                    </td>
                    <td className="py-3 px-4">
                      <Badge variant="outline" className="font-mono text-[10px] px-1.5 py-0 text-[var(--muted-foreground)]" title={file.checksum_sha256}>
                        {file.checksum_sha256.substring(0, 10)}...
                      </Badge>
                    </td>
                    <td className="py-3 px-4 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          size="icon"
                          variant="ghost"
                          onClick={() => handleOpenPreview(file)}
                          className="h-7 w-7 text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                          title="Preview File"
                          aria-label={`Preview ${file.filename}`}
                        >
                          <Eye className="h-3.5 w-3.5" />
                        </Button>
                        <a
                          href={`/api/v1/files/${file.id}/download`}
                          download={file.filename}
                          className="inline-flex items-center justify-center h-7 w-7 rounded-md text-[var(--muted-foreground)] hover:text-[var(--foreground)] hover:bg-[var(--muted)] transition-colors"
                          title="Download File"
                          aria-label={`Download ${file.filename}`}
                        >
                          <Download className="h-3.5 w-3.5" />
                        </a>
                        <Button
                          size="icon"
                          variant="ghost"
                          onClick={() => setDeletingFile(file)}
                          className="h-7 w-7 text-rose-500 hover:text-rose-600 hover:bg-rose-500/10"
                          title="Delete File"
                          aria-label={`Delete ${file.filename}`}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Upload File Modal */}
      <Dialog open={isUploadOpen} onOpenChange={setIsUploadOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Upload File</DialogTitle>
            <DialogDescription>
              Upload datasets, documentation, or analytical files (Max 25 MB).
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleUploadSubmit} className="space-y-4 py-2">
            <div className="space-y-1.5">
              <label htmlFor="file-input" className="text-xs font-medium text-[var(--foreground)]">
                Select File
              </label>
              <Input
                id="file-input"
                type="file"
                onChange={handleFileChange}
                required
                className="cursor-pointer file:cursor-pointer file:font-semibold"
              />
              <p className="text-[11px] text-[var(--muted-foreground)]">
                Allowed formats: CSV, Parquet, XLSX, XLS, PDF, PNG, JPG, JSON, TXT.
              </p>
            </div>

            <div className="space-y-1.5">
              <label htmlFor="folder-input" className="text-xs font-medium text-[var(--foreground)]">
                Virtual Folder
              </label>
              <Input
                id="folder-input"
                value={uploadFolder}
                onChange={(e) => setUploadFolder(e.target.value)}
                placeholder="/"
                className="font-mono text-xs"
              />
            </div>

            <DialogFooter className="gap-2 sm:gap-0 pt-3">
              <Button type="button" variant="outline" onClick={() => setIsUploadOpen(false)} disabled={uploading}>
                Cancel
              </Button>
              <Button type="submit" disabled={uploading || !uploadFile}>
                {uploading ? "Uploading..." : "Upload File"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Modal */}
      <Dialog open={Boolean(deletingFile)} onOpenChange={() => setDeletingFile(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <div className="flex items-center gap-2 text-rose-500 mb-1">
              <AlertTriangle className="h-5 w-5 shrink-0" />
              <DialogTitle className="text-rose-500">Delete File</DialogTitle>
            </div>
            <DialogDescription>
              Are you sure you want to permanently delete <strong>{deletingFile?.filename}</strong>? This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:gap-0 pt-2">
            <Button type="button" variant="outline" onClick={() => setDeletingFile(null)} disabled={deleting}>
              Cancel
            </Button>
            <Button type="button" variant="destructive" onClick={handleDeleteFile} disabled={deleting}>
              {deleting ? "Deleting..." : "Permanently Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Preview Modal */}
      <Dialog open={Boolean(previewFile)} onOpenChange={() => setPreviewFile(null)}>
        <DialogContent className="sm:max-w-3xl max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>Preview: {previewFile?.filename}</DialogTitle>
            <DialogDescription>
              SHA-256: <code className="font-mono text-xs">{previewFile?.checksum_sha256}</code>
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-auto p-3 rounded-lg border border-[var(--border)] bg-[var(--muted)]/30 font-mono text-xs">
            {previewLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-4 w-1/2" />
              </div>
            ) : previewContent ? (
              <pre className="whitespace-pre-wrap break-all">{previewContent}</pre>
            ) : previewFile?.content_type?.startsWith("image/") ? (
              <div className="flex justify-center p-4">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={`/api/v1/files/${previewFile.id}/preview`}
                  alt={previewFile.filename}
                  className="max-h-[500px] object-contain rounded"
                />
              </div>
            ) : previewFile?.content_type === "application/pdf" ? (
              <iframe
                src={`/api/v1/files/${previewFile.id}/preview`}
                className="w-full h-[500px] rounded border"
                title={previewFile.filename}
              />
            ) : (
              <div className="text-center py-8 text-[var(--muted-foreground)]">
                Binary or unsupported format. Please download the file to inspect its contents.
              </div>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setPreviewFile(null)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

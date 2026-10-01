// Thin client for the Scrapper backend API.
// API base + access token come from env (NEXT_PUBLIC_*) or localStorage.

export type Clip = {
  id: number;
  job_id: number | null;
  source_url: string;
  platform: string | null;
  uploader: string | null;
  title: string | null;
  duration: number | null;
  width: number | null;
  height: number | null;
  size_bytes: number | null;
  status: string;
  error: string | null;
  selected: boolean;
  has_thumb: boolean;
  created_at: string;
};

export type Compilation = {
  id: number;
  clip_ids: number[];
  orientation: string;
  status: string;
  progress: number;
  duration: number | null;
  size_bytes: number | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
};

export type Stats = {
  clips_total: number;
  clips_selected: number;
  clips_done: number;
  clips_failed: number;
  compilations: number;
  storage_bytes: number;
  retention_days: number;
  sources: { source: string; recent: number; success_rate: number | null; alerting: boolean }[];
  engine: { yt_dlp: string | null; ffmpeg: boolean; python: string };
};

export type LogLine = {
  id?: number;
  level: string;
  event: string;
  message: string;
  context?: Record<string, unknown> | null;
  created_at: string;
};

export type SocialAccount = {
  id: string; // "<upload-post profile>:<network>"
  profile?: string;
  nickname?: string;
  network: string;
  username: string;
  isActive?: number | boolean;
  profile_picture_url?: string;
};

export type MetadataResult = {
  title: string;
  caption: string;
  hashtags: string[];
  full_text: string;
  model: string;
};

export type SocialPost = {
  id: number;
  compilation_id: number | null;
  publish_requests: { profile: string; request_id?: string; error?: string }[] | null;
  accounts: string[];
  content: string;
  scheduled_at: string | null;
  status: string;
  error: string | null;
  created_at: string;
};

export type QueueItem = {
  id: number;
  compilation_id: number | null;
  clip_id: number | null;
  pipeline: string;
  video_name: string;
  video_path: string | null;
  thumb_path: string | null;
  drive_link: string | null;
  source: string | null;
  title: string;
  description: string;
  tags: string;
  accounts: string[];
  status: string; // "review" | "ready" | "posting" | "posted" | "retry" | "error" | "archived"
  notes: string | null;
  position: number | null;
  scheduled_at: string | null;
  published_at: string | null;
  publish_requests: { profile: string; request_id?: string; error?: string }[] | null;
  created_at: string;
};

export type ScheduleInfo = {
  timezone: string;
  start_hour: number;
  end_hour: number;
  interval_hours: number;
  slots_per_day: number;
  next_slots: string[];
  archive_delete_days: number;
  ready_without_accounts: number;
  pipelines: Record<
    string,
    { ready: number; next: { id: number; title: string; channel: string; scheduled_at: string }[] }
  >;
  ai: { configured: boolean; model: string };
  publisher: { name: string; configured: boolean; privacy: string };
};

const ENV_BASE = process.env.NEXT_PUBLIC_API_BASE || "";

export function apiBase(): string {
  if (typeof window !== "undefined") {
    const stored = window.localStorage.getItem("scrapper_api_base");
    if (stored) return stored.replace(/\/$/, "");
  }
  return (ENV_BASE || "http://127.0.0.1:8000").replace(/\/$/, "");
}

export function token(): string {
  if (typeof window !== "undefined") {
    return window.localStorage.getItem("scrapper_token") || "";
  }
  return process.env.NEXT_PUBLIC_ACCESS_TOKEN || "";
}

function headers(json = true): HeadersInit {
  const h: Record<string, string> = {};
  if (json) h["content-type"] = "application/json";
  const t = token();
  if (t) h["x-access-token"] = t;
  return h;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, init);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

// Browser-native GETs (<img src>, <a href> download, EventSource) can't set
// headers, so the token rides as a query param on those URLs.
function withToken(url: string): string {
  const t = token();
  return t ? `${url}?token=${encodeURIComponent(t)}` : url;
}

export const api = {
  base: apiBase,
  thumbUrl: (id: number) => withToken(`${apiBase()}/api/clips/${id}/thumb`),
  downloadUrl: (id: number) => withToken(`${apiBase()}/api/compilations/${id}/download`),
  clipDownloadUrl: (id: number) => withToken(`${apiBase()}/api/clips/${id}/download`),
  eventsUrl: () => withToken(`${apiBase()}/api/events`),

  health: () => req<{ ok: boolean }>("/api/health"),
  stats: () => req<Stats>("/api/stats", { headers: headers(false) }),
  clips: () => req<Clip[]>("/api/clips", { headers: headers(false) }),
  compilations: () => req<Compilation[]>("/api/compilations", { headers: headers(false) }),
  logs: (limit = 200) => req<LogLine[]>(`/api/logs?limit=${limit}`, { headers: headers(false) }),
  cookiesStatus: () => req<{ present: boolean; bytes: number }>("/api/cookies", { headers: headers(false) }),

  ingest: (urls: string[]) =>
    req<{ job_id: number; count: number }>("/api/ingest", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ urls }),
    }),

  rescan: () =>
    req<{ clips_added: number; compilations_added: number }>("/api/rescan", {
      method: "POST",
      headers: headers(false),
    }),

  select: (id: number, selected: boolean) =>
    req<{ id: number; selected: boolean }>(`/api/clips/${id}/select`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ selected }),
    }),

  deleteClip: (id: number) =>
    req<{ deleted: number }>(`/api/clips/${id}`, { method: "DELETE", headers: headers(false) }),

  compile: (orientation: string) =>
    req<{ compilation_id: number; count: number }>("/api/compile", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ orientation }),
    }),

  deleteCompilation: (id: number) =>
    req<{ deleted: number }>(`/api/compilations/${id}`, { method: "DELETE", headers: headers(false) }),

  uploadCookies: async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const h: Record<string, string> = {};
    const t = token();
    if (t) h["x-access-token"] = t;
    const res = await fetch(`${apiBase()}/api/cookies`, { method: "POST", headers: h, body: fd });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  socialAccounts: () =>
    req<{ configured: boolean; accounts: SocialAccount[]; message?: string; manage_url?: string; privacy?: string }>(
      "/api/social/accounts",
      { headers: headers(false) }
    ),

  generateMetadata: (compilationId: number, prompt?: string) =>
    req<MetadataResult>("/api/social/generate-metadata", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ compilation_id: compilationId, prompt }),
    }),

  publishSocial: (
    compilationId: number,
    accountIds: string[],
    content: string,
    scheduledAt?: string
  ) =>
    req<SocialPost>("/api/social/publish", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({
        compilation_id: compilationId,
        account_ids: accountIds,
        content,
        scheduled_at: scheduledAt || null,
      }),
    }),

  socialPosts: () => req<SocialPost[]>("/api/social/posts", { headers: headers(false) }),

  queue: (pipeline?: string, status?: string) => {
    const params = new URLSearchParams();
    if (pipeline && pipeline !== "all") params.set("pipeline", pipeline);
    if (status && status !== "all") params.set("status", status);
    const qs = params.toString() ? `?${params.toString()}` : "";
    return req<QueueItem[]>(`/api/queue${qs}`, { headers: headers(false) });
  },

  createQueueItem: (data: {
    compilation_id?: number;
    clip_id?: number;
    pipeline?: string;
    title?: string;
    description?: string;
    tags?: string;
    source?: string;
    drive_link?: string;
    accounts?: string[];
    status?: string;
    scheduled_at?: string;
  }) =>
    req<QueueItem>("/api/queue", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(data),
    }),

  updateQueueItem: (id: number, data: Partial<QueueItem>) =>
    req<QueueItem>(`/api/queue/${id}`, {
      method: "PATCH",
      headers: headers(),
      body: JSON.stringify(data),
    }),

  approveQueueItem: (id: number) =>
    req<QueueItem>(`/api/queue/${id}/approve`, {
      method: "POST",
      headers: headers(),
    }),

  publishQueueItem: (id: number) =>
    req<QueueItem>(`/api/queue/${id}/publish`, {
      method: "POST",
      headers: headers(),
    }),

  generateQueueAi: (id: number) =>
    req<QueueItem>(`/api/queue/${id}/generate-ai`, {
      method: "POST",
      headers: headers(),
    }),

  deleteQueueItem: (id: number) =>
    req<{ deleted: number }>(`/api/queue/${id}`, {
      method: "DELETE",
      headers: headers(false),
    }),

  bulkQueueAction: (
    ids: number[],
    action: string,
    opts?: {
      pipeline?: string;
      target_status?: string;
      accounts?: string[];
      title?: string;
      description?: string;
      tags?: string;
    }
  ) =>
    req<{ ok: boolean; count: number; action: string }>("/api/queue/bulk-action", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({
        ids,
        action,
        pipeline: opts?.pipeline,
        target_status: opts?.target_status,
        accounts: opts?.accounts,
        title: opts?.title,
        description: opts?.description,
        tags: opts?.tags,
      }),
    }),

  schedule: () => req<ScheduleInfo>("/api/schedule", { headers: headers(false) }),

  socialConnectUrl: (profile: string) =>
    req<{ url: string }>(`/api/social/connect-url?profile=${encodeURIComponent(profile)}`, { headers: headers(false) }),

  shuffleQueue: (data: { mode: "round_robin" | "random" | "by_channel"; pipeline?: string; status?: string }) =>
    req<{ ok: boolean; count: number; mode: string; message?: string }>("/api/queue/shuffle", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(data),
    }),

  ingestChannel: (data: {
    url: string;
    parent_folder_id?: string;
    parent_folder_name?: string;
    channel_name?: string;
    max_videos?: number;
    auto_approve?: boolean;
    upload_to_drive?: boolean;
  }) =>
    req<{ ok: boolean; message: string; channel: string; uploaded_count: number; items: any[] }>(
      "/api/social/ingest-channel",
      {
        method: "POST",
        headers: headers(),
        body: JSON.stringify(data),
      }
    ),

  syncDrive: (data: { folder_url?: string; folder_id: string; pipeline?: string; auto_approve?: boolean }) =>
    req<{ ok: boolean; message: string; channels?: any[] }>("/api/drive/sync", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(data),
    }),
};

/** Days left before the retention sweep deletes this item ("2d", "today", null). */
export function expiresIn(createdAt: string, retentionDays: number): string | null {
  if (!retentionDays || retentionDays <= 0) return null;
  const due = new Date(createdAt).getTime() + retentionDays * 86400_000;
  const msLeft = due - Date.now();
  if (msLeft <= 0) return "due";
  const days = Math.floor(msLeft / 86400_000);
  if (days >= 1) return `${days}d`;
  const hours = Math.max(1, Math.floor(msLeft / 3600_000));
  return `${hours}h`;
}

export function fmtBytes(n: number | null): string {
  if (!n) return "—";
  if (n < 1e6) return `${(n / 1e3).toFixed(0)} KB`;
  if (n < 1e9) return `${(n / 1e6).toFixed(0)} MB`;
  return `${(n / 1e9).toFixed(2)} GB`;
}

export function fmtDuration(s: number | null): string {
  if (!s) return "—";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

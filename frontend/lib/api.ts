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
};

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

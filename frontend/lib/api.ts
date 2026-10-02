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
  research?: {
    matched: boolean; title?: string; year?: string | null; confidence?: number; reason?: string;
    guess?: string; source?: string; cast?: { actor: string; character: string }[]; keywords?: string[];
  } | null;
  scheduled_at: string | null;
  published_at: string | null;
  publish_requests: { profile: string; request_id?: string; error?: string }[] | null;
  media_url?: string | null;
  created_at: string;
};

export type ScheduleConfig = {
  timezone: string;
  start_hour: number;
  end_hour: number;
  interval_hours: number;
};

export type TTSVoice = {
  id: string;
  name: string;
  gender: string;
  timbre: string;
  description: string;
  recommended_for: string[];
};

export type TTSVoicesResponse = {
  voices: TTSVoice[];
  styles: string[];
  tags: { tag: string; desc: string }[];
  default_voice: string;
  default_model: string;
  configured: boolean;
};

export type TTSGenerateResponse = {
  ok: boolean;
  audio_url: string;
  filename: string;
  duration_seconds: number;
  voice: string;
  model: string;
  text: string;
};

export type BulkAiStatus = {
  running: boolean;
  total: number;
  done: number;
  failed: number;
  errors: string[];
  started_at: string | null;
  finished_at: string | null;
  aborted: string | null;
};

export type ScheduleInfo = ScheduleConfig & {
  defaults: ScheduleConfig;
  customized: boolean;
  slots_per_day: number;
  next_slots: string[];
  archive_delete_days: number;
  ready_without_accounts: number;
  pipelines: Record<
    string,
    { ready: number; next: { id: number; title: string; channel: string; scheduled_at: string }[] }
  >;
  ai: { configured: boolean; model: string };
  scheduler: {
    enabled: boolean;
    paused: boolean;
    tick_seconds: number;
    started_at: string | null;
    last_tick_at: string | null;
    last_error: string | null;
    last_error_at: string | null;
    ticks: number;
    last_submitted: { id: number; at: string } | null;
  };
  publisher: { name: string; configured: boolean; privacy: string };
};

/** API times are UTC. A value without a zone must not be read as local time. */
export function parseApiDate(iso: string): number {
  return Date.parse(/([zZ]|[+-]\d\d:?\d\d)$/.test(iso) ? iso : `${iso}Z`);
}

// --- LongForm Studio ---------------------------------------------------------
export type StudioTitle = {
  tmdb_id: number;
  media_type: "movie" | "tv";
  title: string;
  date: string;
  overview: string;
  poster: string | null;
  popularity: number;
  release?: "theaters" | "limited" | "digital";   // upcoming movies: the US opening shown
  in_theaters_since?: string | null;               // already in theaters; the date shown is digital/wide
};

export type StudioShot = {
  id: string;
  source: string;
  source_type: string;
  start: number;
  end: number;
  seconds: number;
  still: string;
  thumb: string;
  description?: string;
  people?: string[];
  setting?: string;
  mood?: string;
  size?: string;
  card?: boolean;
  text?: boolean;
  quality?: string;
  usable?: boolean;
  tag_error?: string;
};

export type StudioSentence = {
  i?: number;
  paragraph: number;
  text: string;
  refs?: string[];
  check?: string;
  changed?: boolean;
  original?: string;
  reason?: string;
};

export type StudioPlanItem = {
  slot: number;
  sentence: number;
  text: string;
  kind: "poster" | "card" | "still" | "clip";
  shot?: string | null;
  start: number;
  end: number;
  duration: number;
  clip_start?: number;
  clip_len?: number;
};

export type StudioMotion = { mode: "off" | "compare" | "on"; cast_cards: number; available: boolean; reason: string | null };

export type StudioProject = {
  id: number;
  tmdb_id: number;
  media_type: string;
  title: string;
  target_minutes: number;
  stage: string;
  stage_status: "idle" | "running" | "done" | "error" | "stopped";
  stage_message: string | null;
  queue_item_id: number | null;
  created_at: string;
  updated_at: string;
  poster: string | null;
  cost_usd?: number;
  drive?: { status: "uploading" | "saved" | "error" | "stopped"; error?: string | null; link?: string;
            thumb_link?: string | null; saved_at?: string; rendered_at?: string } | null;
  has: Record<string, boolean>;
  facts?: any;
  research?: { text: string; sources: { title: string; url: string }[]; claims: { text: string; sources: number[] }[] } | null;
  trailer?: any;
  shots?: StudioShot[] | null;
  script?: {
    sentences: StudioSentence[];
    changes?: { original: string; fix: string; reason: string }[];
    word_count?: number;
    est_seconds?: number;
    youtube_title?: string;
    description?: string;
    tags?: string[];
    narration_seconds?: number;
  } | null;
  plan?: StudioPlanItem[] | null;
  render?: { file: string; thumbnail: string; seconds: number; size: number; rendered_at: string;
            motion_mode?: "off" | "compare" | "on";
            motion?: { file?: string; pieces: number; failures: string[]; cast_cards: string[];
                       available: boolean; reason: string | null } | null } | null;
};

export type StudioStatus = {
  tmdb: boolean;
  gemini: boolean;
  tts: boolean;
  voice: string;
  region: string;
  stages: string[];
  busy: { project_id: number | null; stage: string | null };
  attribution: string;
};

// --- Stop + Undo ---------------------------------------------------------------
export type Job = {
  id: string;
  kind: string;
  label: string;
  scope: string;
  ref?: any;
  status: "queued" | "running" | "stopping" | "cancelled" | "done" | "error";
  message: string;
  started_at?: string | null;
  finished_at?: string | null;
  elapsed?: number;
  result?: any;
};

export type UndoStep = { id: number; label: string; created_at: string };

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

export type CostSettings = {
  budget_usd: number;
  hard_stop: boolean;
  upload_post_plan: string;
  fixed_costs: { name: string; usd: number }[];
  tts_usd_per_million_chars: number;
  grounding_usd_per_1000: number;
  grounding_free_per_month: number;
  tts_free_chars_per_month: number;
  gemini: Record<string, { in: number; out: number; in_2027?: number; out_2027?: number }>;
};

export type CostSummary = {
  month: string;
  variable_usd: number;
  fixed_usd: number;
  total_usd: number;
  budget_usd: number;
  budget_used_pct: number | null;
  hard_stop: boolean;
  by_service: Record<string, { cost: number; requests: number; input_tokens: number; output_tokens: number; chars: number }>;
  by_operation: { operation: string; cost: number; requests: number }[];
  by_day: { day: string; cost: number }[];
  by_ref: Record<string, number>;
  free_tiers: {
    search_requests: { used: number; free: number };
    tts_chars: { used: number; free: number };
    uploads: { used: number; plan: string; limit: number | null; plan_usd: number };
  };
  settings: CostSettings;
};

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
    req<{ configured: boolean; accounts: SocialAccount[]; profiles?: string[]; message?: string; manage_url?: string; privacy?: string }>(
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

  queueCounts: (pipeline?: string) => {
    const params = new URLSearchParams();
    if (pipeline && pipeline !== "all") params.set("pipeline", pipeline);
    const qs = params.toString() ? `?${params.toString()}` : "";
    return req<{
      all: number;
      review: number;
      ready: number;
      posting: number;
      posted: number;
      retry: number;
      error: number;
      archived: number;
      errors_total: number;
    }>(`/api/queue/counts${qs}`, { headers: headers(false) });
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

  updateSchedule: (cfg: ScheduleConfig | { reset: true }) =>
    req<ScheduleInfo>("/api/schedule/config", {
      method: "PUT",
      headers: headers(),
      body: JSON.stringify(cfg),
    }),

  bulkAi: (ids: number[]) =>
    req<BulkAiStatus>("/api/queue/bulk-ai", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ ids, action: "ai" }),
    }),

  bulkAiStatus: () => req<BulkAiStatus>("/api/queue/bulk-ai", { headers: headers(false) }),

  // --- LongForm Studio ---
  studioStatus: () => req<StudioStatus>("/api/studio/status", { headers: headers(false) }),
  studioCalendar: () =>
    req<{ upcoming_movies: StudioTitle[]; on_the_air_tv: StudioTitle[]; trending: StudioTitle[] }>(
      "/api/studio/calendar", { headers: headers(false) }),
  studioSearch: (q: string) =>
    req<StudioTitle[]>(`/api/studio/search?q=${encodeURIComponent(q)}`, { headers: headers(false) }),
  studioProjects: () => req<StudioProject[]>("/api/studio/projects", { headers: headers(false) }),
  studioProject: (id: number) => req<StudioProject>(`/api/studio/projects/${id}`, { headers: headers(false) }),
  studioCreate: (t: { tmdb_id: number; media_type: string; title: string; target_minutes?: number }) =>
    req<StudioProject>("/api/studio/projects", { method: "POST", headers: headers(), body: JSON.stringify(t) }),
  studioPatch: (id: number, data: { target_minutes?: number; script?: any; plan?: any }) =>
    req<StudioProject>(`/api/studio/projects/${id}`, { method: "PATCH", headers: headers(), body: JSON.stringify(data) }),
  studioDelete: (id: number) =>
    req<{ deleted: number }>(`/api/studio/projects/${id}`, { method: "DELETE", headers: headers(false) }),
  studioRun: (id: number, stage: string, auto = false) =>
    req<{ ok: boolean }>(`/api/studio/projects/${id}/run`, {
      method: "POST", headers: headers(), body: JSON.stringify({ stage, auto }) }),
  studioPublish: (id: number) =>
    req<{ queue_item_id: number; status: string; drive_job_id?: string | null }>(`/api/studio/projects/${id}/publish`, {
      method: "POST", headers: headers() }),
  studioUploadTrailer: async (id: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const h: Record<string, string> = {};
    const t = token();
    if (t) h["x-access-token"] = t;
    const res = await fetch(`${apiBase()}/api/studio/projects/${id}/trailer-upload`, { method: "POST", headers: h, body: fd });
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    return res.json() as Promise<StudioProject>;
  },
  studioMotion: () => req<StudioMotion>("/api/studio/motion", { headers: headers(false) }),
  setStudioMotion: (patch: Partial<Pick<StudioMotion, "mode" | "cast_cards">>) =>
    req<StudioMotion>("/api/studio/motion", { method: "PUT", headers: headers(), body: JSON.stringify(patch) }),
  studioFileUrl: (id: number, path: string, bust?: string) => {
    const q = new URLSearchParams({ path });
    const t = token();
    if (t) q.set("token", t);
    if (bust) q.set("v", bust);
    return `${apiBase()}/api/studio/projects/${id}/file?${q.toString()}`;
  },

  aiCheck: () =>
    req<{ ok: boolean; model: string; title: string; hashtags: string[] }>("/api/social/ai-check", {
      method: "POST",
      headers: headers(),
    }),

  socialConnectUrl: (profile: string) =>
    req<{ url: string }>(`/api/social/connect-url?profile=${encodeURIComponent(profile)}`, { headers: headers(false) }),

  shuffleQueue: (data: { mode: "round_robin" | "random" | "by_channel"; pipeline?: string; status?: string }) =>
    req<{ ok: boolean; count: number; mode: string; message?: string }>("/api/queue/shuffle", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(data),
    }),

  jobs: () => req<Job[]>("/api/jobs", { headers: headers(false) }),
  job: (id: string) => req<Job>(`/api/jobs/${encodeURIComponent(id)}`, { headers: headers(false) }),
  stopJob: (id: string) =>
    req<Job>(`/api/jobs/${encodeURIComponent(id)}/stop`, { method: "POST", headers: headers() }),
  /** Poll a background job until it ends; resolves with its result, rejects on error/stop. */
  waitJob: async (id: string, onTick?: (j: Job) => void): Promise<any> => {
    for (;;) {
      const j = await api.job(id);
      onTick?.(j);
      if (j.status === "done") return j.result;
      if (j.status === "cancelled") throw new Error("Stopped");
      if (j.status === "error") throw new Error(j.message || "failed");
      await new Promise((r) => setTimeout(r, 2000));
    }
  },
  undoStack: (scope: string) =>
    req<{ scope: string; stack: UndoStep[] }>(`/api/undo?scope=${encodeURIComponent(scope)}`, { headers: headers(false) }),
  undo: (scope: string) =>
    req<{ undone: string; stack: UndoStep[] }>("/api/undo", {
      method: "POST", headers: headers(), body: JSON.stringify({ scope }) }),
  costs: (month?: string) =>
    req<CostSummary>(`/api/costs${month ? `?month=${encodeURIComponent(month)}` : ""}`, { headers: headers(false) }),
  setCostSettings: (patch: Partial<CostSettings>) =>
    req<CostSummary>("/api/costs/settings", { method: "PUT", headers: headers(), body: JSON.stringify(patch) }),
  seo: () => req<{ auto: boolean; clips_without_seo: number }>("/api/seo", { headers: headers(false) }),
  setSeo: (auto: boolean) =>
    req<{ auto: boolean; clips_without_seo: number }>("/api/seo", { method: "PUT", headers: headers(), body: JSON.stringify({ auto }) }),
  studioSaveToDrive: (id: number) =>
    req<{ job_id: string }>(`/api/studio/projects/${id}/drive`, { method: "POST", headers: headers() }),
  setAutopost: (paused: boolean) =>
    req<{ paused: boolean }>("/api/autopost", { method: "PUT", headers: headers(), body: JSON.stringify({ paused }) }),

  ingestChannel: (data: {
    url: string;
    parent_folder_id?: string;
    parent_folder_name?: string;
    channel_name?: string;
    max_videos?: number;
    auto_approve?: boolean;
    upload_to_drive?: boolean;
  }) =>
    req<{ job_id: string; status: string }>(
      "/api/social/ingest-channel",
      {
        method: "POST",
        headers: headers(),
        body: JSON.stringify(data),
      }
    ),

  syncDrive: (data: { folder_url?: string; folder_id: string; pipeline?: string; auto_approve?: boolean }) =>
    req<{ job_id: string; status: string }>("/api/drive/sync", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(data),
    }),

  ttsVoices: () => req<TTSVoicesResponse>("/api/tts/voices", { headers: headers(false) }),

  ttsGenerate: (data: { text: string; voice?: string; style?: string; model?: string }) =>
    req<TTSGenerateResponse>("/api/tts/generate", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(data),
    }),

  ttsCheck: () =>
    req<{ ok: boolean; model: string; voice: string; audio_url: string; duration: number; sample_text: string }>(
      "/api/tts/check",
      { method: "POST", headers: headers() }
    ),

  generateQueueVoiceover: (id: number, opts?: { voice?: string; style?: string; model?: string }) =>
    req<{ ok: boolean; script: string; audio_url: string; duration: number; voice: string; item_id: number }>(
      `/api/queue/${id}/generate-voiceover`,
      {
        method: "POST",
        headers: headers(),
        body: JSON.stringify(opts || {}),
      }
    ),
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

// Thin client for the Scrapper backend API.
// API base + access token come from env (NEXT_PUBLIC_*) or localStorage.

export type EditorialOpportunity = {
  id: number; title: string; angle: string; audience: string; formats: string[];
  evidence: { url: string; excerpt: string; entity: string; retrieved_at: string }[];
  demand: number | null; demand_source: string | null; competition: string; feasibility: string;
  planned_for: string | null; status: string;
  feedback_refs?: number[];
  assessment?: { confidence: string; uncertainty: string[]; evidence_count: number };
};
export type EditorialInsights = {
  adapters: { platform: string; status: string; reason: string; live_metrics: null; imported_observations: number }[];
  observation_count: number;
  cohorts: { platform: string; account_id?: string; format: string; age_hours: number; n: number; confidence: string;
    medians: Record<string, number | null>; metric_samples: Record<string, number>; limitations: string }[];
  next_actions: string[]; claim: string;
};
export type ContentSource = { kind: "export_version" | "native_export" | "delivery_archive"; id: string; title: string; fingerprint: string; created_at: string;
  preview: { kind: "video" | "image"; file?: string; role?: string; index?: number } | null };
export type ContentMappingDecision = { platform: string; account_id: string; content_id: string; format: string; published_at: string;
  source_kind: ContentSource["kind"]; source_id: string; expected_source_fingerprint: string; mapping_evidence: string; reviewed: true };
export type ContentBinding = { id: string; revision: number; platform: string; account_id: string; content_id: string; created_at: string;
  data: { decision: ContentMappingDecision; source_fingerprint: string; supersedes: string | null;
    cost_snapshot: { status: string; amount_usd: number | null; scope?: string; reason?: string; limitations?: string[]; by_basis?: Record<string, number> }; mapping_basis: string; public_bytes_verified: boolean } };
export type AudienceAdapterStatus = { platform: string; status: string; reason: string; mode: string; real_account_verified: boolean; latency: string };
export type NativeBeat = { role: "hook" | "body" | "payoff"; text: string; source_refs: number[]; asset_id?: number | null };
export type NativeExport = { id: string; campaign_id: number; format: string; queue_item_id: number | null;
  approval: { fingerprint: string; at: string } | null;
  manifest: { cards: { file: string; alt: string; sha256: string; caption_lines: number }[];
    video?: { file: string }; cover: { file: string }; quality: { decode: string; errors: string[]; warnings: string[]; seconds?: number; width?: number; height?: number };
    delivery?: string; platform_copy?: string;
    manual_post?: { format: string; title: string; text: string; options: string[]; delivery: string; publication_status: string };
    manual_assets?: { file: string; sha256: string; content_type: string }[];
    limitations: string[]; timeline?: { start: number; end: number; text: string }[] } };
export type NativeCampaign = { id: number; opportunity_id: number; stage_status: string; stage_message: string | null;
  brief: { title: string; formats: string[]; beats: NativeBeat[]; short_caption: string; reel_caption?: string; carousel_caption: string; community_question: string; image_text?: string; image_caption?: string; community_text?: string; poll_question?: string; poll_options?: string[] };
  exports: NativeExport[] };
export type IndexedAsset = { id: number; sha256: string; kind: string; tags: string[]; provenance: { license: string; attestation: string } };
export type CatalogAsset = IndexedAsset & { score: number; matching_spans: { start: number; end: number; text: string }[];
  provenance: IndexedAsset["provenance"] & { description?: string; people?: string[];
    media?: { seconds: number; bytes: number; caption_count: number; frames: { at: number; sha256: string }[] } } };
export type CommentCursorView = { id: string; version: string; pages: number; pagination_exhausted: boolean; can_resume: boolean; status: string; last_error: string | null; seen_entries: number; new_versions: number; duplicates: number; scope: string };
export type FeedbackCoverage = { reported_replies: number | null; retained_replies: number; pagination_exhausted: boolean; requires_decision: boolean; parent_evidence_id: string; scope: string };
export type AudienceFeedback = { id: number; feedback_identity: string; text: string; category: string; classification: string;
  reply_draft: string; reply_version: string; approved_for_manual_use: boolean; privacy_note: string;
  lineage_id?: string; latest_evidence_id?: string; context_lineage_id: string | null; context_title: string | null;
  context_mapping_superseded: boolean; coverage: FeedbackCoverage | null };
export type FeedbackContext = AudienceFeedback & { thread_members: { feedback_id: number; evidence_id: string; text: string; reply: boolean; provider_updated_at: string }[];
  thread_total: number; claim_total: number; claims: { key: string; kind: string; index: number; text: string }[];
  decisions: { id: string; action: string; data: Record<string, unknown>; at: string }[] };
export type LearningReport = { delivery: string; live_comment_ingestion: string; feedback: AudienceFeedback[];
  question_groups: { topic_token: string; n: number; suggestion: string; feedback_ids: number[] }[];
  experiments: { id: number; name: string; hypothesis: string; metric: string; age_hours: number;
    result: { status: string; design: string; winner: null; relative_median_change_percent: number | null;
      groups: { control: { n: number; median: number | null }; variant: { n: number; median: number | null } }; next_action: string } }[] };

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
  disk?: DiskSystemStatus;
  safe_e2e_mode?: boolean;
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
  media_kind?: "video" | "images";
  media_files?: string[];
  thumb_path: string | null;
  thumb_version?: number | null;
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
    native_export_id?: string; approval_fingerprint?: string;
    matched?: boolean; title?: string; year?: string | null; confidence?: number; reason?: string;
    guess?: string; source?: string; cast?: { actor: string; character: string }[]; keywords?: string[];
  } | null;
  scheduled_at: string | null;
  pinned_at?: string | null;          // owner-pinned post time (never re-planned)
  published_at: string | null;
  publish_requests: { profile: string; request_id?: string; error?: string }[] | null;
  media_url?: string | null;
  momentum?: ReleaseMomentum | null;
  created_at: string;
};

export type PublicationEntry = { client_request_id: string; state: string; profile?: string; platforms?: string[]; request_id?: string; error?: string };
export type PublicationEvidenceView = {
  queue_id: number; status: string;
  current: { entry: PublicationEntry; version: string; resolvable: boolean }[];
  events: { id: number; attempt_id: string; kind: string; payload: Record<string, unknown>; at: string }[];
  decisions: { id: number; attempt_id: string; evidence: Record<string, unknown>; at: string }[];
};
export type PublicationDecisionInput = {
  request_key: string; attempt_id: string; expected_version: string; account: string;
  outcome: "accepted" | "not_accepted" | "unresolved"; provider_request_id: string | null;
  evidence_url: string; rationale: string; reviewed_account: true; absence_confirmed: boolean;
};

export type ReleaseMomentum = {
  has_release_date: boolean;
  release_date: string | null;
  days_until_release: number | null;
  status: "critical" | "approaching" | "upcoming" | "released" | "unknown";
  urgency: "urgent" | "optimal" | "early" | "missed" | "none";
  is_pre_release: boolean;
  is_post_release: boolean;
  scheduled_after_release: boolean;
  badge_text: string;
  badge_variant: "critical" | "approaching" | "upcoming" | "missed" | "neutral";
  warning: string | null;
  priority_score: number;
};

export type PacingOverride = {
  posts_per_day?: number | null;
  start_hour?: number | null;
  end_hour?: number | null;
  interval_hours?: number | null;
  times?: string[] | null;          // exact posting times "HH:MM" — win over posts_per_day
  platforms?: Record<string, number | string[]> | null;   // per platform: posts/day, or its own times ["11:00", "19:00"]
};

export type ScheduleConfig = {
  timezone: string;
  start_hour: number;
  end_hour: number;
  interval_hours: number;
  posts_per_day?: number | null;
  pipeline_overrides?: Record<string, PacingOverride>;
  account_overrides?: Record<string, PacingOverride>;
  pipelines?: Record<string, PacingOverride>;
  accounts?: Record<string, PacingOverride>;
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

export type BackupCopy = {
  filename: string;
  size_bytes: number;
  created_at: string;
  download_url: string;
};

export type BackupStatus = {
  ok: boolean;
  enabled: boolean;
  database_type: string;
  last_backup: {
    filename: string;
    tag: string;
    created_at_local: string;
    uncompressed_bytes: number;
    compressed_bytes: number;
    sha256: string;
    duration_seconds: number;
    drive?: {
      uploaded: boolean;
      status: string;
      link?: string;
      error?: string;
    };
  } | null;
  is_stale: boolean;
  warning: string | null;
  local_copies_count: number;
  local_copies: BackupCopy[];
  drive_folder_id: string;
  drive_status: string;
  next_scheduled_run: string;
  retention: {
    keep_local: number;
    keep_drive_days: number;
  };
};

export type BackupNowResponse = {
  ok: boolean;
  filename: string;
  tag: string;
  compressed_bytes: number;
  uncompressed_bytes: number;
  duration_seconds: number;
  drive: {
    uploaded: boolean;
    status: string;
    link?: string;
    error?: string;
  };
};

export type DiskSystemStatus = {
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  total_gb: number;
  used_gb: number;
  free_gb: number;
  percent_used: number;
  status: "healthy" | "warning" | "critical";
  max_threshold_percent: number;
  warn_threshold_percent: number;
  path: string;
  caches?: {
    motion_cache_mb: number;
    tts_cache_mb: number;
    audio_cache_mb: number;
    max_motion_mb: number;
    max_tts_mb: number;
  };
};

export type CleanupResult = {
  retention?: { clips: number; compilations: number };
  leftover_renders: { folders_removed: number };
  temp_downloads: { files_removed: number; bytes_freed: number };
  caches: {
    motion: { files_removed: number; bytes_freed: number; remaining_bytes: number };
    tts: { files_removed: number; bytes_freed: number; remaining_bytes: number };
    audio?: { files_removed: number; bytes_freed: number; remaining_bytes: number };
  };
  disk_usage: DiskSystemStatus;
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
  pipeline_slots_per_day?: Record<string, number>;
  pipeline_next_slots?: Record<string, string[]>;
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
  auto_usable?: boolean;   // what the tagger decided, when the owner overrode it
  owner_set?: boolean;
  look?: string;           // look-alike group (shots that show the same picture share one)
  file?: string;           // absolute path of the source video on the server
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

export type StudioArchive = {
  settings: { enabled: boolean; days: number };
  projects: { id: number; title: string; eligible: boolean; reason: string; frees_mb: number; archived_at: string | null }[];
  would_free_mb: number;
};

export type StudioMotion = { mode: "off" | "compare" | "on"; cast_cards: number; available: boolean; reason: string | null };

export type CandidateCheck = { status: "pass" | "warn" | "fail"; why: string };
export type StudioCandidate = {
  key: string;
  tmdb_id: number;
  media_type: "movie" | "tv";
  title: string;
  poster: string | null;
  overview: string;
  trending_rank: number;
  popularity: number;
  release: { date: string; kind: string; next?: string | null; last?: string | null };
  imdb_id: string | null;
  imdb: {
    rating: number | null; votes: number; metascore: number | null; meter_rank: number | null;
    meter_change: string | null; meter_change_by: number | null; review_avg: number | null;
    review_lines: string[]; reviews_total: number; has_trailer: boolean; page?: string;
  } | null;
  trailer_stats?: { views: number; likes: number | null; comments: number | null; published_at: string | null } | null;
  youtube_trailer: string | null;
  checks: Record<"not_made" | "trailer" | "facts" | "release" | "interest", CandidateCheck>;
  score: { total: number; parts: Record<string, number>; views_per_day: number;
           sentiment: { value: number; parts: Record<string, number> } };
  mark: "pin" | "skip" | null;
  auto_ok: boolean;
  beyond_top?: boolean;
};
export type StudioCandidates = {
  built_at: string | null;
  youtube_note: string | null;
  refreshing: boolean;
  last_error: string | null;
  movie: StudioCandidate[];
  tv: StudioCandidate[];
  not_passing: StudioCandidate[];
};
export type StudioAuto = {
  enabled: boolean;
  paused_until?: string | null;
  movies_per_day: number;
  tv_per_day: number;
  today: { movie: number; tv: number };
  estimate_usd: number;
  waiting: string | null;
  next: { title: string; media_type: string; score: number } | null;
  last_check_at: string | null;
  last_result: string | null;
  last_started: { project_id: number; title: string; media_type: string; at: string | null; score?: number } | null;
  busy: { project_id: number | null; stage: string | null };
  undo: string;
};
export type RenderBatch = {
  running: boolean;
  total: number;
  done: number;
  current: number | null;
  results: { id: number; title: string; ok: boolean; why?: string | null }[];
  started_at: string | null;
  finished_at: string | null;
  job_status: string | null;
};

export type StudioProject = {
  script_version?: string;
  id: number;
  tmdb_id: number;
  media_type: string;
  title: string;
  target_minutes: number;
  stage: string;
  stage_status: "idle" | "running" | "done" | "error" | "stopped" | "paused" | "queued" | "awaiting_worker";
  stage_message: string | null;
  queue_item_id: number | null;
  created_at: string;
  updated_at: string;
  poster: string | null;
  primary_date?: string | null;
  momentum?: ReleaseMomentum | null;
  cost_usd?: number;
  archive?: { posted_at?: string | null; archived_at?: string | null; freed_mb?: number; restored_at?: string | null } | null;
  review?: { auto?: boolean; reviewed_at?: string | null; score?: number;
             export_approval?: { fingerprint: string; approved_at: string };
             archived_at?: string | null; delete_after?: string | null } | null;
  drive?: { status: "uploading" | "saved" | "error" | "stopped"; error?: string | null; link?: string;
            thumb_link?: string | null; saved_at?: string; rendered_at?: string } | null;
  has: Record<string, boolean>;
  script_ready?: boolean;
  research_issues?: string[];
  facts?: any;
  research?: { text: string; sources: { title: string; url: string }[]; claims: { text: string; sources: number[]; evidence?: { verdict: string; scope?: string; excerpt?: string; rationale?: string } }[] } | null;
  trailer?: any;
  shots?: StudioShot[] | null;
  plan?: StudioPlanItem[] | null;
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
  render?: { file: string; thumbnail: string; selected_thumbnail?: string;
             quality?: { status: string; decode: string; sha256: string; lufs: number | null; errors: string[]; warnings: string[] };
            thumbnails?: { id: string; label: string; desc: string; file: string }[];
            thumbnails_updated_at?: string;
            seconds: number; size: number; rendered_at: string;
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

// --- Countdown Compilations ---
export type CountdownTheme = {
  id: string;
  title: string;
  description: string;
  media_type: string;
  genre_ids?: number[];
  default_count: number;
};

export type CuratedCandidate = {
  tmdb_id: number;
  media_type: string;
  imdb_id: string | null;
  title: string;
  date: string;
  overview: string;
  poster: string | null;
  backdrop: string | null;
  vote_average: number;
  popularity: number;
};

export type EligibleProject = {
  id: number;
  title: string;
  media_type: string;
  tmdb_id: number;
  seconds: number;
  rendered_at: string;
  poster_url: string | null;
  thumb_url: string;
  facts: {
    primary_date?: string;
    genres?: string[];
    tagline?: string;
  };
};

export type CompilationChapter = {
  time: number;
  time_formatted: string;
  title: string;
  project_id?: number;
};

export type CompilationPreview = {
  title: string;
  total_seconds: number;
  total_minutes: number;
  item_count: number;
  chapters: CompilationChapter[];
};

export type StitchResult = {
  compilation_id: number | string;
  title: string;
  total_seconds: number;
  total_minutes: number;
  video_path: string;
  primary_thumb: string;
  thumbnails: string[];
  chapters: CompilationChapter[];
  chapters_text: string;
  description: string;
  queue_item_id: number | null;
  started?: boolean;   // the stitch runs in the background; the video lands in Needs Review
  job_id?: string;
};

export type CinemetaTitle = {
  imdb_id: string;
  title: string;
  year: string;
  media_type: string;
  genres: string[];
  director: string[];
  cast: string[];
  rating: number;
  synopsis: string;
  poster: string | null;
  background: string | null;
  runtime: string;
  trailers: { youtube_id: string; type: string; url: string }[];
};

export type FmhyResource = {
  category: string;
  name: string;
  url: string;
  description: string;
};

export type CountdownTrigger = {
  id: string;
  name: string;
  icon: string;
  desc: string;
};

// --- Countdowns Studio (2026-10-12) ---------------------------------------------------
export type CountdownEntryRow = {
  rank: number;
  title: string;
  year?: number | string;
  score?: number;
  tmdb_id?: number | null;
  media_type?: string;
  poster?: string | null;
  why_it_ranks?: string;
  key_stat?: string;
  fun_fact?: string;
  where_to_watch?: string;
  segment_id?: number;
  reused?: boolean;
};

export type CountdownSegment = {
  id: number;
  title: string;
  rank?: number;
  poster?: string | null;
  stage: string;
  stage_status: string;
  stage_message?: string | null;
  rendered: boolean;
  seconds?: number;
  reused?: boolean;
  missing?: boolean;
};

export type CountdownEligibility = {
  territory: string; medium: string | null; genres: string[]; genre_mode: string; excluded_genres: string[];
  production_countries: string[]; original_languages: string[]; year_min: number | null; year_max: number | null;
  date_from: string | null; date_to: string | null; providers: string[]; requires_independent_review: string[]; interpretation: string;
};
export type CountdownProject = {
  ranking_version?: string;
  eligibility_criteria?: CountdownEligibility;
  bookend_version?: string;
  bookend_reviewed?: boolean;
  bookend_issue?: string;
  script_version?: string;
  id: number;
  topic: string;
  fmt: "top5" | "top10" | "top15" | string;
  count: number;
  stage: string;
  stage_status: string;
  stage_message?: string | null;
  title: string;
  queue_item_id?: number | null;
  queue_status?: string | null;
  review: { reviewed_at?: string | null; delete_after?: string | null; auto?: boolean; keyword?: string; keywords?: string[];
            export_approval?: { fingerprint: string; approved_at: string };
            demand?: { overall?: number; monthly_searches?: number; growth_pct?: number } };
  created_at: string;
  updated_at: string;
  posters: string[];
  entry_count: number;
  unmatched: number;
  segments_made: number;
  segments_total: number;
  est_minutes: number;
  segment_minutes: number;
  auto: boolean;
  cost_usd: number;
  has: { ranking: boolean; titles: boolean; segments: boolean; script: boolean; render: boolean };
  render?: {
    quality?: { decode: string; errors: string[]; warnings: string[]; sha256: string; lufs: number | null };
    compilation_id: number | string; file: string; thumbnail: string; thumbnails: Record<string, string>;
    selected_thumbnail: string; chapters: { time_formatted: string; title: string }[];
    seconds: number; minutes: number; rendered_at: string; thumb_v?: number; archived_at?: string;
    drive?: { status?: string; error?: string | null; link?: string; saved_at?: string };
  } | null;
  // full view only
  ranking?: { contract_version?: number; eligibility?: { criteria: CountdownEligibility; checked_at: string; scope: string }; entries: CountdownEntryRow[]; title_options?: string[]; hook_script?: string; closing_question?: string;
              why_now?: string; honorable_mentions?: string[];
              market?: { keyword?: string; demand?: { monthly_searches: number; competition: number; growth_pct: number } | null;
                         related?: { keyword: string; monthly_searches: number }[]; questions?: { question: string; monthly_searches: number }[];
                         competitors?: { title: string; views?: number; channel?: string; published?: string; id?: string }[] } } | null;
  notes?: string | null;
  script?: { title?: string; title_options?: string[]; intro?: string; outro?: string; thumbnail_text?: string;
             title_scores?: Record<string, any> } | null;
  segments?: CountdownSegment[];
};

export type CountdownCheck = { status: "pass" | "warn" | "fail"; why: string };
export type CountdownTopic = {
  sig: string; topic: string; keyword: string; group: "month" | "trend" | "evergreen"; why_now: string;
  debate: number; best_format: string; sizes: number[]; score: number; pinned?: boolean;
  titles: { title: string; year?: number; tmdb_id: number; media_type: string; poster?: string | null }[];
  demand?: { overall: number; volume: number; competition: number; monthly_searches: number; growth_pct: number };
  keywords?: string[];
  checks: Record<string, CountdownCheck>;
};
export type CountdownTopics = {
  built_at?: string | null; month?: string; topics: CountdownTopic[]; skipped: CountdownTopic[]; building: boolean; vidiq: boolean;
  videos: { title: string; views?: number; breakout?: number; vph?: number; channel?: string; id?: string }[];
  asks?: string;
  rising?: { keyword: string; monthly_searches: number; change_pct: number; category: string }[];
};
export type CountdownAuto = {
  settings: { enabled: boolean; per_day: number; paused_until?: string | null };
  state: { last_check_at?: string | null; last_result?: string | null;
           last_started?: { countdown_id: number; topic: string; fmt: string; score: number; at: string } | null };
  today: number;
  next: { topic: string; fmt: string; score: number | null; sig: string; estimate_usd: number; from_line?: boolean } | null;
  waiting: string | null;
  line: { id: string; topic: string; fmt: string; sig: string; from_topics: boolean; notes?: string; added_at: string }[];
  topics_built_at?: string | null;
};
export type VidIQStatus = {
  configured: boolean; connected?: boolean; error?: string; last_error?: string | null;
  settings: { enabled: boolean; daily_credits: number; monthly_credits: number; cache_hours: number };
  usage: { today: number; month: number; calls_today: number };
};

export type CountdownTopicCandidate = {
  id: string;
  topic: string;
  trigger: "trending" | "calendar" | "streaming" | "debate" | "evergreen" | string;
  trigger_name: string;
  format: "top5" | "top10" | "top15" | string;
  why_now: string;
  demand: number;
  momentum: number;
  debate: number;
  gap: number;
  visuals: number;
  total_score: number;
  suggested_entries: string[];
};

export type CountdownEntry = {
  rank: number;
  title: string;
  year: number;
  score: number;
  key_stat: string;
  why_it_ranks: string;
  fun_fact: string;
  where_to_watch: string;
  sources: string[];
  tmdb_id?: number;
  media_type?: string;
  poster?: string | null;
  backdrop?: string | null;
  tmdb_rating?: number;
  overview?: string;
  justwatch_url?: string;
  linked_project?: {
    project_id: number;
    has_render: boolean;
    seconds: number;
    thumbnail?: string | null;
    stage?: string;
  } | null;
  has_footage?: boolean;
};

export type ResearchedCountdown = {
  id?: string;
  topic: string;
  why_now: string;
  format: "top5" | "top10" | "top15" | string;
  title_options: string[];
  thumbnail_text: string;
  hook_script: string;
  entries: CountdownEntry[];
  honorable_mentions: string[];
  closing_question: string;
  unverified_flags: string[];
  generated_at?: string;
};

export type CountdownCompilation = {
  id: string;
  queue_item_id?: number;
  compilation_id?: string;
  title: string;
  topic: string;
  status: string;
  pipeline: string;
  source: string;
  video_path?: string | null;
  thumb_path?: string | null;
  has_video: boolean;
  format: string;
  hook_script?: string;
  closing_question?: string;
  chapters?: { time_formatted: string; title: string; rank?: number }[];
  created_at?: string | null;
  notes?: string | null;
  project_ids?: number[];
  entry_count?: number;
  size_mb?: number;
  video_url?: string | null;
  thumb_url?: string | null;
  // Countdown production (audit 2026-10-11): making → stitching → done, or stopped with a reason.
  production?: { state?: string; reason?: string | null; started_at?: string; finished_at?: string };
};

// --- Stop + Undo ---------------------------------------------------------------


export type Job = {
  id: string;
  kind: string;
  label: string;
  scope: string;
  ref?: any;
  status: "queued" | "running" | "stopping" | "cancelled" | "done" | "error" | "waiting";
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

export type QueueThumbOptions = {
  has_thumbnail: boolean;
  updated_at: number | null;
  project_id: number | null;
  selected: string | null;
  options: { id: string; label: string; desc: string; file: string }[];
};

export type ExportAcceptanceView = { fingerprint: string; checks: Record<string, string>;
  findings: { id: string; kind: string; message: string }[]; source: unknown; rights_status: string };
export type ExportAcceptanceRequest = { expected_fingerprint: string; checks: string[];
  warning_decisions: Record<string, string>; rights_rationale: string; reviewed: true };

export type FinancialHold = { id: string; kind: "usd" | "credit"; service: string; state: string; ref: string | null;
  reserved: number; actual: number | null; amount_basis: string; usage_estimate: number | null; provider_invoice_verified: boolean; version: string; worker_live: boolean; orphan: boolean; can_resolve: boolean; created_at: string };
export type FinancialHoldView = { holds: FinancialHold[]; history: { id: string; kind: string; reservation_id: string; amount: number; receipt: string; rationale: string; at: string }[] };
export type CostSettings = {
  request_bounds?: Record<string, number>;
  budget_usd: number;
  daily_usd: number;
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
  billing_status: string;
  by_cost_basis: Record<string, { cost_usd: number; events: number }>;
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
  breakdowns?: { id: number; title: string; auto: boolean; cost: number }[];
  today: {
    cap_usd: number;
    base_cap_usd?: number;
    extra_today_usd?: number;
    spent_usd: number;
    held_usd?: number;
    remaining_usd: number | null;
    reached: boolean;
    resets_at: string;
    timezone: string;
    paused_jobs: { id: number; label: string; since: string | null; step?: string | null }[];
  };
  free_tiers: {
    search_requests: { used: number; free: number };
    tts_chars: { used: number; free: number };
    uploads: { used: number; plan: string; limit: number | null; plan_usd: number };
  };
  settings: CostSettings;
};

// The token value the server last refused. Until a different one is saved, the
// page sends nothing: its panels refresh every few seconds, and a wrong or
// missing token used to fire dozens of failed sign-ins a minute (2026-10-04).
let rejectedToken: string | null = null;
export function tokenRejected(): boolean {
  return rejectedToken !== null && rejectedToken === token();
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  if (tokenRejected()) {
    throw new Error("401: the access token was refused — enter the value of ACCESS_TOKEN (Railway) under ⚙ Connection");
  }
  const sent = token();
  const res = await fetch(`${apiBase()}${path}`, init);
  if (!res.ok) {
    const body = await res.text();
    if (res.status === 401 && !body.includes("media link")) rejectedToken = sent;
    throw new Error(`${res.status}: ${body}`);
  }
  if (rejectedToken === sent) rejectedToken = null;
  return res.json() as Promise<T>;
}

// Browser-native GETs (<img src>, <video>, <a href> download, EventSource) can't
// set headers. They used to carry the access token as ?token=… — visible in
// history, logs and anything a link was pasted into. Now they carry a media
// pass (?g=…): signed by the server, valid 12 h, and only good for viewing
// files on the read-only media routes. The token itself only travels in headers.
let _pass: { pass: string; expires_at: number } | null = null;

export async function refreshMediaPass(): Promise<void> {
  if (!token()) {
    _pass = null;
    return;
  }
  const r = await req<{ pass: string; expires_at: number }>("/api/media-pass", { headers: headers(false) });
  _pass = r;
  try {
    window.sessionStorage.setItem("scrapper_media_pass", JSON.stringify(r));
  } catch {
    /* private mode: memory only */
  }
}

function currentPass(): string | null {
  if (!_pass && typeof window !== "undefined") {
    try {
      const s = window.sessionStorage.getItem("scrapper_media_pass");
      if (s) _pass = JSON.parse(s);
    } catch {
      /* ignore */
    }
  }
  return _pass && _pass.expires_at * 1000 > Date.now() + 60_000 ? _pass.pass : null;
}

/** A media URL (absolute, or an /api/... path) with the media pass added. */
export function mediaUrl(url: string): string {
  const full = url.startsWith("http") ? url : `${apiBase()}${url}`;
  const g = currentPass();
  if (!g) return full;
  return `${full}${full.includes("?") ? "&" : "?"}g=${encodeURIComponent(g)}`;
}
const withToken = mediaUrl;

/** Private backups require the owner header; never attach a reusable media pass. */
export async function downloadBackup(path: string, filename: string): Promise<void> {
  if (!/^\/api\/backup\/download\/scrapper_[^/]+\.db\.gz$/.test(path)) throw new Error("Invalid backup path");
  const res = await fetch(`${apiBase()}${path}`, { headers: headers(false), cache: "no-store" });
  if (!res.ok) throw new Error(`Backup download failed (${res.status})`);
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export type RetainedVersion = { id: string; kind: "studio" | "countdown"; project_id: number; title: string;
  created_at: string; project_created_at: string; approval_recorded: boolean;
  assets: { role: string; available: boolean; sha256?: string; bytes?: number; reason?: string }[] };
export type RetainedDetail = { id: string; kind: "studio" | "countdown"; project_id: number;
  manifest: { title: string; snapshot: { inputs: { script?: any }; render: any; approval: any } } };

export type DeliveryHistory = {
  retention: string; next_offset: number; has_more: boolean;
  deliveries: { id: string; queue_id: number; created_at: string; title: string; pipeline: string; published_at: string | null; status: string; asset_count: number }[];
  remote_copies: { id: string; resource: string; file_id: string; action: string; created_at: string;
    evidence: { role?: string; link?: string; reason?: string; verification_recorded?: boolean; verification?: { sha256?: string; remote_file_id?: string; bytes?: number }; scope?: string; retention?: string } }[];
};
export type DeliveryDetail = { id: string; manifest: {
  snapshot: { title: string; description: string; tags: string; status: string; accounts: string[];
    published_at: string | null; drive_link?: string; notes?: string; publish_requests?: { state?: string; profile?: string; request_id?: string }[] };
  assets: { role: string; mime: string; available: boolean; bytes?: number; sha256?: string; reason?: string }[];
  delivery_scope: string; remote_retention: string;
} };

export const api = {
  deliveryHistory: (offset = 0) => req<DeliveryHistory>(`/api/delivery-history?offset=${offset}`, { headers: headers(false) }),
  deliveryDetail: (id: string) => req<DeliveryDetail>(`/api/delivery-history/${id}`, { headers: headers(false) }),
  deliveryFile: (id: string, index: number) => mediaUrl(`/api/delivery-history/${id}/file/${index}`),
  retainedVersions: (kind?: string, projectId?: number) => req<{ project_version: string | null; versions: RetainedVersion[] }>(
    `/api/export-history${kind && projectId ? `?kind=${kind}&project_id=${projectId}` : ""}`, { headers: headers(false) }),
  retainedDetail: (id: string) => req<RetainedDetail>(`/api/export-history/${id}`, { headers: headers(false) }),
  retainedCapture: (kind: string, id: number) => req<{ id: string | null }>(`/api/export-history/${kind}/${id}/capture`, { method: "POST", headers: headers() }),
  retainedRestore: (id: string, expected: string) => req<{ restored: string }>(`/api/export-history/${id}/restore-draft`,
    { method: "POST", headers: headers(), body: JSON.stringify({ reviewed: true, expected_project_version: expected }) }),
  retainedFile: (id: string, role: string) => mediaUrl(`/api/export-history/${id}/file/${role}`),
  editorialLearning: () => req<LearningReport>("/api/editorial/learning", { headers: headers(false) }),
  editorialFeedback: (data: unknown) => req<{ created: number[] }>("/api/editorial/feedback/import", { method: "POST", headers: headers(), body: JSON.stringify(data) }),
  editorialReply: (id: number, data: { text: string; expected_version: string; approved_for_manual_use: boolean; reviewed: boolean; context_lineage_id: string | null; coverage_rationale: string }) => req<AudienceFeedback>(`/api/editorial/feedback/${id}/draft`, { method: "PUT", headers: headers(), body: JSON.stringify(data) }),
  feedbackContext: (id: number, offset = 0) => req<FeedbackContext>(`/api/editorial/feedback/${id}/context?offset=${offset}`, { headers: headers(false) }),
  feedbackList: (lineage_id: string, q = "", offset = 0) => req<{ feedback: AudienceFeedback[]; total: number }>(`/api/editorial/feedback?${new URLSearchParams({ lineage_id, q, offset: String(offset) })}`, { headers: headers(false) }),
  feedbackCorrection: (id: number, data: { expected_version: string; claim_key: string; proposal: string; evidence_url: string; evidence_excerpt: string; rationale: string; reviewed: true; create_followup: boolean }) => req<{ id: string; followup_opportunity_id: number | null; status: string }>(`/api/editorial/feedback/${id}/correction`, { method: "POST", headers: headers(), body: JSON.stringify(data) }),
  commentProgress: (id: string, parent_id?: string) => req<{ cursor: CommentCursorView; configured: boolean }>(`/api/editorial/comments/${id}${parent_id ? `?parent_id=${encodeURIComponent(parent_id)}` : ""}`, { headers: headers(false) }),
  commentAction: (id: string, action: "page" | "restart" | "stop", data: { expected_version: string; parent_id?: string; reviewed: true }) => req<{ cursor: CommentCursorView }>(`/api/editorial/comments/${id}/${action}`, { method: "POST", headers: headers(), body: JSON.stringify(data) }),
  editorialExperiment: (data: unknown) => req<unknown>("/api/editorial/experiments", { method: "POST", headers: headers(), body: JSON.stringify(data) }),
  nativeCampaigns: () => req<NativeCampaign[]>("/api/editorial/campaigns", { headers: headers(false) }),
  nativeCreate: (data: unknown) => req<NativeCampaign>("/api/editorial/campaigns", { method: "POST", headers: headers(), body: JSON.stringify(data) }),
  nativeRender: (cid: number, fmt: string) => req<NativeExport>(`/api/editorial/campaigns/${cid}/render/${fmt}`, { method: "POST", headers: headers() }),
  nativeApprove: (id: string) => req<NativeExport>(`/api/editorial/exports/${id}/approve`,
    { method: "POST", headers: headers(), body: JSON.stringify({ reviewed: true }) }),
  nativeEnqueue: (id: string, accounts: string[]) => req<{ queue_item_id: number; status: string }>(`/api/editorial/exports/${id}/enqueue`,
    { method: "POST", headers: headers(), body: JSON.stringify({ accounts }) }),
  nativeFile: (id: string, file: string) => withToken(`${apiBase()}/api/editorial/exports/${id}/file/${encodeURIComponent(file.split("/").pop() || "")}`),
  nativeManualDownload: async (id: string, file: string) => {
    const response = await fetch(`${apiBase()}/api/editorial/exports/${id}/manual/${encodeURIComponent(file.split("/").pop() || "")}`, { headers: headers(false) });
    if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(error.detail || "Manual export download failed"); }
    return response.blob();
  },
  contentSources: () => req<{ sources: ContentSource[] }>("/api/editorial/content-sources", { headers: headers(false) }),
  contentLineages: () => req<{ bindings: ContentBinding[]; versions: ContentBinding[]; total: number }>("/api/editorial/content-lineage", { headers: headers(false) }),
  bindContent: (data: ContentMappingDecision & { expected_current_id: string | null }) => req<ContentBinding>("/api/editorial/content-lineage", { method: "POST", headers: headers(), body: JSON.stringify(data) }),
  audienceStatus: () => req<AudienceAdapterStatus>("/api/editorial/audience/status", { headers: headers(false) }),
  audienceSync: (id: string) => req<{ status: string; reason?: string; through_day?: string; observed_at?: string; created: boolean }>(`/api/editorial/audience/${id}/sync`, { method: "POST", headers: headers(), body: JSON.stringify({ reviewed: true }) }),
  contentPreview: (source: ContentSource) => source.kind === "native_export" ? withToken(`${apiBase()}/api/editorial/exports/${source.id}/file/${encodeURIComponent(source.preview?.file?.split("/").pop() || "")}`) : source.kind === "export_version" ? withToken(`${apiBase()}/api/export-history/${source.id}/file/${source.preview?.role || "video"}`) : withToken(`${apiBase()}/api/delivery-history/${source.id}/file/${source.preview?.index || 0}`),
  assetSearch: (q: string, kind: string) => req<{ assets: CatalogAsset[]; total: number; method: string }>(`/api/editorial/assets/search?${new URLSearchParams({ q, ...(kind ? { kind } : {}) })}`, { headers: headers(false) }),
  assetMedia: (id: number) => withToken(`${apiBase()}/api/editorial/assets/${id}/media`),
  assetFrame: (id: number, index: number) => withToken(`${apiBase()}/api/editorial/assets/${id}/frame/${index}`),
  assetImport: (file: File, fields: Record<string, string>) => {
    const body = new FormData(); body.append("file", file); Object.entries(fields).forEach(([key, value]) => body.append(key, value));
    return req<IndexedAsset & { duplicate: boolean }>("/api/editorial/assets/media", { method: "POST", headers: headers(false), body });
  },
  nativeAssets: () => req<IndexedAsset[]>("/api/editorial/assets", { headers: headers(false) }),
  nativeAssetUpload: (file: File, license: string, attestation: string, tags: string) => {
    const body = new FormData(); body.append("file", file); body.append("license", license); body.append("attestation", attestation); body.append("tags", tags);
    return req<{ id: number; duplicate: boolean }>("/api/editorial/assets", { method: "POST", headers: headers(false), body });
  },
  editorialOpportunities: () => req<EditorialOpportunity[]>("/api/editorial/opportunities", { headers: headers(false) }),
  editorialSave: (data: unknown, id?: number) => req<EditorialOpportunity>(`/api/editorial/opportunities${id ? `/${id}` : ""}`,
    { method: id ? "PUT" : "POST", headers: headers(), body: JSON.stringify(data) }),
  editorialApprove: (id: number) => req<EditorialOpportunity>(`/api/editorial/opportunities/${id}/approve`, { method: "POST", headers: headers() }),
  editorialInsights: () => req<EditorialInsights>("/api/editorial/insights", { headers: headers(false) }),
  editorialObservation: (data: unknown) => req<{ key: string; created: boolean }>("/api/editorial/observations",
    { method: "POST", headers: headers(), body: JSON.stringify(data) }),
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

  queuePostNext: (id: number) =>
    req<{ id: number; position: number; scheduled_at: string | null }>(`/api/queue/${id}/post-next`, {
      method: "POST", headers: headers() }),
  queuePin: (id: number, pinnedAt: string) =>        // ISO with timezone; "" unpins
    req<QueueItem>(`/api/queue/${id}`, { method: "PATCH", headers: headers(), body: JSON.stringify({ pinned_at: pinnedAt }) }),
  queueThumbUrl: (id: number, v?: number | null) => withToken(`${apiBase()}/api/queue/${id}/thumb${v ? `?v=${v}` : ""}`),
  queueThumbOptions: (id: number) =>
    req<QueueThumbOptions>(`/api/queue/${id}/thumbnail/options`, { headers: headers(false) }),
  queueThumbStudio: (id: number, option: "poster" | "shot1" | "shot2" | "refresh") =>
    req<QueueThumbOptions>(`/api/queue/${id}/thumbnail/studio`, {
      method: "POST", headers: headers(), body: JSON.stringify({ option }) }),
  queueThumbUpload: async (id: number, file: File): Promise<QueueThumbOptions> => {
    const fd = new FormData();
    fd.append("file", file);
    const h: Record<string, string> = {};
    const t = token();
    if (t) h["x-access-token"] = t;
    const res = await fetch(`${apiBase()}/api/queue/${id}/thumbnail`, { method: "POST", headers: h, body: fd });
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    return res.json();
  },
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

  publicationEvidence: (id: number) => req<PublicationEvidenceView>(`/api/queue/${id}/publication-evidence`, { headers: headers(false) }),
  publicationDecision: (id: number, data: PublicationDecisionInput) => req<{ saved: number; duplicate: boolean }>(`/api/queue/${id}/publication-decisions`, { method: "POST", headers: headers(), body: JSON.stringify(data) }),
  publicationLedger: () => req<PublicationEvidenceView["events"]>("/api/queue/publication-ledger/export?limit=2000", { headers: headers(false) }),
  reconcileQueue: () => req<{ settled: number }>("/api/queue/reconcile", { method: "POST", headers: headers() }),
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

  updateSchedule: (cfg: Partial<ScheduleConfig> | { reset: true }) =>
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
  studioSources: (id: number) => req<{ review_version: string; context: Record<string, string>; claim_keys: string[]; reviews: { id: string; record_id: string; claim: string; claim_key: string; excerpt: string; verdict: string; rationale: string; conflicts: string[]; context: Record<string, string>; reviewed_at: string; resolved_review_ids: string[]; resolution_rationale: string | null }[]; records: { id: string; url: string; requested_url: string; text: string; sha256: string; retrieved_at: string }[] }>(`/api/studio/projects/${id}/source-records`, { headers: headers(false) }),
  studioFetchSource: (id: number, url: string) => req<{ id: string }>(`/api/studio/projects/${id}/source-records`, { method: "POST", headers: headers(), body: JSON.stringify({ url }) }),
  studioReviewClaim: (id: number, index: number, data: object) => req<StudioProject>(`/api/studio/projects/${id}/claims/${index}/review`, { method: "POST", headers: headers(), body: JSON.stringify(data) }),
  studioProjects: () => req<StudioProject[]>("/api/studio/projects", { headers: headers(false) }),
  studioProject: (id: number) => req<StudioProject>(`/api/studio/projects/${id}`, { headers: headers(false) }),
  studioCandidates: () => req<StudioCandidates>("/api/studio/candidates", { headers: headers(false) }),
  studioCandidatesRefresh: () =>
    req<{ started: boolean }>("/api/studio/candidates/refresh", { method: "POST", headers: headers() }),
  studioCandidateMark: (key: string, mark: "pin" | "skip" | null) =>
    req<StudioCandidates>(`/api/studio/candidates/${encodeURIComponent(key)}/mark`, {
      method: "PUT", headers: headers(), body: JSON.stringify({ mark }) }),
  studioAuto: () => req<StudioAuto>("/api/studio/auto", { headers: headers(false) }),
  studioAutoSet: (patch: { enabled?: boolean; movies_per_day?: number; tv_per_day?: number }) =>
    req<StudioAuto>("/api/studio/auto", { method: "PUT", headers: headers(), body: JSON.stringify(patch) }),
  studioResume: (id: number) =>
    req<{ ok: boolean; stage: string; until: string; state: "started" | "queued" }>(`/api/studio/projects/${id}/resume`, {
      method: "POST", headers: headers() }),
  studioMoveUp: (id: number) =>
    req<{ ok: boolean }>(`/api/studio/projects/${id}/move-up`, { method: "POST", headers: headers() }),
  studioAutoPauseToday: (paused: boolean) =>
    req<StudioAuto>("/api/studio/auto/pause-today", { method: "POST", headers: headers(), body: JSON.stringify({ paused }) }),
  studioAutoSkipNext: () =>
    req<StudioAuto & { skipped: string }>("/api/studio/auto/skip-next", { method: "POST", headers: headers() }),
  costsTodayExtra: (usd: number) =>
    req<CostSummary["today"]>("/api/costs/today-extra", { method: "POST", headers: headers(), body: JSON.stringify({ usd }) }),
  studioReview: (id: number, reviewed: boolean) =>
    req<StudioProject>(`/api/studio/projects/${id}/review`, {
      method: "PUT", headers: headers(), body: JSON.stringify({ reviewed }) }),
  studioRenderBatch: (ids: number[]) =>
    req<RenderBatch>("/api/studio/render-batch", { method: "POST", headers: headers(), body: JSON.stringify({ ids }) }),
  studioRenderBatchStatus: () => req<RenderBatch>("/api/studio/render-batch", { headers: headers(false) }),
  studioPublishBatch: (ids: number[]) =>
    req<{ sent: number; results: { id: number; title: string; ok: boolean; why?: string; queue_item_id?: number }[] }>(
      "/api/studio/publish-batch", { method: "POST", headers: headers(), body: JSON.stringify({ ids }) }),
  studioCreate: (t: { tmdb_id: number; media_type: string; title: string; target_minutes?: number; force?: boolean }) =>
    req<StudioProject>("/api/studio/projects", { method: "POST", headers: headers(), body: JSON.stringify(t) }),
  studioPatch: (id: number, data: { target_minutes?: number; script?: any; plan?: any; expected_script_version?: string }) =>
    req<StudioProject>(`/api/studio/projects/${id}`, { method: "PATCH", headers: headers(), body: JSON.stringify(data) }),
  studioDelete: (id: number, force = false) =>
    req<{ deleted: number }>(`/api/studio/projects/${id}${force ? "?force=true" : ""}`, { method: "DELETE", headers: headers(false) }),
  studioArchiveProject: (id: number, archived: boolean) =>
    req<StudioProject>(`/api/studio/projects/${id}/archive`, {
      method: "PUT", headers: headers(), body: JSON.stringify({ archived }) }),
  studioRun: (id: number, stage: string, auto = false, until: "script" | "plan" | "render" = "script") =>
    req<{ ok: boolean; state?: "started" | "queued" }>(`/api/studio/projects/${id}/run`, {
      method: "POST", headers: headers(), body: JSON.stringify({ stage, auto, until }) }),
  studioStop: (id: number) =>
    req<StudioProject>(`/api/studio/projects/${id}/stop`, {
      method: "POST", headers: headers() }),
  studioSelectThumbnail: (id: number, thumbId: string) =>
    req<StudioProject>(`/api/studio/projects/${id}/thumbnail/select`, {
      method: "POST", headers: headers(), body: JSON.stringify({ id: thumbId }) }),
  studioGenerateThumbnails: (id: number) =>
    req<StudioProject>(`/api/studio/projects/${id}/thumbnail/generate`, {
      method: "POST", headers: headers() }),
  exportAcceptanceView: (kind: "studio" | "countdown", id: number) => req<ExportAcceptanceView>(`${kind === "studio" ? "/api/studio/projects" : "/api/countdowns"}/${id}/export-review`, { headers: headers(false) }),
  approveReviewedExport: (kind: "studio" | "countdown", id: number, data: ExportAcceptanceRequest) => req<StudioProject | CountdownProject>(`${kind === "studio" ? "/api/studio/projects" : "/api/countdowns"}/${id}/approve-export`, { method: "POST", headers: headers(), body: JSON.stringify(data) }),
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
  studioSetShot: (id: number, shotId: string, usable: boolean) =>
    req<StudioProject>(`/api/studio/projects/${id}/shots/${encodeURIComponent(shotId)}`, {
      method: "PUT", headers: headers(), body: JSON.stringify({ usable }) }),
  studioArchive: () => req<StudioArchive>("/api/studio/archive", { headers: headers(false) }),
  setStudioArchive: (patch: { enabled?: boolean; days?: number }) =>
    req<StudioArchive>("/api/studio/archive", { method: "PUT", headers: headers(), body: JSON.stringify(patch) }),
  studioArchiveNow: () =>
    req<StudioArchive & { archived: { id: number; freed_mb: number }[] }>("/api/studio/archive/run", { method: "POST", headers: headers() }),
  studioRestore: (id: number) =>
    req<{ job_id: string }>(`/api/studio/projects/${id}/restore`, { method: "POST", headers: headers() }),
  studioMotion: () => req<StudioMotion>("/api/studio/motion", { headers: headers(false) }),
  setStudioMotion: (patch: Partial<Pick<StudioMotion, "mode" | "cast_cards">>) =>
    req<StudioMotion>("/api/studio/motion", { method: "PUT", headers: headers(), body: JSON.stringify(patch) }),
  studioFileUrl: (id: number, path: string, bust?: string) => {
    const q = new URLSearchParams({ path });
    if (bust) q.set("v", bust);
    return mediaUrl(`/api/studio/projects/${id}/file?${q.toString()}`);
  },

  // --- Countdown Compilations ---
  compilationThemes: () => req<CountdownTheme[]>("/api/studio/compilations/themes", { headers: headers(false) }),
  compilationCurate: (data: { theme_id?: string; custom_query?: string; media_type?: string; limit?: number }) =>
    req<CuratedCandidate[]>("/api/studio/compilations/curate", { method: "POST", headers: headers(), body: JSON.stringify(data) }),
  cinemetaTop: (media_type = "movie") =>
    req<CinemetaTitle[]>(`/api/studio/compilations/cinemeta/top?media_type=${media_type}`, { headers: headers(false) }),
  cinemetaMeta: (imdb_id: string, media_type = "movie") =>
    req<CinemetaTitle>(`/api/studio/compilations/cinemeta/meta?imdb_id=${encodeURIComponent(imdb_id)}&media_type=${media_type}`, { headers: headers(false) }),
  fmhyResources: () =>
    req<FmhyResource[]>("/api/studio/compilations/fmhy/resources", { headers: headers(false) }),

  // Top 5 & Top 10 Countdown Research Engine
  countdownTriggers: () =>
    req<CountdownTrigger[]>("/api/studio/compilations/triggers", { headers: headers(false) }),
  countdownTopics: (trigger?: string, refresh?: boolean) => {
    const params = new URLSearchParams();
    if (trigger && trigger !== "all") params.set("trigger", trigger);
    const q = params.toString() ? `?${params.toString()}` : "";
    return req<CountdownTopicCandidate[]>(`/api/studio/compilations/topics${refresh ? "/refresh" : ""}${q}`,
      { method: refresh ? "POST" : "GET", headers: headers(false) });
  },

  // --- Countdowns Studio (2026-10-12): a countdown is a project, like a breakdown ---
  countdowns: () => req<CountdownProject[]>("/api/countdowns", { headers: headers(false) }),
  countdown: (id: number) => req<CountdownProject>(`/api/countdowns/${id}`, { headers: headers(false) }),
  countdownCreate: (data: { topic: string; fmt: string; notes?: string }) =>
    req<CountdownProject>("/api/countdowns", { method: "POST", headers: headers(), body: JSON.stringify(data) }),
  countdownFromBreakdowns: (data: { topic: string; project_ids: number[] }) =>
    req<CountdownProject>("/api/countdowns/from-breakdowns", { method: "POST", headers: headers(), body: JSON.stringify(data) }),
  countdownResearch: (id: number) =>
    req<{ ok: boolean }>(`/api/countdowns/${id}/research`, { method: "POST", headers: headers(false) }),
  countdownEntries: (id: number, entries: CountdownEntryRow[], expected_ranking_version?: string) =>
    req<CountdownProject>(`/api/countdowns/${id}/entries`, { method: "PUT", headers: headers(), body: JSON.stringify({ entries, expected_ranking_version }) }),
  countdownReviewBookends: (id: number, expected_version: string) =>
    req<CountdownProject>(`/api/countdowns/${id}/review-bookends`, { method: "POST", headers: headers(), body: JSON.stringify({ reviewed: true, expected_version }) }),
  countdownConfirm: (id: number) =>
    req<CountdownProject>(`/api/countdowns/${id}/confirm`, { method: "POST", headers: headers(false) }),
  countdownMake: (id: number) =>
    req<CountdownProject>(`/api/countdowns/${id}/make`, { method: "POST", headers: headers(false) }),
  countdownStop: (id: number) =>
    req<CountdownProject>(`/api/countdowns/${id}/stop`, { method: "POST", headers: headers(false) }),
  countdownScript: (id: number, patch: { title?: string; intro?: string; outro?: string; expected_script_version?: string }) =>
    req<CountdownProject>(`/api/countdowns/${id}/script`, { method: "PUT", headers: headers(), body: JSON.stringify(patch) }),
  countdownRender: (id: number) =>
    req<{ ok: boolean; job_id: string }>(`/api/countdowns/${id}/render`, { method: "POST", headers: headers(false) }),
  countdownThumbnail: (id: number, option: string) =>
    req<CountdownProject>(`/api/countdowns/${id}/thumbnail`, { method: "POST", headers: headers(), body: JSON.stringify({ option }) }),
  countdownPublish: (id: number) =>
    req<{ queue_item_id: number; status: string }>(`/api/countdowns/${id}/publish`, { method: "POST", headers: headers(false) }),
  countdownReview: (id: number, on: boolean) =>
    req<CountdownProject>(`/api/countdowns/${id}/review`, { method: "PUT", headers: headers(), body: JSON.stringify({ on }) }),
  countdownArchive: (id: number, on: boolean) =>
    req<CountdownProject>(`/api/countdowns/${id}/archive`, { method: "PUT", headers: headers(), body: JSON.stringify({ on }) }),
  countdownDelete: (id: number) =>
    req<{ deleted: number }>(`/api/countdowns/${id}`, { method: "DELETE", headers: headers(false) }),
  countdownAuto: () => req<CountdownAuto>("/api/countdowns/auto", { headers: headers(false) }),
  countdownAutoSet: (patch: { enabled?: boolean; per_day?: number }) =>
    req<CountdownAuto>("/api/countdowns/auto", { method: "PUT", headers: headers(), body: JSON.stringify(patch) }),
  countdownAutoPauseToday: (on: boolean) =>
    req<CountdownAuto>("/api/countdowns/auto/pause-today", { method: "POST", headers: headers(), body: JSON.stringify({ on }) }),
  countdownAutoSkipNext: () =>
    req<CountdownAuto>("/api/countdowns/auto/skip-next", { method: "POST", headers: headers(false) }),
  countdownTopicList: () => req<CountdownTopics>("/api/countdowns/topics", { headers: headers(false) }),
  countdownLineAdd: (data: { topic: string; fmt: string; sig?: string; notes?: string }) =>
    req<CountdownAuto>("/api/countdowns/line", { method: "POST", headers: headers(), body: JSON.stringify(data) }),
  countdownLineRemove: (id: string) => req<CountdownAuto>(`/api/countdowns/line/${encodeURIComponent(id)}`, { method: "DELETE", headers: headers(false) }),
  countdownLineUp: (id: string) => req<CountdownAuto>(`/api/countdowns/line/${encodeURIComponent(id)}/up`, { method: "POST", headers: headers(false) }),
  countdownLineStart: (id: string) => req<CountdownProject>(`/api/countdowns/line/${encodeURIComponent(id)}/start`, { method: "POST", headers: headers(false) }),
  countdownTopicsRefresh: () =>
    req<{ started: boolean }>("/api/countdowns/topics/refresh", { method: "POST", headers: headers(false) }),
  countdownTopicMark: (sig: string, kind: "pinned" | "skipped", on: boolean) =>
    req<CountdownTopics>(`/api/countdowns/topics/${encodeURIComponent(sig)}/mark`, { method: "PUT", headers: headers(), body: JSON.stringify({ kind, on }) }),
  countdownTopicStart: (sig: string, fmt: string) =>
    req<CountdownProject>("/api/countdowns/topics/start", { method: "POST", headers: headers(), body: JSON.stringify({ sig, fmt }) }),
  countdownBatch: (action: string, ids: number[]) =>
    req<{ results: { id: number; title?: string; ok: boolean; why?: string }[]; done: number }>("/api/countdowns/batch",
      { method: "POST", headers: headers(), body: JSON.stringify({ action, ids }) }),
  countdownResume: (id: number) => req<CountdownProject>(`/api/countdowns/${id}/resume`, { method: "POST", headers: headers(false) }),
  countdownMoveUp: (id: number) => req<{ ok: boolean }>(`/api/countdowns/${id}/move-up`, { method: "POST", headers: headers(false) }),
  countdownSettings: (id: number, patch: { segment_minutes?: number }) =>
    req<CountdownProject>(`/api/countdowns/${id}/settings`, { method: "PUT", headers: headers(), body: JSON.stringify(patch) }),
  countdownDrive: (id: number) => req<{ job_id: string | null }>(`/api/countdowns/${id}/drive`, { method: "POST", headers: headers(false) }),
  countdownThumbRefresh: (id: number) =>
    req<CountdownProject>(`/api/countdowns/${id}/thumbnails/refresh`, { method: "POST", headers: headers(false) }),
  countdownScoreTitles: (id: number) =>
    req<CountdownProject>(`/api/countdowns/${id}/titles/score`, { method: "POST", headers: headers(false) }),
  countdownSuggestTitles: (id: number) =>
    req<CountdownProject>(`/api/countdowns/${id}/titles/suggest`, { method: "POST", headers: headers(false) }),
  vidiqStatus: () => req<VidIQStatus>("/api/vidiq/status", { headers: headers(false) }),
  countdownFileUrl: (id: number, path = "video", bust?: string | number) =>
    mediaUrl(`/api/countdowns/${id}/file?path=${encodeURIComponent(path)}${bust ? `&v=${bust}` : ""}`),

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
  financialHolds: () => req<FinancialHoldView>("/api/financial/holds", { headers: headers(false) }),
  resolveFinancialHold: (data: { request_key: string; kind: "usd" | "credit"; reservation_id: string; expected_version: string; actual: number; receipt: string; rationale: string; reviewed: true; confirm_interrupted: boolean }) => req<{ resolved: string }>("/api/financial/resolve", { method: "POST", headers: headers(), body: JSON.stringify(data) }),
  setCostSettings: (patch: Partial<CostSettings>) =>
    req<CostSummary>("/api/costs/settings", { method: "PUT", headers: headers(), body: JSON.stringify(patch) }),
  seo: () => req<{ auto: boolean; clips_without_seo: number }>("/api/seo", { headers: headers(false) }),
  setSeo: (auto: boolean) =>
    req<{ auto: boolean; clips_without_seo: number }>("/api/seo", { method: "PUT", headers: headers(), body: JSON.stringify({ auto }) }),
  studioSaveToDrive: (id: number) =>
    req<{ job_id: string }>(`/api/studio/projects/${id}/drive`, { method: "POST", headers: headers() }),
  setAutopost: (paused: boolean) =>
    req<{ paused: boolean }>("/api/autopost", { method: "PUT", headers: headers(), body: JSON.stringify({ paused }) }),

  archiveChannels: () => req<{ channels: ChannelArchive[] }>("/api/channel-archives", { headers: headers(false) }),
  inventoryArchive: (data: { url: string; parent_folder_id: string; formats: string[] }) => req<{ job_id: string }>("/api/channel-archives/inventory", { method: "POST", headers: headers(), body: JSON.stringify(data) }),
  runArchive: (id: string, retry_failures = false) => req<{ job_id: string }>(`/api/channel-archives/${encodeURIComponent(id)}/run`, { method: "POST", headers: headers(), body: JSON.stringify({ retry_failures }) }),
  handoffArchive: (id: string) => req<{ awaiting_worker: boolean }>(`/api/channel-archives/${encodeURIComponent(id)}/handoff`, { method: "POST", headers: headers() }),
  stopArchive: (id: string) => req<{ stopped: boolean }>(`/api/channel-archives/${encodeURIComponent(id)}/stop`, { method: "POST", headers: headers() }),
  archiveCapacity: (id: string) => req<ArchiveCapacity>(`/api/channel-archives/${encodeURIComponent(id)}/capacity`, { headers: headers(false) }),
  archiveManifest: (id: string) => req<ChannelArchive>(`/api/channel-archives/${encodeURIComponent(id)}/manifest`, { headers: headers(false) }),
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

  backupStatus: () => req<BackupStatus>("/api/backup/status", { headers: headers(false) }),

  backupNow: () => req<BackupNowResponse>("/api/backup/now", { method: "POST", headers: headers() }),

  diskStatus: () => req<DiskSystemStatus>("/api/system/disk", { headers: headers(false) }),

  runCleanup: () => req<CleanupResult>("/api/cleanup/now", { method: "POST", headers: headers() }),
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


export interface ChannelArchive {
  channel_id: string; title: string; handle: string | null; source_url: string;
  formats: string[]; status: string; error: string | null; drive_folder_id: string | null; manifest_id: string | null;
  inventory: { complete: boolean; revision: string; discovered: number; estimated_bytes: number; unknown_size_count: number; completed_at: string };
  counts: { discovered: number; downloaded: number; uploaded: number; skipped: number; failed: number; remaining: number; missing_from_latest_inventory: number };
  active_job: { id: string; status: string; message: string } | null; active_lease: boolean;
  uploaded_bytes: number; items: { video_id: string; title: string; status: string; error: string | null; drive_file_id: string | null; sha256: string | null }[];
}
export interface ArchiveCapacity {
  local_free_bytes: number; local_reserve_bytes: number; drive_limit_bytes: number | null;
  drive_usage_bytes: number; drive_available_bytes: number | null; shared_drive_id: string | null;
  estimate_known_bytes: number; estimate_unknown_count: number; quota_note: string | null;
}

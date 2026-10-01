"""Central configuration, sourced from environment variables.

Local dev needs zero setup: SQLite + a data/ dir under the repo. In production
(Railway) set DATABASE_URL (Postgres) and DATA_DIR (the mounted volume).
"""

from __future__ import annotations

import pathlib

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Storage ---------------------------------------------------------
    # DATA_DIR holds everything that must persist: the DB (in dev), downloads,
    # compilations, thumbnails, cookies, and the yt-dlp archive. On Railway this
    # is the mounted volume path.
    data_dir: str = str(REPO_ROOT / "data")

    # --- Database --------------------------------------------------------
    # Blank -> SQLite file inside DATA_DIR. Railway sets a postgres:// URL.
    database_url: str = ""

    # --- Auth ------------------------------------------------------------
    # Single shared token gating the whole API. Blank in local dev = open.
    access_token: str = ""

    # --- Reliability layers ---------------------------------------------
    auto_update_ytdlp: bool = True          # Layer 1: self-healing engine
    ytdlp_update_hour: int = 4              # nightly hour (local time) to update
    max_retries: int = 3                    # Layer 3: retries per URL
    retry_backoff_seconds: float = 5.0      # base for exponential backoff
    per_clip_delay_seconds: float = 2.0     # self-rate-limit between clips
    proxy_url: str = ""                     # Layer 4: yt-dlp --proxy (reserve)

    # YouTube PO (Proof-of-Origin) token — required by YouTube when downloading
    # from datacenter IPs (Railway). Without it, YouTube returns HTTP 403 or
    # "Sign in to confirm you're not a bot". Set YTDLP_PO_TOKEN in Railway env.
    # Guide: https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide
    ytdlp_po_token: str = ""

    # yt-dlp `-t sleep` preset: 0.75s between HTTP requests and a randomized
    # 10-20s pause between downloads. The single most effective anti-ban knob
    # on a headless server — keeps request patterns from looking automated.
    ytdlp_sleep_preset: bool = True

    # Read cookies straight from a locally-installed browser instead of an
    # uploaded cookies.txt (e.g. "chrome", "firefox", "safari", "edge", "brave").
    # Only works where a real browser profile exists — your Mac or a hybrid
    # worker — NOT on the headless Railway container. For the cloud, use
    # scripts/sync_cookies.py to push browser cookies up to /api/cookies.
    cookies_from_browser: str = ""

    # --- Retention (auto-delete) -----------------------------------------
    # Each clip/compilation gets its own rolling timer from its created_at, so
    # something scraped Thursday expires the following Tuesday. 0 disables.
    retention_days: int = 5
    retention_sweep_minutes: int = 30       # how often the worker sweeps

    # --- Worker ----------------------------------------------------------
    # "web" runs API + in-process worker (default, all-in-one).
    # "web_only" runs API but no worker (pair with a remote worker).
    # "worker_only" runs just the worker loop (Layer 5: hybrid home-IP box).
    worker_mode: str = "web"
    worker_poll_seconds: float = 2.0

    # --- Video encode ----------------------------------------------------
    video_encoder: str = "libx264"          # portable default (Railway has no VideoToolbox)
    video_bitrate: str = "6000k"
    encode_fps: int = 30

    # --- Observability ---------------------------------------------------
    alert_webhook_url: str = ""             # POSTed a JSON payload on failure spikes
    alert_failure_threshold: float = 0.5    # fraction of a source's recent jobs failing

    # --- Social Publishing (Upload-Post) --------------------------------
    # The only publisher. Also read from UPLOAD_POST_API_KEY / UPLOADPOST_KEY
    # (names used elsewhere); unset -> publishing is blocked with that reason.
    uploadpost_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("UPLOADPOST_API_KEY", "UPLOAD_POST_API_KEY", "UPLOADPOST_KEY"),
    )
    # Visibility for queue posts: public | unlisted | private.
    publish_privacy: str = "public"

    # --- Posting schedule (SocialPilot) ---------------------------------
    # Same cadence as socialpilot_Ai CONFIG.md: a slot every 2h from 8am to
    # 10pm Eastern; each slot posts the next Ready video of every pipeline.
    post_timezone: str = "America/New_York"
    post_start_hour: int = 8                # first slot (local hour, 0-23)
    post_end_hour: int = 22                 # last slot (inclusive)
    post_interval_hours: int = 2
    # Posted/archived queue items (and their Drive file, moved to trash) are
    # removed this many days after they were posted. 0 disables.
    archive_delete_days: int = 4

    # --- AI Metadata (Google Gemini) ------------------------------------
    gemini_api_key: str = ""
    # 2.5 is closed to new users; Google recommends 3.5 Flash-Lite / 3.8 Flash.
    gemini_model: str = "gemini-3.5-flash-lite"

    # --- CORS ------------------------------------------------------------
    cors_origins: str = "*"                 # comma-separated; set to the Vercel URL in prod

    @model_validator(mode="after")
    def _check_schedule(self) -> "Settings":
        from zoneinfo import ZoneInfo
        ZoneInfo(self.post_timezone)  # raises on an unknown zone
        if not (0 <= self.post_start_hour <= self.post_end_hour <= 23):
            raise ValueError("POST_START_HOUR/POST_END_HOUR must satisfy 0 <= start <= end <= 23")
        if self.post_interval_hours < 1:
            raise ValueError("POST_INTERVAL_HOURS must be >= 1")
        if self.archive_delete_days < 0:
            raise ValueError("ARCHIVE_DELETE_DAYS must be >= 0")
        if self.publish_privacy not in ("public", "unlisted", "private"):
            raise ValueError("PUBLISH_PRIVACY must be public, unlisted or private")
        return self

    # ---------------------------------------------------------------------
    @property
    def data_path(self) -> pathlib.Path:
        p = pathlib.Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def downloads_path(self) -> pathlib.Path:
        p = self.data_path / "downloads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def compilations_path(self) -> pathlib.Path:
        p = self.data_path / "compilations"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def thumbs_path(self) -> pathlib.Path:
        p = self.data_path / "thumbs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cookies_path(self) -> pathlib.Path:
        return self.data_path / "cookies.txt"

    @property
    def archive_path(self) -> pathlib.Path:
        return self.data_path / "archive.txt"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            # Railway hands out postgres://; SQLAlchemy wants postgresql://
            return self.database_url.replace("postgres://", "postgresql://", 1)
        return f"sqlite:///{self.data_path / 'scrapper.db'}"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()

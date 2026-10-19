/** Typed, bounded owner-facing errors. Never serialize validation inputs or HTML. */
export class ApiError extends Error {
  readonly status: number;
  readonly retryAfterSeconds: number | null;
  constructor(status: number, detail: string, retryAfterSeconds: number | null = null) {
    super(status ? `${status}: ${detail}` : detail);
    this.name = "ApiError"; this.status = status; this.retryAfterSeconds = retryAfterSeconds;
  }
}

const defaults: Record<number, string> = {
  401: "The session was not accepted. Check the access token under Connection.",
  403: "This action is not permitted for the current session.",
  409: "This item changed or is busy. Refresh its current state before trying again.",
  413: "The file or request exceeds the allowed size.",
  422: "Some supplied values are invalid. Review the form before trying again.",
  429: "Too many requests. Wait before retrying.",
};
const clean = (text: string) => text.replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 2000);

function retrySeconds(header: string | null, nowMs: number): number | null {
  if (!header) return null;
  if (/^\d+$/.test(header.trim())) {
    const seconds = Number(header); return Number.isSafeInteger(seconds) ? seconds : null;
  }
  if (!/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), .* GMT$/.test(header)) return null;
  const time = Date.parse(header);
  return Number.isFinite(time) ? Math.max(0, Math.ceil((time - nowMs) / 1000)) : null;
}

async function boundedBody(response: Response): Promise<string | null> {
  if (!response.body) return "";
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let text = ""; let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) return text + decoder.decode();
      size += value.byteLength;
      if (size > 16_384) { await reader.cancel(); return null; }
      text += decoder.decode(value, { stream: true });
    }
  } catch { return null; }
  finally { reader.releaseLock(); }
}

export async function apiError(response: Response, nowMs = Date.now()): Promise<ApiError> {
  let detail = defaults[response.status] || "The service could not complete this request. Check the item's current state before retrying.";
  const body = await boundedBody(response);
  if (body !== null) {
    try {
      const parsed = JSON.parse(body);
      if (typeof parsed?.detail === "string" && clean(parsed.detail)) detail = clean(parsed.detail);
      else if (Array.isArray(parsed?.detail)) {
        const messages = parsed.detail.slice(0, 5).flatMap((entry: unknown) => {
          if (!entry || typeof entry !== "object") return [];
          const item = entry as Record<string, unknown>;
          if (typeof item.msg !== "string") return [];
          const path = Array.isArray(item.loc) ? item.loc.filter(part => (typeof part === "string" || typeof part === "number") && !["body", "query"].includes(String(part))).map(String).join(".") : "";
          return [`${path ? clean(path) + ": " : ""}${clean(item.msg)}`];
        });
        if (messages.length) detail = messages.join("; ") + (parsed.detail.length > 5 ? "; additional fields need review" : "");
      }
    } catch { /* Raw proxy pages and malformed JSON are not useful owner messages. */ }
  }
  const retry = retrySeconds(response.headers.get("Retry-After"), nowMs);
  if (response.status === 429 && retry !== null && retry > 0) detail += ` Try again in ${retry} seconds.`;
  return new ApiError(response.status, detail.slice(0, 4000), retry);
}

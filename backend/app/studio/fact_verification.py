"""Independent source provenance plus explicit semantic review, with no paid AI.

Fetching proves what a page said at retrieval time. It does not prove the page
is correct, or that a quoted denial supports the proposition it mentions.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from ..db import SessionLocal
from ..models import ResearchClaimReview, ResearchSourceRecord, StudioProject
from ..publication_journal import identity
from . import sources

MAX_BYTES, MAX_REDIRECTS, MAX_AGE = 1_000_000, 3, 7 * 86400


def context(facts: dict) -> dict:
    return {"title": facts.get("title", ""), "year": str(facts.get("primary_date") or "")[:4],
            "medium": facts.get("media_type", ""), "territory": facts.get("region") or "US"}


def _destination(url: str) -> tuple[str, str, list[str]]:
    p = urlsplit(url)
    if (len(url) > 2048 or p.scheme != "https" or not p.hostname or p.username or p.password
            or p.port not in (None, 443) or p.fragment):
        raise ValueError("Source must be a plain HTTPS URL on port 443")
    host = p.hostname.encode("idna").decode()
    # No user-selected endpoints outside the editorial source allowlist.
    grounding = host == "vertexaisearch.cloud.google.com" and p.path.startswith("/grounding-api-redirect/")
    if sources.tier(host) not in ("official", "trusted") and not grounding:
        raise ValueError("Source host is not an approved editorial source")
    ips = list(dict.fromkeys(x[4][0] for x in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError("Source resolved to a non-public address")
    return host, (p.path or "/") + ("?" + p.query if p.query else ""), ips


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.skip = 0; self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"): self.skip += 1
    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"): self.skip = max(0, self.skip - 1)
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def fetch(url: str) -> dict:
    """Bounded uncompressed HTML/text; validate and pin every redirect's IP.

    TLS still checks the original hostname. No environment proxies, cookies,
    credentials, script execution or automatic content decompression.
    """
    from ..e2e_safety import deny_live
    deny_live("Independent research source retrieval")
    requested, deadline = url, time.monotonic() + 20
    for hop in range(MAX_REDIRECTS + 1):
        host, path, ips = _destination(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise ValueError("Source retrieval timed out")
        conn = http.client.HTTPSConnection(host, timeout=min(remaining, 5))
        try:
            raw = socket.create_connection((ips[0], 443), timeout=min(remaining, 5))
            try:
                conn.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
            except BaseException:
                raw.close(); raise
            conn.request("GET", path, headers={"Accept": "text/html,text/plain", "Accept-Encoding": "identity",
                                                "User-Agent": "ScreenCentralResearch/1.0"})
            res = conn.getresponse()
            if res.status in (301, 302, 303, 307, 308):
                if hop == MAX_REDIRECTS or not res.getheader("Location"): raise ValueError("Too many source redirects")
                url = urljoin(url, res.getheader("Location")); continue
            if res.status != 200: raise ValueError(f"Source returned HTTP {res.status}")
            mime = res.getheader("Content-Type", "").split(";")[0].lower()
            if mime not in ("text/html", "text/plain") or res.getheader("Content-Encoding", "identity") != "identity":
                raise ValueError("Source must return uncompressed HTML or plain text")
            chunks, size = [], 0
            while True:
                if time.monotonic() >= deadline: raise ValueError("Source retrieval timed out")
                chunk = res.read(min(16384, MAX_BYTES + 1 - size))
                if not chunk: break
                chunks.append(chunk); size += len(chunk)
                if size > MAX_BYTES: raise ValueError("Source exceeds the 1 MB limit")
            text = b"".join(chunks).decode("utf-8", errors="replace")
            if mime == "text/html":
                parser = _Text(); parser.feed(text); text = " ".join(parser.parts)
            text = " ".join(text.split())
            if len(text) < 40: raise ValueError("Source has too little readable text")
            if sources.tier(url) not in ("official", "trusted"): raise ValueError("Source did not resolve to an editorial host")
            return {"url": url, "requested_url": requested, "text": text,
                    "sha256": hashlib.sha256(text.encode()).hexdigest()}
        finally:
            conn.close()
    raise ValueError("Source retrieval failed")


def save_record(s, project_id: int, url: str) -> ResearchSourceRecord:
    project = s.get(StudioProject, project_id)
    if not project:
        raise ValueError("Research project no longer exists")
    project_identity = identity(project)
    data = fetch(url)
    row = ResearchSourceRecord(id=str(uuid.uuid4()), project_id=project_id,
                               project_identity=project_identity, **data)
    s.add(row); s.flush()
    return row


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def claim_key(text: str) -> str:
    """Keep case/punctuation edits from hiding an earlier review of these words.

    This is lexical identity, not a claim of semantic equivalence of paraphrases.
    """
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def conflicting_reviews(s, project, claim: str, facts: dict) -> list[ResearchClaimReview]:
    return [r for r in s.query(ResearchClaimReview).filter_by(
        project_id=project.id, project_identity=identity(project)).all()
        if claim_key(r.claim) == claim_key(claim) and r.context == context(facts)
        and (r.verdict in ("unresolved", "contradicted") or r.conflicts)]


def fresh(record) -> bool:
    return 0 <= (datetime.now(timezone.utc) - _aware(record.retrieved_at)).total_seconds() <= MAX_AGE


def supported(claim: dict, source_list: list, facts: dict, project_id: int | None) -> bool:
    evidence = claim.get("evidence") or {}
    if not project_id or not isinstance(evidence, dict) or not isinstance(evidence.get("review_id"), str): return False
    with SessionLocal() as s:
        project = s.get(StudioProject, project_id)
        if not project or context(project.facts or {}) != context(facts):
            return False
        project_identity = identity(project)
        review = s.get(ResearchClaimReview, evidence["review_id"])
        if (not review or review.project_id != project_id or review.project_identity != project_identity
                or review.claim != claim.get("text")): return False
        record = s.get(ResearchSourceRecord, review.record_id)
        if (not record or record.project_id != project_id or record.project_identity != project_identity
                or evidence.get("record_sha256") != record.sha256
                or review.verdict != "supported" or review.conflicts
                or review.context != context(facts) or not review.rationale.strip() or review.excerpt not in record.text
                or record.sha256 != hashlib.sha256(record.text.encode()).hexdigest()
                or not fresh(record)): return False
        conflicts = conflicting_reviews(s, project, review.claim, facts)
        if conflicts and (not {r.id for r in conflicts}.issubset(set(review.resolved_review_ids or []))
                or len((review.resolution_rationale or "").strip()) < 20):
            return False
        refs = claim.get("sources")
        return isinstance(refs, list) and any(type(i) is int and 0 <= i < len(source_list)
            and record.requested_url == source_list[i].get("url") for i in refs)


def invalidate(p):
    from .research_gate import invalidate as invalidate_script
    p.script = invalidate_script(p.script, "Source evidence changed; verify the saved narration again")
    p.plan, p.render = None, None
    p.review = {k: v for k, v in (p.review or {}).items() if k != "export_approval"}

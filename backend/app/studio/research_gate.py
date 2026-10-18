"""Fail-closed storyboard checks. A model verdict is not independent verification.

These guards validate coverage, references and known structured contradictions.
They deliberately do not claim that a URL or a second model proves a fact.
"""
from __future__ import annotations

import re


def refs_for(board: dict) -> set[str]:
    return {f"{kind}{i + 1}" for kind in ("F", "R", "T", "C")
            for i in range(len(board.get(kind) or []))}


def checked_sentences(sentences: list[dict], verdicts, board: dict) -> tuple[list[dict], list[str]]:
    valid = refs_for(board)
    issues, indexed = [], {}
    if not isinstance(verdicts, list):
        verdicts = []
        issues.append("Fact checker returned a non-array response")
    malformed = False
    for v in verdicts:
        if (not isinstance(v, dict) or type(v.get("i")) is not int
                or not 1 <= v["i"] <= len(sentences) or v["i"] in indexed
                or v.get("verdict") not in ("ok", "unsupported")):
            malformed = True
            continue
        indexed[v["i"]] = v
    if malformed:
        issues.append("Fact checker returned duplicate, malformed or out-of-range verdicts")
    out = []
    for i, sentence in enumerate(sentences, 1):
        v = indexed.get(i, {})
        kind = v.get("kind", "fact")
        refs = v.get("refs", sentence.get("refs", []))
        reason = None
        if not v or malformed:
            reason = "Missing or malformed fact-check verdict; check this sentence again"
        elif v.get("verdict") != "ok":
            reason = str(v.get("reason") or "Unsupported by the storyboard")
        elif kind not in ("fact", "opinion", "interpretation", "transition", "cta"):
            reason = "Unknown claim type"
        elif (not isinstance(refs, list) or any(not isinstance(r, str) or r not in valid for r in refs)
              or (kind in ("fact", "interpretation") and not refs)):
            reason = "Missing or invalid storyboard references"
        if reason:
            issues.append(f"Sentence {i}: {reason}")
        out.append({**sentence, "i": i, "kind": kind, "refs": refs if isinstance(refs, list) else [],
                    "check": "unchecked" if reason else "ok", "check_reason": reason or "Storyboard checked; not independently verified"})
    return out, issues


def structured_conflicts(sentences: list[dict], facts: dict) -> list[str]:
    """Catch explicit actor/role swaps even if the model approves them."""
    conflicts = []
    for i, sentence in enumerate(sentences, 1):
        text = sentence.get("text", "")
        for cast in facts.get("cast", []):
            actor, character = cast.get("actor"), cast.get("character")
            if not actor or not character:
                continue
            match = re.search(re.escape(actor) + r"\s+(?:plays|portrays|stars as|as)\s+([^.,;!?]+)", text, re.I)
            if match and character.casefold() not in match[1].casefold():
                conflicts.append(f"Sentence {i}: {actor} role conflicts with structured credit ({character})")
    return conflicts


def script_issues(script: dict | None) -> list[str]:
    if not script or not script.get("sentences"):
        return ["Write and check the script first"]
    issues = list(script.get("check_issues") or [])
    for i, sentence in enumerate(script["sentences"], 1):
        if sentence.get("check") != "ok":
            issues.append(f"Sentence {i} needs a storyboard check")
    return list(dict.fromkeys(issues))


def require_checked(script: dict | None) -> None:
    issues = script_issues(script)
    if issues:
        raise RuntimeError("Research blocked: " + "; ".join(issues[:5]) + ". Review sources and run Check saved script.")


def invalidate(script: dict | None, reason: str) -> dict | None:
    if not script:
        return script
    return {**script, "sentences": [{**x, "check": "unchecked"} for x in script.get("sentences", [])],
            "check_issues": [reason]}

"""Fail-closed storyboard checks. A model verdict is not independent verification.

These guards validate coverage, references and known structured contradictions.
They deliberately do not claim that a URL or a second model proves a fact.
"""
from __future__ import annotations

import re
import unicodedata


def _name(value):
    return ' '.join(unicodedata.normalize('NFKC',str(value)).casefold().replace('’',"'").split()).strip(' .')


def _roles(value):
    # Parenthetical performance labels do not change the character identity.
    return {_name(re.sub(r'\s*\((?:voice|uncredited|archive footage)\)\s*','',v,flags=re.I))
            for v in re.split(r'\s*/\s*|\s+aka\s+',value,flags=re.I) if v.strip()}


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
    """Typed relational checks independent of the model's storyboard verdict.

    This catches explicit credit/date contradictions. Free prose is still not
    an independently established truth just because it escapes these patterns.
    """
    conflicts = []
    writers={_name(c.get('name')) for c in facts.get('writing_credits',[])
             if c.get('job') in ('Screenplay','Writer','Teleplay')}
    credited={_name(c.get('name')):c.get('job') for c in facts.get('writing_credits',[])}
    for i, sentence in enumerate(sentences, 1):
        text = sentence.get("text", "")
        for cast in facts.get("cast", []):
            actor, character = cast.get("actor"), cast.get("character")
            if not actor or not character:
                continue
            match = re.search(re.escape(actor) + r"\s+(?:plays|portrays|stars as|as)\s+([^.,;!?]+)", text, re.I)
            role = re.split(r'\s+(?:and|while|who|in the)\b',match[1],flags=re.I)[0] if match else ''
            if match and not (_roles(role)&_roles(character)):
                conflicts.append(f"Sentence {i}: {actor} role conflicts with structured credit ({character})")
            # Reversing the grammar must not bypass the same binding.
            for known in _roles(character):
                reverse=re.search(re.escape(known)+r'\s+(?:is\s+)?(?:played|portrayed|voiced)\s+by\s+([^.,;!?]+)',text,re.I)
                if reverse:
                    named=re.split(r'\s+(?:and|while|who|in the)\b',reverse[1],flags=re.I)[0]
                    allowed={_name(c.get('actor')) for c in facts.get('cast',[]) if known in _roles(c.get('character') or '')}
                    if _name(named) not in allowed:
                        conflicts.append(f'Sentence {i}: {character} performer conflicts with structured cast credit')
        for credit in facts.get('writing_credits',[]):
            person=credit.get('name') or ''
            if not person or _name(person) in writers:continue
            name=re.escape(person)
            screenplay=(re.search(r'(?:screenplay|teleplay|screenwriter|script)\s+(?:(?:was|is)\s+)?(?:written\s+)?by\s+'+name,text,re.I)
                        or re.search(name+r'\s+(?:wrote|writes|penned|scripted)\s+(?:the\s+)?(?:screenplay|teleplay|script|movie|film)\b',text,re.I))
            ambiguous=re.search(r'(?:written|scripted)\s+by\s+'+name,text,re.I)
            original=bool(ambiguous and re.search(r'\b(novel|book|source material|original story)\b',text[max(0,ambiguous.start()-35):ambiguous.start()],re.I))
            if screenplay or (ambiguous and not original):
                conflicts.append(f'Sentence {i}: {person} is credited for {credited[_name(person)]}, not screenplay writing')
        title=facts.get('title') or ''
        dates={str(facts.get('primary_date') or '')[:4],*[str(r.get('date') or '')[:4] for r in facts.get('releases',[])]}-{''}
        if title and dates:
            match=re.search(re.escape(title)+r'\s+(?:(?:was|is|will be|first)\s+)*(?:released|premiered|debuted|opens)\s+(?:in\s+)?(\d{4})\b',text,re.I)
            if match and match[1] not in dates:
                conflicts.append(f'Sentence {i}: stated release year conflicts with the dated provider record')
    return list(dict.fromkeys(conflicts))


def script_issues(script: dict | None) -> list[str]:
    if not script or not script.get("sentences"):
        return ["Write and check the script first"]
    issues = list(script.get("check_issues") or [])
    for i, sentence in enumerate(script["sentences"], 1):
        if sentence.get("check") != "ok":
            issues.append(f"Sentence {i} needs a storyboard check")
    board = script.get("storyboard") or {}
    if board.get("R"):
        from .sources import usable
        for i, finding in enumerate(board["R"], 1):
            refs = finding.get("sources") or []
            claim = {**finding, "sources": list(range(len(refs)))}
            if not usable(claim, refs, facts=board.get("identity") or {}, project_id=board.get("project_id")):
                issues.append(f"Web finding R{i} evidence expired or no longer matches; review sources again")
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

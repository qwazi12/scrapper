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


def _credit_title(value, facts):
    """Only an explicit canonical title/year can qualify a known credit name."""
    titles={_name(facts.get(key) or '') for key in ('title','original_title')}-{''}
    if value in titles:return True
    dated=re.fullmatch(r'(.+?)\s+\((\d{4})\)',value)
    if dated:
        if str(facts.get('primary_date') or '')[:4]!=dated[2]:return False
        value=dated[1]
    if value in titles:return True
    prefix=re.match(r'^(?:the\s+)?(film|movie|tv series|series|show)\s+(.+)$',value)
    if not prefix:return False
    medium='movie' if prefix[1] in ('film','movie') else 'tv'
    if facts.get('media_type') in ('movie','tv') and facts['media_type']!=medium:return False
    return prefix[2] in titles


def _credit_value(raw, allowed, facts):
    # Try complete known names first: "The Man in the Wall" and "Dr. Original"
    # must not be truncated by a generic prose/period splitter. This is bounded
    # credit grammar, not semantic verification of the rest of the sentence.
    value=_name(re.sub(r'\s*\((?:voice|uncredited|archive footage)\)\s*','',raw,flags=re.I))
    def matches(candidate):
        for name in allowed:
            if candidate==name:return True
            if not candidate.startswith(name+' '):continue
            tail=candidate[len(name):].strip()
            context=re.fullmatch(r'(?:in|from)\s+(.+)',tail)
            if context and _credit_title(context[1],facts):return True
            # Preserve the existing handling of following narrative clauses;
            # their factual coverage remains the separate storyboard gate.
            if re.match(r'^(?:and|while|who)\b',tail):return True
        return False
    if matches(value):return True
    # "stars as a lonely fisherwoman": an article plus up to three describing words before the
    # credited role (owner, 2026-10-19). The role itself must still be the credited one.
    described=re.match(r"^(?:a|an|the)\s+(.+)$",value)
    if described:
        words=described[1].split()
        for k in range(min(3,len(words)-1)+1):
            if matches(' '.join(words[k:])):return True
    alternatives=_roles(raw)
    return len(alternatives)>1 and all(matches(part) for part in alternatives)


def _forward_performer_conflicts(text, facts):
    """A known role also constrains the subject of a forward credit claim."""
    roles={}
    for credit in facts.get('cast',[]):
        if not credit.get('actor') or not credit.get('character'):continue
        for role in _roles(credit['character']):roles.setdefault(role,set()).add(_name(credit['actor']))
    conflicts=[]
    for claim in re.finditer(r'(?:^|[,;!?])\s*([^,;!?\n]+?)\s+(?:plays|portrays|stars as)\s+([^,;!?\n]+)',text,re.I):
        offered=_name(claim[2])
        # Prefer the complete longer credit (e.g. "The Traveler Junior") before
        # treating a suffix as prose qualifying the shorter role.
        role=next((name for name in sorted(roles,key=len,reverse=True)
            if offered==name or offered.startswith(name+' ') or offered.startswith(name+'/')),None)
        if role is None:continue
        subject=_name(claim[1])
        subject=re.sub(r'^(?:(?:the|an|a)\s+)?(?:actor|actress|performer)\s+','',subject)
        allowed=roles[role]
        valid=subject in allowed
        if not valid:
            for actor in allowed:
                if subject.endswith(' '+actor):
                    prefix=subject[:-len(actor)].strip()
                    if prefix.startswith('in ') and _credit_title(prefix[3:],facts):valid=True;break
        if not valid:conflicts.append(f'Claimed performer for {role} is not supported by the structured cast relationship')
    return conflicts


def _writing_value(raw, allowed, facts):
    """Complete credited people, including every explicitly joined writer."""
    value=_name(raw)
    for person in sorted(allowed,key=len,reverse=True):
        if value==person:return True
        if not value.startswith(person+' ') and not value.startswith(person+','):continue
        tail=value[len(person):].strip()
        context=re.fullmatch(r'(?:in|for|of)\s+(.+)',tail)
        if context and _credit_title(context[1],facts):return True
        joined=re.match(r'^(?:,\s*(?:and\s+)?|and\s+|&\s+)(.+)$',tail)
        if joined and _writing_value(joined[1],allowed,facts):return True
        if re.match(r'^(?:while|who)\b',tail):return True
    return False


def _writing_conflicts(text, facts):
    writers={_name(row.get('name') or '') for row in facts.get('writing_credits',[])
        if row.get('job') in ('Screenplay','Writer','Teleplay')}-{''}
    conflicts=[]
    for claim in re.finditer(r'\b(screenplay|teleplay|script|movie|film)\s+(?:(?:was|is)\s+)?'
            r'(?:(?:co-)?written\s+|penned\s+|scripted\s+)?by\s+([^;!?\n]+)',text,re.I):
        if not _writing_value(claim[2],writers,facts):
            conflicts.append('Claimed screenplay writer/title context is not supported by structured writing credits')
    for claim in re.finditer(r'(?:^|[;!?])\s*([^;!?\n]+?)\s+(?:co-wrote|wrote|writes|penned|scripted)\s+'
            r'(?:the\s+)?(?:screenplay|teleplay|script|movie|film)\b([^;!?\n]*)',text,re.I):
        people=re.sub(r'^(?:(?:the|a|an)\s+)?(?:screenwriter|writer)\s+','',claim[1],flags=re.I)
        prefix=re.match(r'^in\s+(.+?),\s*(.+)$',people,re.I)
        prefix_valid=True
        if prefix:
            prefix_valid=_credit_title(_name(prefix[1]),facts);people=prefix[2]
        context=_name(claim[2])
        valid_context=not context or bool(re.match(r'^,?\s*(?:while|who)\b',context))
        qualifier=re.fullmatch(r'(?:in|for|of)\s+(.+)',context)
        if qualifier:valid_context=_credit_title(qualifier[1],facts)
        if not prefix_valid or not _writing_value(people,writers,facts) or not valid_context:
            conflicts.append('Claimed screenplay writer/title context is not supported by structured writing credits')
    return conflicts


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
        conflicts.extend(f'Sentence {i}: {reason}' for reason in _forward_performer_conflicts(text,facts))
        conflicts.extend(f'Sentence {i}: {reason}' for reason in _writing_conflicts(text,facts))
        for cast in facts.get("cast", []):
            actor, character = cast.get("actor"), cast.get("character")
            if not actor or not character:
                continue
            match = re.search(re.escape(actor) + r"\s+(?:plays|portrays|stars as|as)\s+([^,;!?]+)", text, re.I)
            if match and not _credit_value(match[1],_roles(character),facts):
                conflicts.append(f"Sentence {i}: {actor} role/title context conflicts with structured credit ({character})")
            # Reversing the grammar must not bypass the same binding.
            for known in _roles(character):
                reverse=re.search(re.escape(known)+r'\s+(?:is\s+)?(?:played|portrayed|voiced)\s+by\s+([^,;!?]+)',text,re.I)
                if reverse:
                    allowed={_name(c.get('actor')) for c in facts.get('cast',[]) if known in _roles(c.get('character') or '')}
                    if not _credit_value(reverse[1],allowed,facts):
                        conflicts.append(f'Sentence {i}: {character} performer/title context conflicts with structured cast credit')
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


def script_issues(script: dict | None, facts: dict | None) -> list[str]:
    if not script or not script.get("sentences"):
        return ["Write and check the script first"]
    issues = [*(script.get("check_issues") or []), *structured_conflicts(script["sentences"],facts or {})]
    for i, sentence in enumerate(script["sentences"], 1):
        if sentence.get("check") != "ok":
            issues.append(f"Sentence {i} needs a storyboard check")
    board = script.get("storyboard") or {}
    if board.get("R"):
        from .sources import usable
        for i, finding in enumerate(board["R"], 1):
            # Owner (2026-10-19): an unreviewed web finding doesn't stop the video; a finding the owner
            # reviewed must still hold (stale or contradicted reviewed evidence still blocks).
            evidence = finding.get("evidence")
            if not (isinstance(evidence, dict) and evidence.get("review_id")):
                continue
            refs = finding.get("sources") or []
            claim = {**finding, "sources": list(range(len(refs)))}
            if not usable(claim, refs, facts=board.get("identity") or {}, project_id=board.get("project_id")):
                issues.append(f"Web finding R{i} evidence expired or no longer matches; review sources again")
    return list(dict.fromkeys(issues))


def require_checked(script: dict | None, facts: dict | None) -> None:
    issues = script_issues(script,facts)
    if issues:
        raise RuntimeError("Research blocked: " + "; ".join(issues[:5]) + ". Review sources and run Check saved script.")


def invalidate(script: dict | None, reason: str) -> dict | None:
    if not script:
        return script
    return {**script, "sentences": [{**x, "check": "unchecked"} for x in script.get("sentences", [])],
            "check_issues": [reason]}


# Owner (2026-10-19): one failing sentence must not stop a whole breakdown. Sentences that fail the
# storyboard or credit checks are dropped and kept for review; the rest carries on. Too many failures
# (more than 40%) still stop it — that script needs a person.
MIN_KEEP = 0.6
_SENTENCE = re.compile(r"Sentence (\d+): (.*)")


def drop_failing(sentences: list[dict], issues: list[str]) -> tuple[list[dict], list[dict], list[str]]:
    reasons: dict[int, str] = {}
    for issue in issues:
        m = _SENTENCE.match(issue)
        if m:
            reasons.setdefault(int(m[1]), m[2])
    bad = set(reasons) | {i for i, s in enumerate(sentences, 1) if s.get("check") != "ok"}
    if not bad:
        return sentences, [], issues
    keep = [s for i, s in enumerate(sentences, 1) if i not in bad]
    if len(keep) < max(1, MIN_KEEP * len(sentences)):
        return sentences, [], [*issues, f"{len(bad)} of {len(sentences)} sentences failed the checks — too many to drop; review the script"]
    dropped = [{"text": s["text"], "reason": reasons.get(i) or s.get("check_reason") or "failed the check"}
               for i, s in enumerate(sentences, 1) if i in bad]
    return keep, dropped, [x for x in issues if not _SENTENCE.match(x)]

"""Deterministic queue copy. Public hashtags and backend phrase tags are distinct."""
import json
import re
import unicodedata
from .platform_payloads import video_fields,weight_upper_bound

HASHTAG=re.compile(r'#[\w]+',re.UNICODE)
ICK_REQUIRED={'#tonightsconversation','#theickroom'}


def tags(raw):
    if not raw:return {'public_hashtags':[],'phrase_tags':[],'encoding':'empty'}
    value=raw
    if isinstance(raw,str) and raw.lstrip().startswith(('[','{')):
        try:value=json.loads(raw)
        except ValueError:raise ValueError('Malformed structured tags; use public_hashtags and phrase_tags arrays')
    if isinstance(value,dict):
        if set(value)-{'public_hashtags','phrase_tags'}:raise ValueError('Unknown structured tag field')
        public=value.get('public_hashtags',[]);phrases=value.get('phrase_tags',[]);encoding='structured'
    elif isinstance(value,list):public=value;phrases=[];encoding='structured_public_hashtags'
    elif isinstance(value,str):public=value.split();phrases=[];encoding='legacy_explicit_hashtags'
    else:raise ValueError('Tags require explicit hashtags or structured public_hashtags/phrase_tags')
    if (not isinstance(public,list) or not isinstance(phrases,list)
            or any(not isinstance(t,str) or not HASHTAG.fullmatch(unicodedata.normalize('NFC',t)) for t in public)
            or any(not isinstance(t,str) or not t.strip() or '#' in t or any(ord(c)<32 for c in t) for t in phrases)):
        raise ValueError('Ambiguous legacy/phrase tags: supply explicit #hashtags or structured public_hashtags and phrase_tags; phrases are never split')
    return {'public_hashtags':public,'phrase_tags':phrases,'encoding':encoding}


def x_text(title,description,raw_tags):
    public=tags(raw_tags)['public_hashtags']
    # Never rewrite reviewed title/description. Append only hashtags absent
    # there, keeping the first exact supplied spelling; compare in NFC/casefold.
    key=lambda t:unicodedata.normalize('NFC',t).casefold()
    seen={key(t) for t in HASHTAG.findall(unicodedata.normalize('NFC','\n'.join((title,description))))}
    missing=[]
    for token in public:
        if key(token) not in seen:missing.append(token);seen.add(key(token))
    return '\n\n'.join(x for x in (title,description,' '.join(missing)) if x),missing


# Plan v2.1 X rule: 1–2 hashtags. Generic tags go first when trimming (least specific first).
X_SHORT_TAGS = 2
GENERIC_TAGS = {'#shorts','#short','#movieclips','#recap','#viral','#fyp','#foryou','#trending','#movie','#movies','#film'}


def x_short_tags(public, limit=X_SHORT_TAGS):
    """The hashtags a short X post keeps: specific ones first, in their order, then generic ones."""
    keep=[t for t in public if t.casefold() not in GENERIC_TAGS]+[t for t in public if t.casefold() in GENERIC_TAGS]
    return [t for t in public if t in keep[:limit]]


def preview(title,description,raw_tags,platforms,*,ick=False,x_variant_text=None):
    parts=tags(raw_tags)
    public=parts['public_hashtags']
    text='\n\n'.join(x for x in (title,description,' '.join(public)) if x)
    present={x.casefold() for x in HASHTAG.findall(text)}
    if ick and not ICK_REQUIRED<=present:
        raise ValueError('ICK copy must retain #tonightsconversation and #theickroom plus all reviewed public hashtags')
    # Preserve historical Facebook copy byte-for-byte for explicit legacy tags.
    caption='\n\n'.join(x.strip() for x in (description,raw_tags if isinstance(raw_tags,str) and parts['encoding'].startswith('legacy') else ' '.join(public)) if x and x.strip())
    exact_x,missing=x_text(title,description,raw_tags)
    common_weight=weight_upper_bound(exact_x)
    if x_variant_text is not None:
        # An explicitly reviewed ICK X-only edit can omit optional Facebook
        # hashtags. Both brand tags remain mandatory; common/FB copy stays exact.
        required=ICK_REQUIRED if ick else {unicodedata.normalize('NFC',t).casefold() for t in HASHTAG.findall(unicodedata.normalize('NFC',exact_x))}
        actual={unicodedata.normalize('NFC',t).casefold() for t in HASHTAG.findall(unicodedata.normalize('NFC',x_variant_text))}
        if not required<=actual:raise ValueError('Reviewed X variant drops mandatory public hashtags')
        exact_x=x_variant_text
    short_x=False
    if x_variant_text is None and not ick and common_weight>280:
        # Owner 2026-11-03: Screen Central X over 280 posts title + hashtags (max 2, plan v2.1); other platforms keep full copy.
        short='\n\n'.join(x for x in (title,' '.join(x_short_tags(public))) if x)
        if weight_upper_bound(short)<=280:exact_x,short_x=short,True
    fields=video_fields(title,caption,platforms,{'x_title':exact_x} if set(platforms)&{'x','twitter'} else None)
    return {'values':fields,'tags':parts,'x_appended_hashtags':missing,'x_body_unchanged':x_variant_text is None and not short_x,'x_short_form':short_x,
        'x_variant_selected':x_variant_text is not None,'x_common_weight_upper_bound':common_weight,'x_weight_upper_bound':weight_upper_bound(exact_x),
        'x_limit':280,'variant_policy':'Over-budget or unsupported copy needs a separately reviewed exact variant; no truncation or threading'}

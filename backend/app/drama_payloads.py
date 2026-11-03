"""Explicit visible copy; reject transformations instead of truncating approval.

Contracts: docs.upload-post.com/api/upload-video/ and /resources/character-limits/;
X weighting ranges: github.com/twitter/twitter-text/blob/master/config/v3.json.
Unknown long-post/video account entitlements never expand these baseline limits.
"""
import re
import unicodedata

from . import pending_metadata as pm

_URL = re.compile(r'(?i)(?:\b(?:https?|ftp|wss?)://|\bwww\.|\b(?:[a-z0-9-]+\.)+[a-z]{2,}/|\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?/)')


def weight_upper_bound(text):
    # Without emoji-cluster parsing this intentionally overcounts some composed
    # emoji. It never undercounts them or treats CJK as one standard character.
    ranges = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
    return sum(1 if any(a <= ord(c) <= b for a, b in ranges) else 2
               for c in unicodedata.normalize('NFC', text))


def build(item, evidence, review):
    description = '\n\n'.join(value for value in (item.description, item.tags) if value)
    x_text = '\n\n'.join(value for value in (item.title, description) if value)
    errors = []
    variant = review.get('x_variant')
    if variant:
        if variant['approved_sha256'] != pm.digest(variant['text']):
            errors.append('X variant differs from its exact approved hash')
        source_tags = {t.casefold() for t in re.findall(r'#\w+', x_text)}
        if not source_tags <= {t.casefold() for t in re.findall(r'#\w+', variant['text'])}:
            errors.append('X variant drops public hashtags from the approved common copy')
        x_text = variant['text']
    if weight_upper_bound(x_text) > 280:
        errors.append('X visible copy exceeds the supported 280-weight baseline; supply a separately approved exact variant or verified entitlement support')
    if _URL.search(x_text):
        errors.append('X provider may strip an approved URL; an exact approved platform variant is required')
    if any(unicodedata.category(c) == 'Cc' and c not in '\n\r\t' for c in x_text):
        errors.append('X visible copy contains unsupported control characters')
    if len(item.title.encode('utf-16-le')) // 2 > 255 or len(description.encode('utf-16-le')) // 2 > 63206:
        errors.append('Facebook visible title/description exceeds the supported exact-copy bounds')
    mode = review.get('facebook_media_type', 'REELS')
    if evidence['duration'] > 140 or evidence['file_size'] > 512 * 1024 * 1024:
        errors.append('X media exceeds the conservative unverified-entitlement baseline (140 seconds/512 MiB)')
    if mode == 'REELS' and evidence['duration'] > 90:
        errors.append('Media exceeds the documented Facebook Reels baseline; an explicitly reviewed VIDEO mode is required')
    probe = evidence.get('media', {})
    if probe and (probe.get('video_codec') != 'h264' or probe.get('audio_codec') not in ('aac', None)
                  or 'mp4' not in probe.get('format', '') or probe.get('width', 0) > 1920 or probe.get('height', 0) > 1920):
        errors.append('Media is outside the explicitly supported baseline MP4/H264/AAC X contract; no silent transcode is authorized here')
    values = {'facebook_title': item.title, 'facebook_description': description,
              'facebook_media_type': mode, 'x_title': x_text}
    return {'values': values, 'sha256': pm.digest(values), 'errors': errors,
            'x_weight_upper_bound': weight_upper_bound(x_text), 'x_counter': 'conservative twitter-text v3 ranges; emoji clusters may be overcounted',
            'entitlement': 'baseline only; current connected API does not expose premium text/video entitlements',
            'url_policy': 'hold URLs rather than silently accept provider stripping', 'automatic_threads': False}

"""Visible Facebook/X video fields, without silent clipping or copy fitting.

Provider contract: https://docs.upload-post.com/api/upload-video/
Full-length Facebook uses the explicit Page VIDEO path, not implicit Reels.
"""
import re
import unicodedata


URL_PATTERN = re.compile(r'(?i)(?:\b(?:https?|ftp|wss?)://|\bwww\.|\b(?:[a-z0-9-]+\.)+[a-z]{2,}/|\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?/)')


def weight_upper_bound(text):
    # Official twitter-text v3 ranges. Emoji sequences may be overcounted;
    # uncertain text is held rather than published with an underestimated count.
    ranges = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
    return sum(1 if any(a <= ord(c) <= b for a, b in ranges) else 2
               for c in unicodedata.normalize('NFC', text))


def visible_text(title, description):
    return '\n\n'.join(value for value in (title, description) if value)


def video_fields(title, description, platforms, overrides=None):
    """Return only platform fields; never mutate input copy, media or targets."""
    overrides = overrides or {}
    fields = {}
    if 'facebook' in platforms:
        fields.update(facebook_title=title, facebook_description=description, facebook_media_type='VIDEO')
        fields.update({k:v for k,v in overrides.items() if k.startswith('facebook_')})
        if fields['facebook_media_type'] not in ('VIDEO', 'REELS'):
            raise ValueError('Facebook mode cannot silently discard approved visible copy')
        if len(fields['facebook_title'].encode('utf-16-le')) // 2 > 255 or len(fields['facebook_description'].encode('utf-16-le')) // 2 > 63206:
            raise ValueError('Facebook visible copy exceeds the supported bounds; an approved variant is required')
    if set(platforms) & {'x', 'twitter'}:
        text = overrides.get('x_title', visible_text(title, description))
        if weight_upper_bound(text) > 280:
            raise ValueError('X visible copy exceeds the verified baseline; an approved exact platform variant or verified long-post entitlement is required')
        if URL_PATTERN.search(text):
            raise ValueError('X provider may remove URLs from this approved copy; approve an exact supported variant instead')
        if any(unicodedata.category(c) == 'Cc' and c not in '\n\r\t' for c in text):
            raise ValueError('X visible copy contains unsupported control characters')
        fields['x_title'] = text
    return fields

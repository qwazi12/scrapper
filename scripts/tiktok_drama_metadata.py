#!/usr/bin/env python3
"""Read normal TikTok series metadata, without parsing a redirected video page.

Same signed-in web episode API used for inventory and TikTok's series player.
No page-challenge fallback, signature generation, identity rotation or unlocking.
Media URLs live only in this process's JSON stdout pipe to the RAM downloader.
"""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.inventory_tiktok_drama import episode


def checked_episode(payload, source):
    status = payload.get('statusCode', payload.get('status_code'))
    if status != 0:
        raise ValueError('TikTok episode API refused; status=' + str(status))
    rows = payload.get('itemList', [])
    if len(rows) != 1 or rows[0].get('id') != source.video_id:
        raise ValueError('Episode API returned unexpected video identity')
    raw = rows[0]
    if any(raw.get(k) or raw.get('video', {}).get(k) for k in
           ('isPaidContent', 'has_drm', 'hasDRM')):
        raise ValueError('Paid/DRM episode cannot be archived')
    number = raw.get('dramaInfo', {}).get('DramaVideoData', {}).get('EpisodeNumber')
    if number != source.episode_number:
        raise ValueError('Episode API sequence mismatch')
    # Inventory's identity checks also validate author UID, secUID, handle and
    # canonical series ID before the downloader sees any media URL.
    series = {'id': source.series_id, 'title': '', 'episodes': source.episode_number}
    normalized = episode(raw, series, source.uid, source.channel_id, source.handle)
    if normalized['restricted']:
        raise ValueError('Restricted episode; no media access attempted')
    return raw


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('video-id', 'series-id', 'uid', 'channel-id', 'handle', 'brave-profile'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--episode-number', type=int, required=True)
    args = p.parse_args()
    if (any(not re.fullmatch(r'\d{19}', getattr(args, key)) for key in ('video_id', 'series_id', 'uid')) or
            not re.fullmatch(r'[A-Za-z0-9_.]{1,30}', args.handle) or
            not re.fullmatch(r'MS4wLjABAAAA[A-Za-z0-9_-]{20,150}', args.channel_id) or
            not 1 <= args.episode_number <= 10000):
        p.error('Invalid canonical source identity/episode')
    from scripts import tiktok_access  # noqa: F401 -- never enable the stock solver
    import yt_dlp
    url = f'https://www.tiktok.com/@{args.handle}/video/{args.video_id}'
    options = {'cookiesfrombrowser': ('brave', args.brave_profile),
               'quiet': True, 'cachedir': False, 'socket_timeout': 25,
               'extractor_retries': 0, 'retries': 0, 'skip_download': True,
               'format': 'b[ext=mp4]/b', 'format_sort': ['res:1080']}
    with yt_dlp.YoutubeDL(options) as y:
        ie = y.get_info_extractor('TikTok')
        data = ie._download_json('https://www.tiktok.com/api/drama/episode/item_list/',
            args.video_id, query={'aid': 1988, 'language': 'en', 'region': 'US',
                                 'dramaID': args.series_id, 'cursor': str(args.episode_number - 1),
                                 'count': 1}, headers={'Referer': url})
        raw = checked_episode(data, args)
        info = ie._parse_aweme_video_web(raw, url, args.video_id)
        info.update(webpage_url=url, extractor='tiktok', extractor_key='TikTok')
        result = y.process_ie_result(info, download=False)
        if result.get('has_drm'):
            raise ValueError('DRM content cannot be archived')
        print(json.dumps(y.sanitize_info(result)), flush=True)


if __name__ == '__main__':
    main()

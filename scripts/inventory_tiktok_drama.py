#!/usr/bin/env python3
"""Inventory ordinary accessible TikTok drama APIs without persisting media URLs.

Uses the same read-only endpoints and query fields as TikTok's public web client.
No signature generation, device/account rotation, paid access or challenge solver.
Completed series checkpoints are reusable after interruption.
"""
import argparse
import datetime as dt
import email.utils
import fcntl
import json
import os
from pathlib import Path
import re
import time


def write(path, value):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.chmod(0o600)
    tmp.replace(path)


def server_wait(headers):
    """Honor a longer server Retry-After without changing access identity."""
    value = headers.get('Retry-After', '')
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        try:
            deadline = email.utils.parsedate_to_datetime(value)
            return max(0, (deadline - dt.datetime.now(dt.timezone.utc)).total_seconds())
        except (TypeError, ValueError, AttributeError):
            return 0


def episode(item, series, uid, sec_uid, handle):
    author, drama = item.get('author', {}), item.get('dramaInfo', {})
    vid = str(item.get('id', ''))
    if (not re.fullmatch(r'\d{19}', vid) or author.get('id') != uid or
            author.get('secUid') != sec_uid or author.get('uniqueId') != handle or
            drama.get('dramaID') != series['id'] or drama.get('authorUID') != uid):
        raise ValueError('Episode source/series identity mismatch')
    sequence = drama.get('DramaVideoData', {}).get('EpisodeNumber')
    if type(sequence) is not int or not 1 <= sequence <= series['episodes']:
        raise ValueError('Invalid episode sequence')
    video = item.get('video', {})
    return {'id': vid, 'title': item.get('desc') or f"{series['title']} EP{sequence}",
            'webpage_url': f'https://www.tiktok.com/@{handle}/video/{vid}',
            'series_id': series['id'], 'series_title': series['title'],
            'episode_number': sequence, 'source_duration': video.get('duration'),
            'create_time': item.get('createTime'),
            'source_preview_flag': drama.get('DramaVideoData', {}).get('IsPreview'),
            'source_limited_free_flag': drama.get('isLimitedFree'),
            'restricted': any(bool(item.get(k)) for k in
                              ('privateItem', 'secret', 'isProhibited', 'isReviewing'))}


def coverage(series, items):
    numbers = [i['episode_number'] for i in items]
    ids = [i['id'] for i in items]
    if len(set(ids)) != len(ids) or len(set(numbers)) != len(numbers):
        raise ValueError('Duplicate IDs or episode numbers inside series')
    return sorted(set(range(1, series['episodes'] + 1)) - set(numbers))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--handle', required=True)
    parser.add_argument('--uid', required=True)
    parser.add_argument('--sec-uid', required=True)
    parser.add_argument('--brave-profile', required=True)
    args = parser.parse_args()
    root = args.state_dir.absolute()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    lock = (root / 'inventory.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    cooldown = root / 'inventory-cooldown.json'
    if cooldown.exists():
        previous = json.loads(cooldown.read_text())
        remaining = (dt.datetime.fromisoformat(previous['retry_at']) -
                     dt.datetime.now(dt.timezone.utc)).total_seconds()
        if remaining > 0:
            time.sleep(remaining)
    import tiktok_access  # noqa: F401 -- disables stock challenge solving
    import yt_dlp
    source_url = 'https://www.tiktok.com/@' + args.handle
    with yt_dlp.YoutubeDL({'cookiesfrombrowser': ('brave', args.brave_profile),
                          'quiet': True, 'cachedir': False, 'socket_timeout': 25,
                          'extractor_retries': 0, 'retries': 0}) as y:
        ie = y.get_info_extractor('TikTok')

        def request(path, query):
            for attempt in range(4):
                time.sleep(1)  # bounded request pacing, not detection evasion
                rate_limited = False
                minimum_delay = 0
                try:
                    data = ie._download_json('https://www.tiktok.com/api/drama/' + path,
                                            'drama-inventory', query={
                                                'aid': 1988, 'language': 'en', 'region': 'US',
                                                'count': 100, **query}, headers={'Referer': source_url})
                except Exception as exc:
                    from yt_dlp.networking.exceptions import HTTPError, TransportError, SSLError
                    cause = getattr(exc, 'cause', exc)
                    status = getattr(cause, 'status', None)
                    rate_limited = status == 429
                    if isinstance(cause, HTTPError):
                        minimum_delay = server_wait(cause.response.headers)
                    allowed = (isinstance(cause, HTTPError) and status in (408, 429, 500, 502, 503, 504) or
                               isinstance(cause, TransportError) and not isinstance(cause, SSLError))
                    if not allowed or attempt == 3:
                        raise
                else:
                    status = data.get('statusCode', data.get('status_code'))
                    if status == 0:
                        return data
                    # Public enum: ServerErrorNot500/NetError. Verification,
                    # risk, login and all unknown statuses still stop.
                    if status not in (10101, 10111) or attempt == 3:
                        raise ValueError('TikTok inventory refused: status=' + str(status))
                seconds = max(minimum_delay, 900 * 2 ** attempt if rate_limited else 60 * 3 ** attempt)
                write(root / 'inventory-cooldown.json', {
                    'status': status, 'seconds': seconds, 'query': query,
                    'retry_at': (dt.datetime.now(dt.timezone.utc) +
                                 dt.timedelta(seconds=seconds)).isoformat()})
                print(json.dumps({'inventory_cooldown': seconds, 'status': status}), flush=True)
                time.sleep(seconds)

        catalog_file = root / 'catalog.json'
        if catalog_file.exists():
            catalog = json.loads(catalog_file.read_text())
            if catalog['channel_id'] != args.sec_uid or catalog['account_id'] != args.uid:
                raise ValueError('Saved catalogue belongs to another source')
        else:
            series, cursor, seen = [], '0', set()
            while True:
                if cursor in seen:
                    raise ValueError('Catalogue cursor repeated')
                seen.add(cursor)
                data = request('user/drama_list/', {'secUid': args.sec_uid, 'cursor': cursor})
                rows = data.get('dramaList', [])
                for row in rows:
                    sid = row.get('dramaID', '')
                    if not re.fullmatch(r'\d{19}', sid):
                        raise ValueError('Invalid series ID')
                    series.append({'id': sid, 'title': row.get('dramaName', ''),
                                   'episodes': int(row['numVideos']),
                                   'total_duration': int(row.get('totalDuration') or 0)})
                if not data.get('hasMore'):
                    break
                if not rows:
                    raise ValueError('Empty page claims more catalogue entries')
                cursor = str(data['cursor'])
            if len({s['id'] for s in series}) != len(series):
                raise ValueError('Duplicate series IDs')
            catalog = {'channel_id': args.sec_uid, 'account_id': args.uid,
                       'source_url': source_url, 'series': series, 'complete': True}
            write(catalog_file, catalog)
        series_root = root / 'series'
        series_root.mkdir(exist_ok=True)
        unique, missing, restrictions, duplicates = {}, [], [], 0
        for index, series in enumerate(catalog['series']):
            checkpoint = series_root / (series['id'] + '.json')
            if checkpoint.exists():
                result = json.loads(checkpoint.read_text())
                if result['series'] != series or result['account_id'] != args.uid:
                    raise ValueError('Saved series identity changed')
            else:
                rows, cursor, seen = [], '0', set()
                while True:
                    if cursor in seen:
                        raise ValueError('Episode cursor repeated')
                    seen.add(cursor)
                    data = request('episode/item_list/', {'dramaID': series['id'], 'cursor': cursor})
                    items = data.get('itemList', [])
                    rows.extend(episode(i, series, args.uid, args.sec_uid, args.handle) for i in items)
                    if not data.get('hasMore'):
                        break
                    if not items:
                        raise ValueError('Empty page claims more episodes')
                    cursor = str(data['cursor'])
                result = {'series': series, 'account_id': args.uid, 'items': rows,
                          'missing_episode_numbers': coverage(series, rows)}
                write(checkpoint, result)
            for row in result['items']:
                if row['id'] in unique:
                    duplicates += 1
                    continue
                unique[row['id']] = row
                if row['restricted']:
                    restrictions.append(row['id'])
            if result['missing_episode_numbers']:
                missing.append({'series_id': series['id'], 'episodes': result['missing_episode_numbers']})
            progress = {'completed_series': index + 1, 'series': len(catalog['series']),
                        'unique_videos': len(unique), 'missing': missing, 'restricted': len(restrictions)}
            write(root / 'inventory-progress.json', progress)
            print(json.dumps(progress), flush=True)
        inventory = {'platform': 'tiktok', 'channel_id': args.sec_uid, 'account_id': args.uid,
                     'handle': args.handle, 'webpage_url': source_url,
                     'complete': True, 'catalogue_pagination_complete': True,
                     'all_episode_slots_present': not missing,
                     'series_count': len(catalog['series']),
                     'listed_episode_slots': sum(s['episodes'] for s in catalog['series']),
                     'missing_episode_slots': missing, 'cross_series_duplicate_ids': duplicates,
                     'restricted_video_ids': restrictions,
                     'inventoried_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                     'items': list(unique.values())}
        write(root / 'inventory.json', inventory)
        print(json.dumps({k:v for k,v in inventory.items() if k != 'items'}), flush=True)


if __name__ == '__main__':
    main()

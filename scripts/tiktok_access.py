"""Normal TikTok extraction only; never solve a verification challenge."""
from yt_dlp.extractor.tiktok import TikTokBaseIE
from yt_dlp.utils import ExtractorError


def require_normal_access(self, webpage):
    raise ExtractorError(
        'TikTok verification or missing page metadata; automatic challenge solving '
        'is disabled. Check normal signed-in browser access.', expected=True)


TikTokBaseIE._solve_challenge_and_set_cookies = require_normal_access

if __name__ == '__main__':
    import yt_dlp
    yt_dlp.main()

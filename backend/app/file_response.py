"""Media readers retain their shared file claim until the last ASGI byte."""
from pathlib import Path

from fastapi import HTTPException
from starlette.responses import FileResponse as StarletteFileResponse

from . import file_use


class FileResponse(StarletteFileResponse):
    def __init__(self,*args,expected_sha256=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.expected_sha256=expected_sha256

    async def __call__(self, scope, receive, send):
        # Resolving once prevents a later symlink switch from changing the
        # pathname actually opened by the streaming implementation.
        self.path = str(Path(self.path).resolve())
        with file_use.pin([self.path]):
            if not Path(self.path).is_file():
                raise HTTPException(404,'File is no longer available; reload its current export')
            if self.expected_sha256:
                from .studio.media_quality import file_hash
                if file_hash(Path(self.path))!=self.expected_sha256:
                    raise HTTPException(409,'File changed since its immutable manifest; no bytes were served')
            # A response created earlier must not reuse stale byte/range headers.
            self.stat_result = None
            for name in ('content-length','last-modified','etag'):
                if name in self.headers: del self.headers[name]
            owned_scope = {**scope,'extensions':{k:v for k,v in scope.get('extensions',{}).items()
                                                if k != 'http.response.pathsend'}}
            # pathsend hands a pathname to a server that may read it after this
            # response returns. Explicit streaming keeps ownership through send.
            async def guarded_send(message):
                file_use.assert_current()
                await send(message)
            await super().__call__(owned_scope,receive,guarded_send)

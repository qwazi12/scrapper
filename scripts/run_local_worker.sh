#!/bin/bash
export PATH="/Library/Frameworks/Python.framework/Versions/3.13/bin:/opt/homebrew/bin:/usr/local/bin:/Users/kwasiyeboah/.nvm/versions/node/v24.13.0/bin:/usr/bin:/bin:/usr/sbin:/sbin"
exec /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -u \
    /Users/kwasiyeboah/dev/scrapper/scripts/local_worker.py \
    --watch \
    --interval 15 \
    "$@"


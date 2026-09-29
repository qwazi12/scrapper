#!/bin/bash
# Wrapper for launchd: uses the full python path so launchd's restricted
# PATH doesn't resolve to CommandLineTools python instead.
exec /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    /Users/kwasiyeboah/Desktop/scrapper/scripts/local_worker.py \
    --watch \
    --interval 120 \
    --browser safari \
    "$@"

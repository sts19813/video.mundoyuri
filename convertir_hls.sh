#!/bin/bash

# Lanzador para macOS/Linux. La logica compartida vive en convertir_hls.py.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: instala Python 3 y FFmpeg (ffmpeg + ffprobe)."
    exit 1
fi

if [[ "$#" -eq 0 ]]; then
    exec python3 "$SCRIPT_DIR/convertir_hls.py" "$SCRIPT_DIR"
else
    exec python3 "$SCRIPT_DIR/convertir_hls.py" "$@"
fi

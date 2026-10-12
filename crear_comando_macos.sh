#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="$PROJECT_DIR/dist"
OUTPUT_FILE="$OUTPUT_DIR/convertir_hls_macos.command"
OUTPUT_AUTO_FILE="$OUTPUT_DIR/convertir_hls_todo_original.command"

mkdir -p "$OUTPUT_DIR"

write_command() {
    local output_file="$1"
    local default_args="$2"

{
cat <<'HEADER'
#!/bin/bash
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TMP_DIR="$(mktemp -d)"
cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

PY_FILE="$TMP_DIR/convertir_hls.py"
cat > "$PY_FILE" <<'PYTHON_PAYLOAD'
HEADER

cat "$PROJECT_DIR/convertir_hls.py"

cat <<'FOOTER'
PYTHON_PAYLOAD

pause_and_exit() {
    local status="$1"
    echo
    read -r -p "Proceso terminado. Presiona Enter para cerrar esta ventana..." _
    exit "$status"
}

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: instala Python 3."
    pause_and_exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    echo "ERROR: instala FFmpeg con: brew install ffmpeg"
    pause_and_exit 1
fi

DEFAULT_ARGS_PLACEHOLDER
if [[ "$#" -eq 0 ]]; then
    python3 "$PY_FILE" "$SCRIPT_DIR" "${DEFAULT_ARGS[@]}"
else
    python3 "$PY_FILE" "$@"
fi
STATUS="$?"
pause_and_exit "$STATUS"
FOOTER
} | sed "s|DEFAULT_ARGS_PLACEHOLDER|$default_args|" > "$output_file"

    chmod +x "$output_file"
}

write_command "$OUTPUT_FILE" 'DEFAULT_ARGS=()'
write_command "$OUTPUT_AUTO_FILE" 'DEFAULT_ARGS=(--all --resolution original --quality original)'

echo "Comando creado en: $OUTPUT_FILE"
echo "Comando automatico creado en: $OUTPUT_AUTO_FILE"

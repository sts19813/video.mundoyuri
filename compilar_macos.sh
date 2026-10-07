#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(command -v python3)"
VENV_DIR="$PROJECT_DIR/.venv-build"

find_program() {
    command -v "$1" 2>/dev/null || true
}

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    "$PYTHON" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip pyinstaller

PYINSTALLER_ARGS=(
    --noconfirm
    --clean
    --onefile
    --console
    --name convertir_hls_macos
    --distpath "$PROJECT_DIR/dist"
    --workpath "$PROJECT_DIR/build"
    --specpath "$PROJECT_DIR"
)

FFMPEG="$(find_program ffmpeg)"
FFPROBE="$(find_program ffprobe)"
if [[ -z "$FFMPEG" || -z "$FFPROBE" ]]; then
    echo "ERROR: no se encontro FFmpeg. Instala primero con: brew install ffmpeg"
    exit 1
fi

PYINSTALLER_ARGS+=(--add-binary "$FFMPEG:.")
PYINSTALLER_ARGS+=(--add-binary "$FFPROBE:.")
PYINSTALLER_ARGS+=("$PROJECT_DIR/convertir_hls.py")

"$VENV_DIR/bin/python" -m PyInstaller "${PYINSTALLER_ARGS[@]}"

cat > "$PROJECT_DIR/dist/convertir_hls_macos.command" <<'EOF'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/convertir_hls_macos"
echo
read -r -p "Proceso terminado. Presiona Enter para cerrar esta ventana..." _
EOF

chmod +x "$PROJECT_DIR/dist/convertir_hls_macos" "$PROJECT_DIR/dist/convertir_hls_macos.command"

echo
echo "Ejecutable creado en: $PROJECT_DIR/dist/convertir_hls_macos"
echo "Comando doble clic creado en: $PROJECT_DIR/dist/convertir_hls_macos.command"

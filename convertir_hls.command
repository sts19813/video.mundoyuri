#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/convertir_hls.sh" "$@"

echo
read -r -p "Proceso terminado. Presiona Enter para cerrar esta ventana..." _

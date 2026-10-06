#!/bin/bash

# Convierte MP4 de la carpeta donde esta este script a HLS
# Ejemplo:
# Moonshadow.S01e01.Iq.X264.1080P.mp4
# →
# Moonshadow.S01e01.HLS/
#   index.m3u8
#   segmento_0000.ts
#   segmento_0001.ts
#   ...

CLOUDFLARE="https://video.mundoyuri.com"

# Trabajar siempre desde la carpeta del script. Esto permite abrirlo con doble clic.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# Nombre de la carpeta actual para armar la URL publica.
CARPETA="$(basename "$SCRIPT_DIR")"

# Para la URL: espacios como %20.
url_escape_spaces() {
    printf '%s' "${1// /%20}"
}

CARPETA_URL="$(url_escape_spaces "$CARPETA")"

shopt -s nullglob nocaseglob

archivos=( *.mp4 )

if [ ${#archivos[@]} -eq 0 ]; then
    echo "No se encontraron archivos MP4 en:"
    echo "$SCRIPT_DIR"
    exit 0
fi

echo
echo "Selecciona que quieres convertir:"
echo "  1) Todos los archivos"

opcion=2
for archivo in "${archivos[@]}"; do
    echo "  $opcion) $archivo"
    opcion=$((opcion + 1))
done
max_opcion=$((opcion - 1))

while true; do
    echo
    read -r -p "Escribe el numero y presiona Enter: " seleccion

    case "$seleccion" in
        ''|*[!0-9]*)
            echo "Opcion invalida. Escribe un numero de la lista."
            continue
            ;;
    esac

    if [ "$seleccion" -ge 1 ] && [ "$seleccion" -le "$max_opcion" ]; then
        break
    fi

    echo "Opcion invalida. Escribe un numero entre 1 y $max_opcion."
done

if [ "$seleccion" -eq 1 ]; then
    seleccionados=( "${archivos[@]}" )
else
    indice=$((seleccion - 2))
    seleccionados=( "${archivos[$indice]}" )
fi

# Archivo donde guardaremos las URLs generadas
URLS="urls-cloudflare.txt"

> "$URLS"

for archivo in "${seleccionados[@]}"; do

    # Quitar extensión
    nombre="${archivo%.*}"

    # Quitar sufijo de codificación
    base=$(echo "$nombre" | sed -E 's/\.Iq\.X264\.1080P$//I')

    salida="${base}.HLS"

    echo
    echo "=========================================="
    echo "Procesando: $archivo"
    echo "Salida:     $salida"
    echo "=========================================="

    # Si ya existe un HLS terminado, no volver a procesarlo
    if [ -f "$salida/index.m3u8" ]; then
        echo "Ya existe index.m3u8. Saltando..."
    else
        mkdir -p "$salida"

        ffmpeg -i "$archivo" \
          -c:v copy \
          -c:a aac \
          -b:a 128k \
          -hls_time 6 \
          -hls_playlist_type vod \
          -hls_segment_filename "$salida/segmento_%04d.ts" \
          "$salida/index.m3u8"

        if [ $? -ne 0 ]; then
            echo "ERROR convirtiendo: $archivo"
            continue
        fi
    fi

    SALIDA_URL="$(url_escape_spaces "$salida")"
    URL="$CLOUDFLARE/$CARPETA_URL/$SALIDA_URL/index.m3u8"

    echo "$URL" >> "$URLS"

    echo
    echo "LISTO:"
    echo "$URL"

done

echo
echo "=========================================="
echo "ARCHIVOS TERMINADOS"
echo "URLs guardadas en: $URLS"
echo "=========================================="

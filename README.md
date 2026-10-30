# Conversor de video a HLS para MundoYuri

El conversor acepta extensiones comunes de video (`.mkv`, `.mp4`, `.mov`, `.avi`,
`.webm`, `.m4v`, `.ts`, entre otras), detecta **todas** las pistas de audio,
extrae **todas** las pistas de subtitulos sin perdida (`.mks`), convierte a
WebVTT las pistas de texto y genera HLS VOD con segmentos de 25 segundos.
Con una sola pista de audio, cada segmento TS incluye video y audio para que
Hls.js no solicite una playlist y segmentos de audio adicionales. Con dos o
mas pistas, se conservan como alternativas y se segmentan usando el mismo mapa
de tiempos del video para evitar drift o desfase durante el episodio.

Al abrirlo en modo interactivo, primero pregunta como optimizar:

- conservar resolucion original o redimensionar a 720p, 480p o 360p;
- elegir entre calidad original, web equilibrada, web rápida, web ligera o muy ligera;
- usar `libx264` por software o, si el equipo lo admite, aceleración de hardware;
- conservar salidas `.HLS` existentes o regenerarlas para aplicar el perfil nuevo;
- convertir todos los archivos, uno especifico o varios usando coma/rangos
  (`2,4-6`).

Cada episodio produce:

```text
Nombre.S01E01.HLS/         <- una pista de audio (caso habitual)
  master.m3u8              <- usar esta URL en el reproductor
  index.m3u8               <- HLS MPEG-TS con video y audio integrados
  segmento_00000.ts
  opciones.json            <- perfil y formato usado para esta salida
  subtitulos/
    pistas.json
    pista_01_es.mks         <- pista original, sin perdida
    pista_01_es.vtt         <- copia WebVTT para web
    pista_01/index.m3u8     <- playlist HLS de subtitulos
```

Con dos o mas pistas de audio, `index.m3u8` contiene solo video y se agrega:

```text
  audios/
    pistas.json
    pista_01/index.m3u8    <- audio HLS alterno MPEG-TS alineado al video
    pista_02/index.m3u8
```

En ese caso `master.m3u8` enlaza el video con sus audios y subtitulos
alternos. Si no hay audio, solo publica el video y no incluye referencias de
audio. `opciones.json` guarda `hls_layout` como `muxed_single_audio`,
`separate_multi_audio` o `video_only`. El conversor lo usa (y verifica
playlists antiguas sin ese metadato) para no mezclar formatos incompatibles;
para cambiarlos hay que ejecutar con `--overwrite`. Las carpetas `audios/`
existentes no se eliminan automaticamente.
Las pistas graficas PGS/VobSub se guardan en `.mks`, pero no se agregan al
playlist web porque necesitan OCR para convertirse en texto.

Cuando se usa calidad `original`, el conversor conserva el audio AAC original
con `copy`. Si la fuente trae audio no compatible con HLS web, lo convierte a
AAC usando el bitrate del perfil elegido.

## Windows

El ejecutable incluido en `dist` lleva `ffmpeg` y `ffprobe` dentro, por lo que en la
computadora donde se use no hace falta instalar nada. Se puede:

- copiar `convertir_hls_windows.exe` a la carpeta de los videos y abrirlo con doble clic;
- arrastrar un video o una carpeta encima del `.exe`;
- ejecutarlo desde PowerShell:

```powershell
.\convertir_hls_windows.exe "F:\GL Project\Only You (1414)" --all
```

Sin argumento, usa la carpeta donde se encuentra el ejecutable. Sin `--all`,
muestra menus para elegir perfil de optimizacion, politica de regeneracion y
archivos a convertir. Si el HLS ya existe y se elige conservarlo, vuelve a
comprobar el formato, completar subtitulos y `master.m3u8` y, cuando hay audio
multiple, completar las pistas alternativas.

Para forzar una regeneracion completa:

```powershell
.\convertir_hls_windows.exe "F:\ruta\temporada" --all --overwrite
```

Para una conversion nueva conservando resolucion y audio AAC original:

```powershell
.\convertir_hls_windows.exe "F:\ruta\temporada" --all --overwrite --resolution original --quality original
```

Para procesar todo sin menus y generar archivos mas ligeros:

```powershell
.\convertir_hls_windows.exe "F:\ruta\temporada" --all --overwrite --resolution 720p --quality web
```

Para convertir más rápido con la GTX (requiere que el FFmpeg incluido tenga
NVENC y que el controlador NVIDIA esté instalado):

```powershell
.\convertir_hls_windows.exe "F:\ruta\temporada" --all --overwrite --resolution 720p --quality web_rapido --encoder hardware
```

Para priorizar archivos ligeros conservando una calidad web razonable:

```powershell
.\convertir_hls_windows.exe "F:\ruta\temporada" --all --overwrite --resolution 720p --quality web_ligero
```

Para hacerlos todavia mas pequenos:

```powershell
.\convertir_hls_windows.exe "F:\ruta\temporada" --all --overwrite --resolution 480p --quality minimo
```

## Compilar el ejecutable

En la maquina de desarrollo se necesita Python 3, FFmpeg y PyInstaller. El script de
compilacion instala/actualiza PyInstaller automaticamente:

```powershell
winget install --id Python.Python.3.11 --exact
winget install --id Gyan.FFmpeg --exact
.\compilar_windows.ps1
```

El `.exe` nuevo queda en:

```text
dist\convertir_hls_windows.exe
```

## macOS

El mismo motor sigue funcionando mediante `convertir_hls.command`. Requiere Python 3
y FFmpeg, por ejemplo:

```bash
brew install python ffmpeg
chmod +x convertir_hls.command convertir_hls.sh
```

Conversion nueva recomendada en Mac:

```bash
./convertir_hls.sh "/Users/tu_usuario/Downloads/Temporada" --all --overwrite --resolution original --quality original
```

En un Mac con Apple Silicon, este comando usa el codificador acelerado
VideoToolbox. Es mucho más rápido que software; `libx264` normalmente comprime
un poco mejor a igual calidad visual:

```bash
./convertir_hls.sh "/Users/tu_usuario/Downloads/Temporada" --all --overwrite --resolution 720p --quality web_rapido --encoder hardware
```

Para abrir con doble clic, usa `convertir_hls.command`.

Para crear un `.command` autocontenido que puedas copiar dentro de una carpeta
de videos:

```bash
./crear_comando_macos.sh
```

Eso genera:

```text
dist/convertir_hls_macos.command          <- abre menus
dist/convertir_hls_todo_original.command <- procesa toda la carpeta
```

Copia `dist/convertir_hls_todo_original.command` dentro de la carpeta de la
temporada y abrelo con doble clic. Procesa todos los videos con resolucion y
calidad original, usando el audio AAC original cuando sea compatible.

## Cloudflare

Para `video.mundoyuri.com`, conserva cache habilitado para `.m3u8` y `.ts`.
Las conversiones nuevas generan playlists limpias, sin `?v=` en los segmentos.
El query `?v=audio4-20261011` se uso solo para parches de audio existentes,
para separar audios reparados de versiones viejas cacheadas.

Configuracion recomendada:

```text
Hostname: video.mundoyuri.com
Path: *.m3u8 y *.ts
Cache eligibility: Eligible for cache
Cache key: standard / incluir query string
Edge TTL: 30 dias o mas para .ts
Browser TTL: respetar origen o 1 dia
```

Validacion rapida de cache:

```bash
curl -sS -L -o /dev/null -D - "https://video.mundoyuri.com/RUTA/segmento_00000.ts" \
  | grep -iE "cf-cache-status|age|cache-control"
```

## Opciones

```text
--all                 procesa todos sin menu
--overwrite           regenera salidas existentes
--cdn URL             cambia https://video.mundoyuri.com
--ffmpeg-dir CARPETA  usa otra instalacion de FFmpeg
--resolution VALOR    original, 720p, 480p o 360p
--quality VALOR       original, web, web_rapido, web_ligero o minimo
--encoder VALOR       software (libx264) o hardware (VideoToolbox/NVENC)
--no-pause            no espera Enter al terminar en Windows
```

## Perfiles de video

Todos generan H.264 `yuv420p` y segmentos HLS de 25 segundos. Con audio
alternativo conservan la misma lógica de alineación; con una unica pista, el
audio AAC se multiplexa en los mismos segmentos que el video. Con software, los perfiles usan CRF: el
tamaño final depende del contenido del episodio y no es un bitrate fijo.
Al recodificar video se fuerzan fotogramas clave para aproximar ese intervalo;
al copiar H.264 se respetan los fotogramas clave de la fuente, por lo que la
duración exacta de cada segmento puede variar.

Las conversiones nuevas usan 25 segundos. Una salida HLS ya existente se
conserva sin cambios si no se indica `--overwrite`; para pasar una salida
anterior de 20 a 25 segundos hay que regenerarla con `--overwrite`, para que
video y pistas de audio alternativas compartan el mismo mapa temporal.

| Perfil | Video por software | Audio | Uso recomendado |
|---|---|---:|---|
| `original` | CRF 20, `medium` | AAC 128k o copia AAC | máxima calidad práctica |
| `web` | CRF 26, `medium` | 96k | equilibrio habitual |
| `web_rapido` | CRF 27, `fast` | 80k | reducir tiempo de conversión |
| `web_ligero` | CRF 28, `slow` | 80k | reducir tamaño manteniendo calidad decente |
| `minimo` | CRF 30, `slow` | 64k | archivos muy pequeños |

Al usar `--encoder hardware`, VideoToolbox o NVENC usa el bitrate objetivo del
perfil en vez del CRF de x264. Esto hace su peso más predecible y acelera la
conversión, pero su eficiencia de compresión puede ser algo inferior a
`libx264`.

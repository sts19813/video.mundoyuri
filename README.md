# Conversor de video a HLS para MundoYuri

El conversor acepta extensiones comunes de video (`.mkv`, `.mp4`, `.mov`, `.avi`,
`.webm`, `.m4v`, `.ts`, entre otras), detecta **todas** las pistas de audio,
extrae **todas** las pistas de subtitulos sin perdida (`.mks`), convierte a
WebVTT las pistas de texto y genera HLS VOD con segmentos de 20 segundos.

Al abrirlo en modo interactivo, primero pregunta como optimizar:

- conservar resolucion original o redimensionar a 720p, 480p o 360p;
- conservar calidad, usar un perfil web ligero o usar un perfil muy ligero;
- conservar salidas `.HLS` existentes o regenerarlas para aplicar el perfil nuevo;
- convertir todos los archivos, uno especifico o varios usando coma/rangos
  (`2,4-6`).

Cada episodio produce:

```text
Nombre.S01E01.HLS/
  master.m3u8              <- usar esta URL en el reproductor
  index.m3u8               <- video HLS sin audio
  segmento_00000.ts
  opciones.json            <- perfil usado para esta salida
  audios/
    pistas.json
    pista_01/index.m3u8    <- audio HLS alterno
    pista_02/index.m3u8
  subtitulos/
    pistas.json
    pista_01_es.mks         <- pista original, sin perdida
    pista_01_es.vtt         <- copia WebVTT para web
    pista_01/index.m3u8     <- playlist HLS de subtitulos
```

El archivo `master.m3u8` enlaza el video con sus audios y subtitulos alternos.
Las pistas graficas PGS/VobSub se guardan en `.mks`, pero no se agregan al
playlist web porque necesitan OCR para convertirse en texto.

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
comprobar y completar audios, subtitulos y `master.m3u8`.

Para forzar una regeneracion completa:

```powershell
.\convertir_hls_windows.exe "F:\ruta\temporada" --all --overwrite
```

Para procesar todo sin menus y generar archivos mas ligeros:

```powershell
.\convertir_hls_windows.exe "F:\ruta\temporada" --all --overwrite --resolution 720p --quality web
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

## macOS

El mismo motor sigue funcionando mediante `convertir_hls.command`. Requiere Python 3
y FFmpeg, por ejemplo:

```bash
brew install python ffmpeg
chmod +x convertir_hls.command convertir_hls.sh
```

## Opciones

```text
--all                 procesa todos sin menu
--overwrite           regenera salidas existentes
--cdn URL             cambia https://video.mundoyuri.com
--ffmpeg-dir CARPETA  usa otra instalacion de FFmpeg
--resolution VALOR    original, 720p, 480p o 360p
--quality VALOR       original, web o minimo
--no-pause            no espera Enter al terminar en Windows
```

# Conversor de video a HLS para MundoYuri

El conversor acepta extensiones comunes de video (`.mkv`, `.mp4`, `.mov`, `.avi`,
`.webm`, `.m4v`, `.ts`, entre otras), extrae **todas** las
pistas de subtitulos sin perdida (`.mks`), convierte a WebVTT las pistas de texto y
genera HLS VOD con segmentos de 20 segundos.

Cada episodio produce:

```text
Nombre.S01E01.HLS/
  master.m3u8              <- usar esta URL en el reproductor
  index.m3u8               <- video HLS
  segmento_00000.ts
  subtitulos/
    pistas.json
    pista_01_es.mks         <- pista original, sin perdida
    pista_01_es.vtt         <- copia WebVTT para web
    pista_01/index.m3u8     <- playlist HLS de subtitulos
```

Las pistas graficas PGS/VobSub se guardan en `.mks`, pero no se agregan al playlist
web porque necesitan OCR para convertirse en texto.

## Windows

El ejecutable incluido en `dist` lleva `ffmpeg` y `ffprobe` dentro, por lo que en la
computadora donde se use no hace falta instalar nada. Se puede:

- copiar `convertir_hls_windows.exe` a la carpeta de los videos y abrirlo con doble clic;
- arrastrar un video o una carpeta encima del `.exe`;
- ejecutarlo desde PowerShell:

```powershell
.\convertir_hls_windows.exe "F:\GL Project\Only You (1414)" --all
```

Sin argumento, usa la carpeta donde se encuentra el ejecutable. Sin `--all`, muestra
un menu para convertir toda la carpeta o un episodio. Si el video ya existe, lo
conserva, pero vuelve a comprobar y completar los subtitulos y `master.m3u8`.

Para forzar una regeneracion completa:

```powershell
.\convertir_hls_windows.exe "F:\ruta\temporada" --all --overwrite
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
--no-pause            no espera Enter al terminar en Windows
```

#!/usr/bin/env python3
"""Convierte archivos MKV a HLS y extrae todas sus pistas de subtitulos."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote


DEFAULT_CDN = "https://video.mundoyuri.com"
TEXT_SUBTITLE_CODECS = {
    "ass", "ssa", "subrip", "srt", "text", "mov_text", "webvtt",
}


class ConversionError(RuntimeError):
    pass


def configure_console() -> None:
    """Evita fallos al mostrar rutas con caracteres fuera de la pagina de Windows."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="replace")


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_dir() -> Path | None:
    value = getattr(sys, "_MEIPASS", None)
    return Path(value) if value else None


def find_program(name: str, explicit_dir: Path | None = None) -> Path | None:
    executable = f"{name}.exe" if os.name == "nt" else name
    candidates: list[Path] = []
    if explicit_dir:
        candidates.append(explicit_dir / executable)
    candidates.extend([application_dir() / executable, application_dir() / "tools" / executable])
    if bundled_dir():
        candidates.extend([bundled_dir() / executable, bundled_dir() / "tools" / executable])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    resolved = shutil.which(name)
    return Path(resolved) if resolved else None


def run(command: list[str | Path], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    printable = " ".join(f'"{part}"' if " " in str(part) else str(part) for part in command)
    print(f"\n> {printable}")
    return subprocess.run(
        [str(part) for part in command], check=True, text=True,
        encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def probe_file(ffprobe: Path, source: Path) -> dict:
    try:
        result = run([
            ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", source,
        ], capture=True)
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise ConversionError(f"ffprobe no pudo leer '{source.name}': {detail.strip()}") from exc


def validate_mkv(source: Path, probe: dict) -> None:
    if source.suffix.lower() != ".mkv":
        raise ConversionError(f"Se omitio '{source.name}': la extension no es .mkv")
    format_name = str(probe.get("format", {}).get("format_name", "")).lower()
    if "matroska" not in format_name:
        raise ConversionError(
            f"Se omitio '{source.name}': ffprobe indica contenedor "
            f"'{format_name or 'desconocido'}', no Matroska."
        )


def clean_output_name(stem: str) -> str:
    value = re.sub(r"\s*\[[^\]]+\]\s*$", "", stem).rstrip(" ._-")
    release_tokens = (
        r"NF|AMZN|DSNP|HMAX|WEB[-_. ]?DL|WEBRip|BluRay|BDRip|HDRip|"
        r"x26[45]|h26[45]|HEVC|AVC|2160p|1080p|720p|480p|"
        r"AAC(?:2\.0)?|DDP?(?:\d(?:\.\d)?)?"
    )
    old = None
    while old != value:
        old = value
        value = re.sub(
            rf"[ ._-]+(?:{release_tokens})$", "", value, flags=re.IGNORECASE
        ).rstrip(" ._-")
    return value or stem


def safe_tag(value: str, fallback: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip()).strip("_")
    return value[:24] or fallback


def metadata_value(stream: dict, key: str, fallback: str = "") -> str:
    return str(stream.get("tags", {}).get(key, fallback)).strip()


def escape_m3u8(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', "\\\"").replace("\r", " ").replace("\n", " ")


def duration_seconds(probe: dict) -> float:
    raw = probe.get("format", {}).get("duration", 0)
    try:
        return max(float(raw), 0.001)
    except (TypeError, ValueError):
        return 1.0


def extract_subtitles(
    ffmpeg: Path, source: Path, output: Path, probe: dict, overwrite: bool
) -> list[dict]:
    subtitles = [s for s in probe.get("streams", []) if s.get("codec_type") == "subtitle"]
    subtitle_root = output / "subtitulos"
    subtitle_root.mkdir(parents=True, exist_ok=True)
    duration = duration_seconds(probe)
    web_tracks: list[dict] = []
    inventory: list[dict] = []

    for number, stream in enumerate(subtitles, start=1):
        stream_index = int(stream["index"])
        codec = str(stream.get("codec_name", "desconocido"))
        language = metadata_value(stream, "language", "und").lower()
        title = metadata_value(stream, "title", f"Subtitulo {number}")
        prefix = f"pista_{number:02d}_{safe_tag(language, 'und')}"
        raw_file = subtitle_root / f"{prefix}.mks"

        if overwrite or not raw_file.is_file():
            run([
                ffmpeg, "-hide_banner", "-loglevel", "warning", "-y", "-i", source,
                "-map", f"0:{stream_index}", "-c", "copy", "-f", "matroska", raw_file,
            ])

        item = {
            "numero": number, "indice_ffmpeg": stream_index, "codec": codec,
            "idioma": language, "titulo": title, "archivo_original": raw_file.name,
            "webvtt": None,
        }

        if codec in TEXT_SUBTITLE_CODECS:
            vtt_file = subtitle_root / f"{prefix}.vtt"
            track_dir = subtitle_root / f"pista_{number:02d}"
            segment_file = track_dir / "segmento_0000.vtt"
            playlist_file = track_dir / "index.m3u8"
            track_dir.mkdir(parents=True, exist_ok=True)
            if overwrite or not vtt_file.is_file():
                run([
                    ffmpeg, "-hide_banner", "-loglevel", "warning", "-y", "-i", source,
                    "-map", f"0:{stream_index}", "-c:s", "webvtt", vtt_file,
                ])
            shutil.copyfile(vtt_file, segment_file)
            target = max(1, math.ceil(duration))
            playlist_file.write_text(
                "#EXTM3U\n#EXT-X-VERSION:3\n"
                f"#EXT-X-TARGETDURATION:{target}\n"
                "#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-PLAYLIST-TYPE:VOD\n"
                f"#EXTINF:{duration:.3f},\nsegmento_0000.vtt\n#EXT-X-ENDLIST\n",
                encoding="utf-8",
            )
            item["webvtt"] = vtt_file.name
            web_tracks.append({
                "number": number, "language": language,
                "name": title or f"Subtitulo {number}",
                "default": bool(stream.get("disposition", {}).get("default", 0)),
                "forced": bool(stream.get("disposition", {}).get("forced", 0)),
                "playlist": f"subtitulos/pista_{number:02d}/index.m3u8",
            })
        else:
            print(
                f"AVISO: pista {number} ({codec}) es grafica. Se extrajo sin perdida a "
                f"{raw_file.name}, pero no se agrego al HLS web porque requiere OCR."
            )
        inventory.append(item)

    (subtitle_root / "pistas.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return web_tracks


def video_settings(probe: dict) -> tuple[dict, dict | None]:
    videos = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
    audios = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
    if not videos:
        raise ConversionError("El MKV no contiene una pista de video.")
    return videos[0], audios[0] if audios else None


def create_media_hls(
    ffmpeg: Path, source: Path, output: Path, probe: dict, overwrite: bool
) -> None:
    playlist = output / "index.m3u8"
    if playlist.is_file() and not overwrite:
        print("El HLS de video ya existe; se conserva y se actualizan subtitulos/master.")
        return

    video, audio = video_settings(probe)
    codec = str(video.get("codec_name", "")).lower()
    command: list[str | Path] = [
        ffmpeg, "-hide_banner", "-y", "-i", source,
        "-map", f"0:{video['index']}",
    ]
    if audio:
        command.extend(["-map", f"0:{audio['index']}"])
    command.extend(["-sn", "-dn"])

    if codec == "h264":
        command.extend(["-c:v", "copy"])
        print("Video H.264 detectado: se copiara sin recomprimir.")
    else:
        print(f"Video {codec or 'desconocido'}: se convertira a H.264 para compatibilidad web.")
        command.extend([
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-force_key_frames", "expr:gte(t,n_forced*6)",
        ])
    if audio:
        command.extend(["-c:a", "aac", "-b:a", "128k", "-ac", "2"])
    command.extend([
        "-max_muxing_queue_size", "4096", "-f", "hls", "-hls_time", "6",
        "-hls_playlist_type", "vod", "-hls_flags", "independent_segments+temp_file",
        "-hls_segment_filename", output / "segmento_%05d.ts", playlist,
    ])
    run(command)


def parse_fraction(value: str) -> float | None:
    try:
        numerator, denominator = value.split("/", 1)
        result = float(numerator) / float(denominator)
        return result if result > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def create_master_playlist(output: Path, probe: dict, web_tracks: list[dict]) -> None:
    video, _ = video_settings(probe)
    bitrate = probe.get("format", {}).get("bit_rate") or video.get("bit_rate") or 4_000_000
    try:
        bandwidth = max(128_000, int(float(bitrate)))
    except (TypeError, ValueError):
        bandwidth = 4_000_000
    attributes = [f"BANDWIDTH={bandwidth}"]
    width, height = video.get("width"), video.get("height")
    if width and height:
        attributes.append(f"RESOLUTION={width}x{height}")
    frame_rate = parse_fraction(str(video.get("avg_frame_rate", "")))
    if frame_rate:
        attributes.append(f"FRAME-RATE={frame_rate:.3f}")
    if web_tracks:
        attributes.append('SUBTITLES="subs"')

    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    default_already_set = False
    for position, track in enumerate(web_tracks):
        is_default = track["default"] and not default_already_set
        if not default_already_set and position == 0 and not any(t["default"] for t in web_tracks):
            is_default = True
        default_already_set = default_already_set or is_default
        lines.append(
            '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",'
            f'NAME="{escape_m3u8(track["name"])}",'
            f'LANGUAGE="{escape_m3u8(track["language"])}",'
            f"DEFAULT={'YES' if is_default else 'NO'},AUTOSELECT=YES,"
            f"FORCED={'YES' if track['forced'] else 'NO'},URI=\"{track['playlist']}\""
        )
    lines.extend([f"#EXT-X-STREAM-INF:{','.join(attributes)}", "index.m3u8"])
    (output / "master.m3u8").write_text("\n".join(lines) + "\n", encoding="utf-8")


def process_file(
    ffmpeg: Path, ffprobe: Path, source: Path, cdn: str, overwrite: bool
) -> str:
    print("\n" + "=" * 72)
    print(f"Procesando: {source.name}")
    probe = probe_file(ffprobe, source)
    validate_mkv(source, probe)
    output = source.parent / f"{clean_output_name(source.stem)}.HLS"
    output.mkdir(parents=True, exist_ok=True)
    print(f"Salida:     {output}")

    web_tracks = extract_subtitles(ffmpeg, source, output, probe, overwrite)
    create_media_hls(ffmpeg, source, output, probe, overwrite)
    create_master_playlist(output, probe, web_tracks)

    url = "/".join([
        cdn.rstrip("/"), quote(source.parent.name, safe=""),
        quote(output.name, safe=""), "master.m3u8",
    ])
    print(f"LISTO: {url}")
    return url


def select_sources(path: Path, convert_all: bool) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise ConversionError(f"La ruta no existe: {path}")
    sources = sorted(
        (item for item in path.iterdir() if item.is_file() and item.suffix.lower() == ".mkv"),
        key=lambda item: item.name.lower(),
    )
    if not sources:
        raise ConversionError(f"No se encontraron archivos MKV en: {path}")
    if convert_all or not sys.stdin.isatty():
        return sources

    print("\nSelecciona que quieres convertir:")
    print("  1) Todos los archivos")
    for number, source in enumerate(sources, start=2):
        print(f"  {number}) {source.name}")
    while True:
        try:
            choice = int(input("\nEscribe el numero y presiona Enter: ").strip())
        except ValueError:
            print("Opcion invalida.")
            continue
        if choice == 1:
            return sources
        if 2 <= choice <= len(sources) + 1:
            return [sources[choice - 2]]
        print(f"Opcion invalida. Escribe un numero entre 1 y {len(sources) + 1}.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Valida MKV, extrae todas las pistas de subtitulos y genera HLS para MundoYuri."
    )
    parser.add_argument(
        "ruta", nargs="?", type=Path,
        help="Archivo MKV o carpeta. Sin argumento usa la carpeta del ejecutable/script.",
    )
    parser.add_argument("--all", action="store_true", help="Procesa todos los MKV sin mostrar el menu.")
    parser.add_argument("--overwrite", action="store_true", help="Vuelve a generar archivos existentes.")
    parser.add_argument("--cdn", default=DEFAULT_CDN, help=f"URL base (predeterminado: {DEFAULT_CDN}).")
    parser.add_argument("--ffmpeg-dir", type=Path, help="Carpeta que contiene ffmpeg y ffprobe.")
    parser.add_argument("--no-pause", action="store_true", help="No espera Enter al terminar.")
    return parser.parse_args()


def should_pause(args: argparse.Namespace) -> bool:
    return not args.no_pause and sys.stdin.isatty() and os.name == "nt"


def main() -> int:
    configure_console()
    args = parse_args()
    try:
        ffmpeg = find_program("ffmpeg", args.ffmpeg_dir)
        ffprobe = find_program("ffprobe", args.ffmpeg_dir)
        if not ffmpeg or not ffprobe:
            raise ConversionError(
                "No se encontraron ffmpeg y ffprobe. En Windows ejecuta:\n"
                "  winget install --id Gyan.FFmpeg --exact\n"
                "Despues abre una terminal nueva, o coloca ambos .exe junto al convertidor."
            )

        target = (args.ruta or application_dir()).expanduser().resolve()
        sources = select_sources(target, args.all)
        urls: list[str] = []
        failures: list[str] = []
        output_parent = sources[0].parent
        for source in sources:
            try:
                urls.append(process_file(ffmpeg, ffprobe, source, args.cdn, args.overwrite))
            except (ConversionError, subprocess.CalledProcessError, OSError) as exc:
                failures.append(f"{source.name}: {exc}")
                print(f"\nERROR: {failures[-1]}", file=sys.stderr)

        if urls:
            url_file = output_parent / "urls-cloudflare.txt"
            url_file.write_text("\n".join(urls) + "\n", encoding="utf-8")
            print(f"\nURLs guardadas en: {url_file}")
        if failures:
            print("\nArchivos con error:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1
        return 0
    except (ConversionError, OSError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if should_pause(args):
            try:
                input("\nPresiona Enter para cerrar...")
            except EOFError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())

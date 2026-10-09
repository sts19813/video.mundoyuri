#!/usr/bin/env python3
"""Convierte archivos de video a HLS y extrae sus pistas de subtitulos."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


DEFAULT_CDN = "https://video.mundoyuri.com"
HLS_SEGMENT_SECONDS = 20
SUPPORTED_VIDEO_EXTENSIONS = {
    ".3g2", ".3gp", ".avi", ".divx", ".flv", ".m2ts", ".m4v", ".mkv",
    ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".mxf", ".ogv", ".ts",
    ".vob", ".webm", ".wmv",
}
TEXT_SUBTITLE_CODECS = {
    "ass", "ssa", "subrip", "srt", "text", "mov_text", "webvtt",
}
RESOLUTION_CHOICES = {
    "original": {"label": "Conservar resolucion original", "height": None},
    "720p": {"label": "Redimensionar a maximo 720p", "height": 720},
    "480p": {"label": "Redimensionar a maximo 480p", "height": 480},
    "360p": {"label": "Redimensionar a maximo 360p", "height": 360},
}
QUALITY_CHOICES = {
    "original": {
        "label": "Conservar calidad cuando se pueda",
        "crf": 20, "audio_bitrate": "128k", "preset": "medium",
        "bandwidth": {2160: 12_000_000, 1080: 5_000_000, 720: 2_800_000, 480: 1_600_000, 360: 900_000},
    },
    "web": {
        "label": "Web ligera",
        "crf": 26, "audio_bitrate": "96k", "preset": "medium",
        "bandwidth": {2160: 4_500_000, 1080: 2_200_000, 720: 1_200_000, 480: 750_000, 360: 450_000},
    },
    "minimo": {
        "label": "Muy ligero",
        "crf": 30, "audio_bitrate": "64k", "preset": "slow",
        "bandwidth": {2160: 2_800_000, 1080: 1_400_000, 720: 800_000, 480: 500_000, 360: 320_000},
    },
}


class ConversionError(RuntimeError):
    pass


@dataclass(frozen=True)
class EncodingOptions:
    resolution: str
    quality: str

    @property
    def target_height(self) -> int | None:
        return RESOLUTION_CHOICES[self.resolution]["height"]

    @property
    def crf(self) -> int:
        return int(QUALITY_CHOICES[self.quality]["crf"])

    @property
    def audio_bitrate(self) -> str:
        return str(QUALITY_CHOICES[self.quality]["audio_bitrate"])

    @property
    def preset(self) -> str:
        return str(QUALITY_CHOICES[self.quality]["preset"])

    @property
    def can_copy_h264(self) -> bool:
        return self.resolution == "original" and self.quality == "original"

    def summary(self) -> str:
        return (
            f"{RESOLUTION_CHOICES[self.resolution]['label']} + "
            f"{QUALITY_CHOICES[self.quality]['label']}"
        )

    def as_json(self) -> dict:
        return {
            "resolution": self.resolution,
            "quality": self.quality,
            "target_height": self.target_height,
            "video_crf": self.crf,
            "audio_bitrate": self.audio_bitrate,
        }


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


def validate_source(source: Path) -> None:
    if source.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))
        raise ConversionError(
            f"Se omitio '{source.name}': la extension no es compatible "
            f"({supported})."
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


def video_settings(probe: dict) -> tuple[dict, list[dict]]:
    videos = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
    audios = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
    if not videos:
        raise ConversionError("El archivo no contiene una pista de video.")
    return videos[0], audios


def output_dimensions(video: dict, options: EncodingOptions) -> tuple[int | None, int | None]:
    width, height = video.get("width"), video.get("height")
    if not width or not height:
        return None, None
    width, height = int(width), int(height)
    target_height = options.target_height
    if not target_height or height <= target_height:
        return width, height
    scaled_width = max(2, int(round((width * target_height / height) / 2) * 2))
    return scaled_width, target_height


def estimated_bandwidth(probe: dict, video: dict, options: EncodingOptions) -> int:
    if options.quality == "original" and options.resolution == "original":
        bitrate = probe.get("format", {}).get("bit_rate") or video.get("bit_rate")
        try:
            return max(128_000, int(float(bitrate)))
        except (TypeError, ValueError):
            return 4_000_000

    _, height = output_dimensions(video, options)
    height = height or int(video.get("height") or 720)
    preset = QUALITY_CHOICES[options.quality]["bandwidth"]
    closest_height = min(preset, key=lambda value: abs(int(value) - height))
    return int(preset[closest_height])


def create_video_hls(
    ffmpeg: Path, source: Path, output: Path, probe: dict,
    options: EncodingOptions, overwrite: bool
) -> None:
    playlist = output / "index.m3u8"
    if playlist.is_file() and not overwrite:
        print("El HLS de video ya existe; se conserva y se actualizan pistas/master.")
        return

    video, _ = video_settings(probe)
    codec = str(video.get("codec_name", "")).lower()
    command: list[str | Path] = [
        ffmpeg, "-hide_banner", "-y", "-i", source,
        "-map", f"0:{video['index']}", "-an", "-sn", "-dn",
    ]

    if codec == "h264" and options.can_copy_h264:
        command.extend(["-c:v", "copy"])
        print("Video H.264 detectado: se copiara sin recomprimir.")
    else:
        reason = "compatibilidad web"
        if not options.can_copy_h264:
            reason = "aplicar el perfil de optimizacion"
        print(f"Video {codec or 'desconocido'}: se convertira a H.264 para {reason}.")
        target_height = options.target_height
        if target_height:
            command.extend(["-vf", f"scale=-2:min(ih\\,{target_height})"])
        command.extend([
            "-c:v", "libx264", "-preset", options.preset, "-crf", str(options.crf),
            "-pix_fmt", "yuv420p", "-force_key_frames",
            f"expr:gte(t,n_forced*{HLS_SEGMENT_SECONDS})",
        ])
    command.extend([
        "-max_muxing_queue_size", "4096", "-f", "hls",
        "-hls_time", str(HLS_SEGMENT_SECONDS),
        "-hls_playlist_type", "vod", "-hls_flags", "independent_segments+temp_file",
        "-hls_segment_filename", output / "segmento_%05d.ts", playlist,
    ])
    run(command)


def create_audio_hls(
    ffmpeg: Path, source: Path, output: Path, probe: dict,
    options: EncodingOptions, overwrite: bool
) -> list[dict]:
    _, audios = video_settings(probe)
    audio_root = output / "audios"
    audio_root.mkdir(parents=True, exist_ok=True)
    inventory: list[dict] = []
    web_tracks: list[dict] = []

    for number, stream in enumerate(audios, start=1):
        stream_index = int(stream["index"])
        codec = str(stream.get("codec_name", "desconocido"))
        language = metadata_value(stream, "language", "und").lower()
        title = metadata_value(stream, "title", f"Audio {number}")
        track_dir = audio_root / f"pista_{number:02d}"
        playlist_file = track_dir / "index.m3u8"
        track_dir.mkdir(parents=True, exist_ok=True)

        if overwrite or not playlist_file.is_file():
            run([
                ffmpeg, "-hide_banner", "-loglevel", "warning", "-y", "-i", source,
                "-map", f"0:{stream_index}", "-vn", "-sn", "-dn",
                "-c:a", "aac", "-b:a", options.audio_bitrate, "-ac", "2",
                "-max_muxing_queue_size", "4096", "-f", "hls",
                "-hls_time", str(HLS_SEGMENT_SECONDS),
                "-hls_playlist_type", "vod", "-hls_flags", "independent_segments+temp_file",
                "-hls_segment_filename", track_dir / "segmento_%05d.ts", playlist_file,
            ])
        else:
            print(f"El HLS de audio {number} ya existe; se conserva.")

        item = {
            "numero": number, "indice_ffmpeg": stream_index, "codec": codec,
            "idioma": language, "titulo": title,
            "playlist": f"audios/pista_{number:02d}/index.m3u8",
            "bitrate": options.audio_bitrate,
        }
        inventory.append(item)
        web_tracks.append({
            "number": number, "language": language,
            "name": title or f"Audio {number}",
            "default": bool(stream.get("disposition", {}).get("default", 0)),
            "playlist": item["playlist"],
        })

    (audio_root / "pistas.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return web_tracks


def parse_fraction(value: str) -> float | None:
    try:
        numerator, denominator = value.split("/", 1)
        result = float(numerator) / float(denominator)
        return result if result > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def create_master_playlist(
    output: Path, probe: dict, subtitle_tracks: list[dict],
    audio_tracks: list[dict], options: EncodingOptions
) -> None:
    video, _ = video_settings(probe)
    bandwidth = estimated_bandwidth(probe, video, options)
    attributes = [f"BANDWIDTH={bandwidth}"]
    width, height = output_dimensions(video, options)
    if width and height:
        attributes.append(f"RESOLUTION={width}x{height}")
    frame_rate = parse_fraction(str(video.get("avg_frame_rate", "")))
    if frame_rate:
        attributes.append(f"FRAME-RATE={frame_rate:.3f}")
    if audio_tracks:
        attributes.append('AUDIO="audios"')
    if subtitle_tracks:
        attributes.append('SUBTITLES="subs"')

    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]

    default_audio_set = False
    for position, track in enumerate(audio_tracks):
        is_default = track["default"] and not default_audio_set
        if not default_audio_set and position == 0 and not any(t["default"] for t in audio_tracks):
            is_default = True
        default_audio_set = default_audio_set or is_default
        lines.append(
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audios",'
            f'NAME="{escape_m3u8(track["name"])}",'
            f'LANGUAGE="{escape_m3u8(track["language"])}",'
            f"DEFAULT={'YES' if is_default else 'NO'},AUTOSELECT=YES,"
            f'URI="{track["playlist"]}"'
        )

    default_already_set = False
    for position, track in enumerate(subtitle_tracks):
        is_default = track["default"] and not default_already_set
        if not default_already_set and position == 0 and not any(t["default"] for t in subtitle_tracks):
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
    ffmpeg: Path, ffprobe: Path, source: Path, cdn: str,
    options: EncodingOptions, overwrite: bool
) -> str:
    print("\n" + "=" * 72)
    print(f"Procesando: {source.name}")
    validate_source(source)
    probe = probe_file(ffprobe, source)
    output = source.parent / f"{clean_output_name(source.stem)}.HLS"
    output.mkdir(parents=True, exist_ok=True)
    print(f"Salida:     {output}")
    print(f"Perfil:     {options.summary()}")

    subtitle_tracks = extract_subtitles(ffmpeg, source, output, probe, overwrite)
    audio_tracks = create_audio_hls(ffmpeg, source, output, probe, options, overwrite)
    create_video_hls(ffmpeg, source, output, probe, options, overwrite)
    create_master_playlist(output, probe, subtitle_tracks, audio_tracks, options)
    (output / "opciones.json").write_text(
        json.dumps(options.as_json(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    url = "/".join([
        cdn.rstrip("/"), quote(source.parent.name, safe=""),
        quote(output.name, safe=""), "master.m3u8",
    ])
    print(f"LISTO: {url}")
    return url


def prompt_choice(title: str, choices: list[tuple[str, str]], default: int = 1) -> str:
    print(f"\n{title}")
    for number, (_, label) in enumerate(choices, start=1):
        suffix = " [Enter]" if number == default else ""
        print(f"  {number}) {label}{suffix}")
    while True:
        raw = input("\nEscribe el numero y presiona Enter: ").strip()
        if not raw:
            return choices[default - 1][0]
        try:
            choice = int(raw)
        except ValueError:
            print("Opcion invalida.")
            continue
        if 1 <= choice <= len(choices):
            return choices[choice - 1][0]
        print(f"Opcion invalida. Escribe un numero entre 1 y {len(choices)}.")


def choose_processing_options(args: argparse.Namespace) -> EncodingOptions:
    resolution = args.resolution
    quality = args.quality
    if sys.stdin.isatty():
        if not resolution:
            resolution = prompt_choice(
                "Resolucion de salida:",
                [(key, str(value["label"])) for key, value in RESOLUTION_CHOICES.items()],
                default=2,
            )
        if not quality:
            quality = prompt_choice(
                "Calidad / peso del archivo:",
                [(key, str(value["label"])) for key, value in QUALITY_CHOICES.items()],
                default=2,
            )
        if not args.overwrite:
            overwrite_choice = prompt_choice(
                "Si ya existe una salida .HLS:",
                [
                    ("no", "Conservarla y solo completar pistas/master"),
                    ("yes", "Regenerarla para aplicar estas opciones"),
                ],
                default=1,
            )
            args.overwrite = overwrite_choice == "yes"

    return EncodingOptions(
        resolution=resolution or "original",
        quality=quality or "original",
    )


def parse_source_selection(raw: str, amount: int) -> list[int]:
    selected: set[int] = set()
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "-" in piece:
            start_raw, end_raw = piece.split("-", 1)
            start, end = int(start_raw), int(end_raw)
            if start > end:
                start, end = end, start
            selected.update(range(start, end + 1))
        else:
            selected.add(int(piece))
    if not selected:
        raise ValueError
    if any(value < 2 or value > amount + 1 for value in selected):
        raise ValueError
    return sorted(selected)


def select_sources(path: Path, convert_all: bool) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise ConversionError(f"La ruta no existe: {path}")
    sources = sorted(
        (
            item for item in path.iterdir()
            if item.is_file() and item.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
        ),
        key=lambda item: item.name.lower(),
    )
    if not sources:
        supported = ", ".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))
        raise ConversionError(f"No se encontraron archivos de video ({supported}) en: {path}")
    if convert_all or not sys.stdin.isatty():
        return sources

    print("\nSelecciona que quieres convertir:")
    print("  1) Todos los archivos")
    for number, source in enumerate(sources, start=2):
        print(f"  {number}) {source.name}")
    while True:
        raw = input(
            "\nEscribe 1 para todos, un numero, o varios separados por coma "
            "(ej. 2,4-6): "
        ).strip()
        if raw == "1":
            return sources
        try:
            choices = parse_source_selection(raw, len(sources))
        except ValueError:
            print(f"Opcion invalida. Usa numeros entre 1 y {len(sources) + 1}.")
            continue
        return [sources[choice - 2] for choice in choices]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Valida videos, extrae pistas de subtitulos y genera HLS para MundoYuri."
    )
    parser.add_argument(
        "ruta", nargs="?", type=Path,
        help="Archivo de video o carpeta. Sin argumento usa la carpeta del ejecutable/script.",
    )
    parser.add_argument("--all", action="store_true", help="Procesa todos los videos sin mostrar el menu.")
    parser.add_argument("--overwrite", action="store_true", help="Vuelve a generar archivos existentes.")
    parser.add_argument("--cdn", default=DEFAULT_CDN, help=f"URL base (predeterminado: {DEFAULT_CDN}).")
    parser.add_argument("--ffmpeg-dir", type=Path, help="Carpeta que contiene ffmpeg y ffprobe.")
    parser.add_argument(
        "--resolution", choices=RESOLUTION_CHOICES,
        help="Resolucion de salida sin menu: original, 720p, 480p o 360p.",
    )
    parser.add_argument(
        "--quality", choices=QUALITY_CHOICES,
        help="Calidad/peso sin menu: original, web o minimo.",
    )
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
        options = choose_processing_options(args)
        sources = select_sources(target, args.all)
        urls: list[str] = []
        failures: list[str] = []
        output_parent = sources[0].parent
        for source in sources:
            try:
                urls.append(process_file(
                    ffmpeg, ffprobe, source, args.cdn, options, args.overwrite
                ))
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

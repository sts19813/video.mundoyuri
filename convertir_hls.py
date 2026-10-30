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
HLS_SEGMENT_SECONDS = 25
AUDIO_TIMELINE_VERSION = 4
AUDIO_ASSET_VERSION = ""
HLS_LAYOUT_MUXED_SINGLE_AUDIO = "muxed_single_audio"
HLS_LAYOUT_SEPARATE_MULTI_AUDIO = "separate_multi_audio"
HLS_LAYOUT_VIDEO_ONLY = "video_only"
HLS_LAYOUTS = {
    HLS_LAYOUT_MUXED_SINGLE_AUDIO,
    HLS_LAYOUT_SEPARATE_MULTI_AUDIO,
    HLS_LAYOUT_VIDEO_ONLY,
}
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
        "label": "Web equilibrada",
        "crf": 26, "audio_bitrate": "96k", "preset": "medium",
        "bandwidth": {2160: 4_500_000, 1080: 2_200_000, 720: 1_200_000, 480: 750_000, 360: 450_000},
    },
    "web_rapido": {
        "label": "Web rápida (prioriza tiempo)",
        "crf": 27, "audio_bitrate": "80k", "preset": "fast",
        "bandwidth": {2160: 3_500_000, 1080: 1_800_000, 720: 1_000_000, 480: 600_000, 360: 350_000},
    },
    "web_ligero": {
        "label": "Web ligera (prioriza tamaño)",
        "crf": 28, "audio_bitrate": "80k", "preset": "slow",
        "bandwidth": {2160: 2_800_000, 1080: 1_500_000, 720: 850_000, 480: 500_000, 360: 300_000},
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
    encoder: str

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
            f"{QUALITY_CHOICES[self.quality]['label']} + "
            f"{encoder_label(self.encoder)}"
        )

    def as_json(self) -> dict:
        return {
            "resolution": self.resolution,
            "quality": self.quality,
            "target_height": self.target_height,
            "video_crf": self.crf,
            "audio_bitrate": self.audio_bitrate,
            "video_encoder": self.encoder,
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


def available_video_encoders(ffmpeg: Path) -> set[str]:
    """Devuelve los codificadores de video publicados por este FFmpeg."""
    try:
        result = run([ffmpeg, "-hide_banner", "-encoders"], capture=True)
    except (subprocess.CalledProcessError, OSError):
        return set()
    encoders: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            encoders.add(parts[1])
    return encoders


def hardware_encoder(available: set[str]) -> str | None:
    """Elige el acelerador nativo de la plataforma, si FFmpeg lo incluye."""
    candidates = ("h264_videotoolbox", "h264_nvenc") if sys.platform == "darwin" else ("h264_nvenc",)
    return next((encoder for encoder in candidates if encoder in available), None)


def encoder_label(encoder: str) -> str:
    labels = {
        "libx264": "software (libx264)",
        "h264_videotoolbox": "hardware (VideoToolbox)",
        "h264_nvenc": "hardware (NVIDIA NVENC)",
    }
    return labels.get(encoder, encoder)


def resolve_video_encoder(ffmpeg: Path, preference: str) -> str:
    available = available_video_encoders(ffmpeg)
    if preference == "software":
        if "libx264" not in available:
            raise ConversionError("Este FFmpeg no incluye libx264 para codificar H.264 por software.")
        return "libx264"
    selected = hardware_encoder(available)
    if selected:
        return selected
    platform = "VideoToolbox" if sys.platform == "darwin" else "NVIDIA NVENC"
    raise ConversionError(
        f"No se encontro {platform} en este FFmpeg. Elige software o instala/actualiza FFmpeg y el controlador de video."
    )


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


def audio_encode_args(
    stream: dict, bitrate: str, *, prefer_copy: bool,
    preserve_timestamps: bool = False,
) -> tuple[list[str], str]:
    codec = str(stream.get("codec_name", "")).lower()
    if prefer_copy and codec == "aac":
        return ["-c:a", "copy"], "copy"
    # La salida multiplexada comparte reloj con el video. No se debe forzar
    # el primer PTS a cero: una pista con retraso intencional conservara su
    # posicion relativa al recodificarse. Las pistas externas mantienen la
    # normalizacion historica y su ajuste contra la linea de tiempo de video.
    resample = "aresample=async=1" if preserve_timestamps else "aresample=async=1:first_pts=0"
    return [
        "-af", resample,
        "-c:a", "aac", "-b:a", bitrate, "-ac", "2",
    ], bitrate


def escape_m3u8(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', "\\\"").replace("\r", " ").replace("\n", " ")


def duration_seconds(probe: dict) -> float:
    raw = probe.get("format", {}).get("duration", 0)
    try:
        return max(float(raw), 0.001)
    except (TypeError, ValueError):
        return 1.0


def existing_audio_timeline_version(audio_root: Path) -> int:
    metadata_file = audio_root / "pistas.json"
    if not metadata_file.is_file():
        return 0
    try:
        data = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(data, list) or not data:
        return 0
    versions: list[int] = []
    for item in data:
        if not isinstance(item, dict):
            return 0
        try:
            versions.append(int(item.get("timeline_version", 0)))
        except (TypeError, ValueError):
            return 0
    return min(versions, default=0)


def playlist_first_dts(ffprobe: Path, playlist: Path, stream_selector: str) -> float:
    if not playlist.is_file():
        return 0.0
    try:
        result = run([
            ffprobe, "-v", "error", "-select_streams", stream_selector,
            "-show_entries", "packet=dts_time,pts_time", "-read_intervals", "%+1",
            "-of", "json", playlist,
        ], capture=True)
        packets = json.loads(result.stdout).get("packets", [])
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return 0.0
    if not packets:
        return 0.0
    for key in ("dts_time", "pts_time"):
        try:
            return max(float(packets[0].get(key, 0)), 0.0)
        except (TypeError, ValueError):
            continue
    return 0.0


def playlist_durations(playlist: Path) -> list[float]:
    if not playlist.is_file():
        return []
    durations: list[float] = []
    for match in re.finditer(
        r"^#EXTINF:([0-9]+(?:\.[0-9]+)?)",
        playlist.read_text(encoding="utf-8", errors="replace"),
        flags=re.MULTILINE,
    ):
        durations.append(float(match.group(1)))
    return durations


def cumulative_segment_times(durations: list[float]) -> list[float]:
    total = 0.0
    times: list[float] = []
    for duration in durations[:-1]:
        total += duration
        times.append(total)
    return times


def clean_hls_segments(folder: Path, patterns: tuple[str, ...]) -> None:
    for pattern in patterns:
        for item in folder.glob(pattern):
            if item.is_file():
                item.unlink()


def add_playlist_asset_version(playlist: Path, version: str) -> None:
    if not version:
        return
    if not playlist.is_file():
        return
    lines: list[str] = []
    for line in playlist.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "?" not in stripped:
            line = f"{line}?v={version}"
        lines.append(line)
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", source,
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
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", source,
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


def hls_layout_for(audios: list[dict]) -> str:
    """El formato HLS depende del numero de alternativas de audio."""
    if len(audios) == 1:
        return HLS_LAYOUT_MUXED_SINGLE_AUDIO
    if len(audios) > 1:
        return HLS_LAYOUT_SEPARATE_MULTI_AUDIO
    return HLS_LAYOUT_VIDEO_ONLY


def saved_hls_layout(output: Path) -> str | None:
    """Lee el formato declarado por versiones nuevas del conversor."""
    settings_file = output / "opciones.json"
    if not settings_file.is_file():
        return None
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    layout = settings.get("hls_layout") if isinstance(settings, dict) else None
    return layout if layout in HLS_LAYOUTS else None


def saved_hls_segment_seconds(output: Path) -> int | None:
    """Devuelve el intervalo declarado por una conversion anterior, si existe."""
    settings_file = output / "opciones.json"
    if not settings_file.is_file():
        return None
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        seconds = int(settings.get("hls_segment_seconds"))
    except (OSError, json.JSONDecodeError, AttributeError, TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def detected_hls_layout(ffprobe: Path, output: Path) -> str | None:
    """Reconoce salidas antiguas que no tenian metadatos de formato.

    Una playlist principal con audio es la nueva salida multiplexada. Las
    conversiones antiguas usaban un indice solo video y el arbol ``audios``.
    """
    declared = saved_hls_layout(output)
    if declared:
        return declared
    playlist = output / "index.m3u8"
    if not playlist.is_file():
        return None
    try:
        playlist_probe = probe_file(ffprobe, playlist)
    except ConversionError:
        # Una conversion antigua incompleta no se puede reutilizar con
        # seguridad; el llamador pedira --overwrite si el formato no coincide.
        playlist_probe = {}
    streams = playlist_probe.get("streams", [])
    if any(stream.get("codec_type") == "audio" for stream in streams):
        return HLS_LAYOUT_MUXED_SINGLE_AUDIO
    audio_root = output / "audios"
    if (audio_root / "pistas.json").is_file() or any(audio_root.glob("pista_*/index.m3u8")):
        return HLS_LAYOUT_SEPARATE_MULTI_AUDIO
    return HLS_LAYOUT_VIDEO_ONLY


def ensure_compatible_existing_hls(
    ffprobe: Path, output: Path, desired_layout: str, overwrite: bool
) -> None:
    """Evita mezclar segmentos y manifests de layouts incompatibles."""
    existing_layout = detected_hls_layout(ffprobe, output)
    if (
        existing_layout
        and existing_layout != desired_layout
        and not overwrite
    ):
        raise ConversionError(
            "La salida HLS existente usa el formato "
            f"'{existing_layout}', pero esta fuente requiere '{desired_layout}'. "
            "No se modifico para no mezclar segmentos antiguos. "
            "Vuelve a ejecutar con --overwrite para regenerar el video."
        )


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


def audio_bandwidth(stream: dict, options: EncodingOptions) -> int:
    """Bitrate anunciado para la unica pista activa de una variante HLS."""
    if options.quality == "original" and str(stream.get("codec_name", "")).lower() == "aac":
        try:
            return max(32_000, int(float(stream.get("bit_rate", 0))))
        except (TypeError, ValueError):
            pass
    return int(options.audio_bitrate.rstrip("kK")) * 1_000


def video_encode_args(probe: dict, video: dict, options: EncodingOptions) -> list[str]:
    """Argumentos de codificación de video para CPU o aceleración de hardware."""
    if options.encoder == "libx264":
        return [
            "-c:v", "libx264", "-preset", options.preset,
            "-crf", str(options.crf),
        ]

    # Los codificadores de hardware no comparten el CRF de x264. Usar el
    # bitrate objetivo del perfil da un resultado predecible y compatible
    # entre VideoToolbox y NVENC, sin alterar la linea de tiempo HLS.
    bitrate = estimated_bandwidth(probe, video, options)
    return ["-c:v", options.encoder, "-b:v", str(bitrate)]


def create_video_hls(
    ffmpeg: Path, source: Path, output: Path, probe: dict,
    options: EncodingOptions, overwrite: bool, layout: str
) -> bool:
    playlist = output / "index.m3u8"
    if playlist.is_file() and not overwrite:
        print("El HLS de video ya existe; se conserva y se actualizan pistas/master.")
        return False

    video, audios = video_settings(probe)
    codec = str(video.get("codec_name", "")).lower()
    clean_hls_segments(output, ("segmento_*.ts", "segmento_*.m4s", "init.mp4"))
    command: list[str | Path] = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-stats", "-y",
        "-copyts", "-start_at_zero", "-i", source,
        "-map", f"0:{video['index']}", "-sn", "-dn",
    ]
    if layout == HLS_LAYOUT_MUXED_SINGLE_AUDIO:
        # Solo hay una alternativa: incluirla en cada segmento evita que el
        # reproductor tenga que descargar una playlist y un TS extra por tramo.
        audio = audios[0]
        command.extend(["-map", f"0:{audio['index']}"])
        audio_args, _ = audio_encode_args(
            audio, options.audio_bitrate, prefer_copy=options.quality == "original",
            preserve_timestamps=True,
        )
        command.extend(audio_args)
    else:
        command.append("-an")

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
            *video_encode_args(probe, video, options),
            "-pix_fmt", "yuv420p", "-force_key_frames",
            f"expr:gte(t,n_forced*{HLS_SEGMENT_SECONDS})",
        ])
    command.extend([
        "-max_muxing_queue_size", "4096",
        "-muxpreload", "0", "-muxdelay", "0",
        "-f", "hls",
        "-hls_time", str(HLS_SEGMENT_SECONDS),
        "-hls_playlist_type", "vod", "-hls_flags", "independent_segments+temp_file",
        "-hls_segment_filename", output / "segmento_%05d.ts", playlist,
    ])
    run(command)
    return True


def create_audio_hls(
    ffmpeg: Path, ffprobe: Path, source: Path, output: Path, probe: dict,
    options: EncodingOptions, overwrite: bool
) -> list[dict]:
    _, audios = video_settings(probe)
    if len(audios) < 2:
        return []
    audio_root = output / "audios"
    audio_root.mkdir(parents=True, exist_ok=True)
    timeline_is_current = (
        existing_audio_timeline_version(audio_root) >= AUDIO_TIMELINE_VERSION
    )
    video_playlist = output / "index.m3u8"
    video_timeline_offset = playlist_first_dts(ffprobe, video_playlist, "v:0")
    video_segment_times = cumulative_segment_times(playlist_durations(video_playlist))
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
        refresh_timeline = playlist_file.is_file() and not overwrite and not timeline_is_current
        stored_bitrate = options.audio_bitrate

        if overwrite or not playlist_file.is_file() or refresh_timeline:
            if refresh_timeline:
                print(f"El HLS de audio {number} se regenerara para corregir sincronizacion.")
            if playlist_file.is_file():
                clean_hls_segments(track_dir, ("segmento_*.m4s", "segmento_*.ts", "init.mp4", "index.m3u8"))
            audio_args, stored_bitrate = audio_encode_args(
                stream, options.audio_bitrate, prefer_copy=options.quality == "original"
            )
            command: list[str | Path] = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-stats", "-y",
                "-copyts", "-start_at_zero", "-i", source,
                "-map", f"0:{stream_index}", "-vn", "-sn", "-dn",
                *audio_args,
                "-max_muxing_queue_size", "4096",
                "-muxpreload", "0", "-muxdelay", "0",
            ]
            if video_timeline_offset > 0.001:
                command.extend(["-output_ts_offset", f"{video_timeline_offset:.6f}"])
            command.extend([
                "-f", "segment",
                "-segment_list", playlist_file,
                "-segment_list_type", "m3u8",
                "-segment_format", "mpegts",
                "-reset_timestamps", "0",
                "-break_non_keyframes", "1",
            ])
            if video_segment_times:
                command.extend([
                    "-segment_times",
                    ",".join(f"{time:.6f}" for time in video_segment_times),
                ])
            else:
                command.extend(["-segment_time", str(HLS_SEGMENT_SECONDS)])
            command.append(track_dir / "segmento_%05d.ts")
            run(command)
            audio_timeline_offset = playlist_first_dts(ffprobe, playlist_file, "a:0")
            timeline_delta = abs(video_timeline_offset - audio_timeline_offset)
            if timeline_delta > 0.25:
                raise ConversionError(
                    f"Audio {number} quedo desalineado "
                    f"({timeline_delta:.3f}s contra el video)."
                )
            add_playlist_asset_version(playlist_file, AUDIO_ASSET_VERSION)
        else:
            print(f"El HLS de audio {number} ya existe; se conserva.")

        item = {
            "numero": number, "indice_ffmpeg": stream_index, "codec": codec,
            "idioma": language, "titulo": title,
            "playlist": f"audios/pista_{number:02d}/index.m3u8",
            "bitrate": stored_bitrate,
            "timeline_version": AUDIO_TIMELINE_VERSION,
            "video_timeline_offset": round(video_timeline_offset, 6),
            "segmentacion": "alineada_al_video",
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
    audio_tracks: list[dict], options: EncodingOptions, layout: str
) -> None:
    video, audios = video_settings(probe)
    bandwidth = estimated_bandwidth(probe, video, options)
    # Los perfiles web estiman el video. Para una variante con audio, anunciar
    # tambien la pista que el reproductor descargara junto con ella. El bitrate
    # del contenedor original ya incluye audio, por lo que no se suma dos veces.
    if layout != HLS_LAYOUT_VIDEO_ONLY and not (
        options.quality == "original" and options.resolution == "original"
    ) and audios:
        bandwidth += audio_bandwidth(audios[0], options)
    attributes = [f"BANDWIDTH={bandwidth}"]
    width, height = output_dimensions(video, options)
    if width and height:
        attributes.append(f"RESOLUTION={width}x{height}")
    frame_rate = parse_fraction(str(video.get("avg_frame_rate", "")))
    if frame_rate:
        attributes.append(f"FRAME-RATE={frame_rate:.3f}")
    if layout == HLS_LAYOUT_SEPARATE_MULTI_AUDIO and audio_tracks:
        attributes.append('AUDIO="audios"')
    if subtitle_tracks:
        attributes.append('SUBTITLES="subs"')

    hls_version = 7 if layout == HLS_LAYOUT_SEPARATE_MULTI_AUDIO and audio_tracks else 3
    lines = ["#EXTM3U", f"#EXT-X-VERSION:{hls_version}"]

    default_audio_set = False
    if layout == HLS_LAYOUT_SEPARATE_MULTI_AUDIO:
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

    _, audios = video_settings(probe)
    layout = hls_layout_for(audios)
    ensure_compatible_existing_hls(ffprobe, output, layout, overwrite)
    layout_description = {
        HLS_LAYOUT_MUXED_SINGLE_AUDIO: "video y unica pista de audio integrados",
        HLS_LAYOUT_SEPARATE_MULTI_AUDIO: "video y pistas de audio alternativas separadas",
        HLS_LAYOUT_VIDEO_ONLY: "solo video (sin pistas de audio)",
    }[layout]
    print(f"Formato HLS: {layout_description}.")

    video_was_regenerated = create_video_hls(
        ffmpeg, source, output, probe, options, overwrite, layout
    )
    subtitle_tracks = extract_subtitles(ffmpeg, source, output, probe, overwrite)
    audio_tracks = (
        create_audio_hls(ffmpeg, ffprobe, source, output, probe, options, overwrite)
        if layout == HLS_LAYOUT_SEPARATE_MULTI_AUDIO else []
    )
    create_master_playlist(output, probe, subtitle_tracks, audio_tracks, options, layout)
    saved_options = options.as_json()
    saved_options.update({
        "hls_layout": layout,
        "audio_track_count": len(audios),
    })
    # No se debe afirmar que una salida conservada paso de 20 a 25 segundos.
    # Solo --overwrite (o una salida nueva) reconstruye video y audio con el
    # nuevo mapa de segmentos.
    if video_was_regenerated:
        saved_options["hls_segment_seconds"] = HLS_SEGMENT_SECONDS
    else:
        previous_segment_seconds = saved_hls_segment_seconds(output)
        if previous_segment_seconds:
            saved_options["hls_segment_seconds"] = previous_segment_seconds
    (output / "opciones.json").write_text(
        json.dumps(saved_options, ensure_ascii=False, indent=2) + "\n",
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


def choose_processing_options(args: argparse.Namespace, ffmpeg: Path) -> EncodingOptions:
    resolution = args.resolution
    quality = args.quality
    encoder_preference = args.encoder
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
        if not encoder_preference:
            available = available_video_encoders(ffmpeg)
            choices = [("software", "Software (libx264: mejor eficiencia/calidad)")]
            selected_hardware = hardware_encoder(available)
            if selected_hardware:
                choices.append(("hardware", f"Hardware ({encoder_label(selected_hardware)}: más rápido)"))
            encoder_preference = prompt_choice(
                "Motor de video:", choices, default=1,
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

    preference = encoder_preference or "software"
    return EncodingOptions(
        resolution=resolution or "original",
        quality=quality or "original",
        encoder=resolve_video_encoder(ffmpeg, preference),
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
        help="Calidad/peso sin menu: original, web, web_rapido, web_ligero o minimo.",
    )
    parser.add_argument(
        "--encoder", choices=("software", "hardware"),
        help="Motor de video: software (libx264) o hardware (VideoToolbox/NVENC).",
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
        options = choose_processing_options(args, ffmpeg)
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

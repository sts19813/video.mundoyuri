#!/usr/bin/env python3
"""Regenera solo audios HLS alineados al video ya publicado."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse


AUDIO_TIMELINE_VERSION = 4
AUDIO_ASSET_VERSION = "audio4-20261011"
HLS_SEGMENT_SECONDS = 20
VIDEO_EXTENSIONS = {
    ".3g2", ".3gp", ".avi", ".divx", ".flv", ".m2ts", ".m4v", ".mkv",
    ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".mxf", ".ogv", ".ts",
    ".vob", ".webm", ".wmv",
}


class RepairError(RuntimeError):
    pass


def run(command: list[str | Path], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    printable = " ".join(f'"{part}"' if " " in str(part) else str(part) for part in command)
    print(f"\n> {printable}")
    return subprocess.run(
        [str(part) for part in command], check=True, text=True,
        encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def remote_hls_key(master_url: str) -> str:
    path = unquote(urlparse(master_url).path.lstrip("/"))
    if not path.endswith("/master.m3u8"):
        raise RepairError(f"URL no parece master.m3u8: {master_url}")
    return path[:-len("/master.m3u8")]


def normalize_name(value: str) -> str:
    value = value.lower()
    value = re.sub(r"\.hls$", "", value)
    value = re.sub(r"\b(uncut|ver|fixed|repack|gaga|wetv|iq|x264|x265|h264|h265|hevc|web|dl|1080p|720p|480p|bd|pv)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def score_source(row: dict[str, str], source: Path) -> int:
    key_stem = normalize_name(Path(remote_hls_key(row["master_url"])).stem)
    source_stem = normalize_name(source.stem)
    slug = normalize_name(row.get("episode_slug", ""))
    score = 0
    if key_stem and key_stem == source_stem:
        score += 100
    if key_stem and (key_stem in source_stem or source_stem in key_stem):
        score += 60
    key_tokens = set(key_stem.split())
    source_tokens = set(source_stem.split())
    score += len(key_tokens & source_tokens) * 5
    slug_tokens = set(slug.split())
    score += len(slug_tokens & source_tokens) * 2
    return score


def find_sources(root: Path) -> list[Path]:
    return [
        item for item in root.rglob("*")
        if item.is_file()
        and item.suffix.lower() in VIDEO_EXTENSIONS
        and ".HLS" not in {part.upper() for part in item.parts}
    ]


def best_source(row: dict[str, str], sources: list[Path]) -> tuple[Path | None, int]:
    scored = sorted(((score_source(row, source), source) for source in sources), reverse=True)
    if not scored or scored[0][0] < 25:
        return None, scored[0][0] if scored else 0
    return scored[0][1], scored[0][0]


def probe_file(ffprobe: Path, source: Path) -> dict:
    result = run([
        ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", source,
    ], capture=True)
    return json.loads(result.stdout)


def metadata_value(stream: dict, key: str, fallback: str = "") -> str:
    return str(stream.get("tags", {}).get(key, fallback)).strip()


def audio_encode_args(stream: dict, bitrate: str) -> tuple[list[str], str]:
    codec = str(stream.get("codec_name", "")).lower()
    normalized = bitrate.lower()
    if normalized in {"copy", "original"}:
        if codec != "aac":
            raise RepairError(
                f"No puedo copiar audio {codec or 'desconocido'} para HLS web; "
                "usa --audio-bitrate 96k o 128k para convertir a AAC."
            )
        return ["-c:a", "copy"], "copy"
    return [
        "-af", "aresample=async=1:first_pts=0",
        "-c:a", "aac", "-b:a", bitrate, "-ac", "2",
    ], bitrate


def playlist_first_dts(ffprobe: Path, playlist: Path) -> float:
    result = run([
        ffprobe, "-v", "error", "-select_streams", "a:0",
        "-show_entries", "packet=dts_time,pts_time", "-read_intervals", "%+1",
        "-of", "json", playlist,
    ], capture=True)
    packets = json.loads(result.stdout).get("packets", [])
    if not packets:
        return 0.0
    for key in ("dts_time", "pts_time"):
        try:
            return max(float(packets[0].get(key, 0)), 0.0)
        except (TypeError, ValueError):
            continue
    return 0.0


def read_url(url: str) -> str:
    from urllib.request import Request, urlopen

    request = Request(url, headers={"User-Agent": "MundoYuri-HLS-Repair/1.0"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def playlist_durations_from_text(playlist: str) -> list[float]:
    return [
        float(match.group(1))
        for match in re.finditer(r"^#EXTINF:([0-9]+(?:\.[0-9]+)?)", playlist, flags=re.MULTILINE)
    ]


def cumulative_segment_times(durations: list[float]) -> list[float]:
    total = 0.0
    times: list[float] = []
    for duration in durations[:-1]:
        total += duration
        times.append(total)
    return times


def clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def add_playlist_asset_version(playlist: Path, version: str) -> None:
    lines: list[str] = []
    for line in playlist.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "?" not in stripped:
            line = f"{line}?v={version}"
        lines.append(line)
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")


def repair_audio(
    ffmpeg: Path, ffprobe: Path, row: dict[str, str],
    source: Path, output_root: Path, audio_bitrate: str
) -> Path:
    key = remote_hls_key(row["master_url"])
    target = output_root / key
    audio_root = target / "audios"
    audio_root.mkdir(parents=True, exist_ok=True)

    probe = probe_file(ffprobe, source)
    audios = [stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"]
    if not audios:
        raise RepairError(f"Sin pistas de audio: {source}")

    try:
        video_offset = max(float(row.get("video_first_dts") or 0), 0.0)
    except ValueError:
        video_offset = 0.0
    video_playlist_url = row["master_url"].rsplit("/", 1)[0] + "/index.m3u8"
    video_segment_times = cumulative_segment_times(
        playlist_durations_from_text(read_url(video_playlist_url + "?v=audio4-segment-map"))
    )

    inventory: list[dict[str, object]] = []
    for number, stream in enumerate(audios, start=1):
        stream_index = int(stream["index"])
        language = metadata_value(stream, "language", "und").lower() or "und"
        title = metadata_value(stream, "title", f"Audio {number}") or f"Audio {number}"
        track_dir = audio_root / f"pista_{number:02d}"
        clean_dir(track_dir)
        playlist = track_dir / "index.m3u8"
        audio_args, stored_bitrate = audio_encode_args(stream, audio_bitrate)
        command: list[str | Path] = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-stats", "-y",
            "-copyts", "-start_at_zero", "-i", source,
            "-map", f"0:{stream_index}", "-vn", "-sn", "-dn",
            *audio_args,
            "-max_muxing_queue_size", "4096",
            "-muxpreload", "0", "-muxdelay", "0",
        ]
        if video_offset > 0.001:
            command.extend(["-output_ts_offset", f"{video_offset:.6f}"])
        command.extend([
            "-f", "segment",
            "-segment_list", playlist,
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

        audio_offset = playlist_first_dts(ffprobe, playlist)
        delta = abs(video_offset - audio_offset)
        if delta > 0.25:
            raise RepairError(
                f"{row.get('episode_slug', key)} pista {number} quedo desalineada: "
                f"video={video_offset:.3f}, audio={audio_offset:.3f}, delta={delta:.3f}"
            )
        add_playlist_asset_version(playlist, AUDIO_ASSET_VERSION)
        inventory.append({
            "numero": number,
            "indice_ffmpeg": stream_index,
            "codec": str(stream.get("codec_name", "desconocido")),
            "idioma": language,
            "titulo": title,
            "playlist": f"audios/pista_{number:02d}/index.m3u8",
            "bitrate": stored_bitrate,
            "timeline_version": AUDIO_TIMELINE_VERSION,
            "video_timeline_offset": round(video_offset, 6),
            "segmentacion": "alineada_al_video",
        })

    (audio_root / "pistas.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def load_rows(path: Path, only_slug: str | None) -> list[dict[str, str]]:
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    rows = [row for row in rows if row.get("risk") in {"probable", "potencial"}]
    if only_slug:
        rows = [row for row in rows if row.get("episode_slug") == only_slug]
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Crea parches de solo audio para HLS ya subidos.")
    parser.add_argument("--report", type=Path, default=Path("reporte_hls_mal_por_fecha.csv"))
    parser.add_argument("--source-root", type=Path, default=Path("~/Downloads").expanduser())
    parser.add_argument("--output-root", type=Path, default=Path("parches_audio_b2"))
    parser.add_argument("--slug", help="Repara solo un episodio por slug.")
    parser.add_argument("--source", type=Path, help="Archivo fuente exacto para --slug.")
    parser.add_argument("--audio-bitrate", default="128k")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ffmpeg", type=Path, default=Path("ffmpeg"))
    parser.add_argument("--ffprobe", type=Path, default=Path("ffprobe"))
    args = parser.parse_args()

    rows = load_rows(args.report, args.slug)
    if not rows:
        raise RepairError("No hay filas para reparar.")

    sources = [] if args.source else find_sources(args.source_root)
    failures: list[str] = []
    for row in rows:
        source = args.source
        score = 999 if source else 0
        if not source:
            source, score = best_source(row, sources)
        print(f"\n{row.get('episode_slug')} -> {source or 'SIN MATCH'} (score {score})")
        if not source:
            failures.append(f"{row.get('episode_slug')}: no encontre fuente local")
            continue
        if args.dry_run:
            continue
        try:
            target = repair_audio(args.ffmpeg, args.ffprobe, row, source, args.output_root, args.audio_bitrate)
            print(f"Parche listo: {target}")
        except Exception as exc:
            failures.append(f"{row.get('episode_slug')}: {exc}")
            print(f"ERROR: {failures[-1]}", file=sys.stderr)

    if failures:
        print("\nPendientes/errores:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

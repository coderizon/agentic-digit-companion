"""Schneidet die Originalaufnahmen aus Open WebUI als stumme MP4 zusammen."""
import argparse
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

import imageio_ffmpeg


# Anfang und Ende in Sekunden der aufgenommenen WebM-Datei, bei 25 Bildern/s.
# „auftrag“ und „nachweis“ zeigen denselben gespeicherten Chat und dieselbe Notiz.
FPS = 25
SCHNITTE = (
    ("quellen", 16.00, 28.00),
    ("plan", 16.80, 22.80),
    ("plan", 127.84, 143.84),
    ("bibliothek_finanzen", 3.72, 7.72),
    ("kaemmerei", 15.60, 27.12),
    ("kaemmerei", 95.72, 102.20),
    ("bibliothek_planung", 4.84, 8.84),
    ("sperre", 12.80, 27.80),
    ("auftrag", 15.76, 23.76),
    ("auftrag", 64.76, 68.76),
    ("nachweis", 6.12, 19.12),
)


def ffmpeg(*args):
    proc = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error",
         *map(str, args)],
        capture_output=True,
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))


def schneiden(aufnahmen, ausgabe):
    ausgabe.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="digit-schnitt-", dir=ausgabe.parent) as tmp:
        work = Path(tmp)
        teile = []
        gesamt = 0
        for index, (name, anfang, ende) in enumerate(SCHNITTE):
            original = aufnahmen / name / "openwebui.webm"
            frames = round(ende * FPS) - round(anfang * FPS)
            teil = work / f"clip-{index:02d}.mp4"
            ffmpeg("-ss", f"{anfang:.2f}", "-i", original, "-map", "0:v:0",
                   "-an", "-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1",
                   "-frames:v", frames, "-c:v", "libx264", "-preset", "fast", "-crf", "14",
                   "-pix_fmt", "yuv420p", "-profile:v", "high", "-threads", "4", teil)
            teile.append(teil)
            print(f"{gesamt / FPS:6.2f} s  {name}", flush=True)
            gesamt += frames

        liste = work / "clips.ffconcat"
        liste.write_text("ffconcat version 1.0\n" + "".join(f"file '{p.name}'\n" for p in teile),
                         encoding="ascii")
        ergebnis = work / "film.mp4"
        ffmpeg("-f", "concat", "-safe", "1", "-i", liste, "-map", "0:v:0",
               "-an", "-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1",
               "-c:v", "copy", "-movflags", "+faststart", ergebnis)
        frames, _ = imageio_ffmpeg.count_frames_and_secs(str(ergebnis))
        if frames != gesamt:
            raise ValueError(f"Der Schnitt enthält {frames} von {gesamt} erwarteten Bildern.")
        os.replace(ergebnis, ausgabe)
        print(f"{gesamt} Bilder · {gesamt / FPS:.2f} Sekunden", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("aufnahmen", type=Path)
    parser.add_argument("ausgabe", type=Path)
    args = parser.parse_args()
    schneiden(args.aufnahmen.resolve(), args.ausgabe.resolve())

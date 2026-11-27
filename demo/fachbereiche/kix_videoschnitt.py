"""Schneidet reine Wartezeiten aus der stummen Originalaufnahme der beiden Oberflächen."""
import argparse
import json
import subprocess
from pathlib import Path

import imageio_ffmpeg


def export(directory, outfile, portal_directory=None):
    data = json.loads((directory / "schnitt.json").read_bytes())
    t = data["time"]
    number = data["result"].get("number")
    if not number:
        raise ValueError("Die Ticketnummer im Chat fehlt.")
    inputs = [directory / "ablauf.webm"]
    if portal_directory is not None:
        portal = json.loads((portal_directory / "zeit.json").read_bytes())
        if portal.get("number") != number:
            raise ValueError("Die Ticketnummern im Chat und im KIX-Portal unterscheiden sich.")
        inputs.append(portal_directory / "kix.webm")
        portal_times = portal["time"]
        article_visible = portal.get("article")
    else:
        portal_times = t
        article_visible = data["result"].get("article")
    if not article_visible or not 0 <= portal_times["kixDetail"] <= portal_times["kixArtikel"] < portal_times["end"]:
        raise ValueError("Die Portalaufnahme zeigt den Artikel nicht vollständig.")
    parts = []
    for scene, end in (("handbuch", "handbuchEnd"), ("entwurf", "entwurfEnd"), ("freigabe", "linkEnd")):
        remove_after = min(t[scene + "Send"] + .75, t[scene + "Answer"])
        resume_at = max(remove_after, t[scene + "Answer"] - .55)
        parts.append((0, max(0, t[scene + "Start"] - .25), remove_after))
        parts.append((0, resume_at, t[end]))
    parts.append((len(inputs) - 1, max(0, portal_times["kixDetail"] - .4), portal_times["end"]))
    parts = [(index, a, b) for index, a, b in parts if b > a + .05]
    filters = [f"[{index}:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[p{i}]"
               for i, (index, a, b) in enumerate(parts)]
    filters.append("".join(f"[p{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0[v]")
    input_args = []
    for index, recording in enumerate(inputs):
        last_frame = max(b for src, _, b in parts if src == index)
        input_args.extend(["-t", f"{last_frame + .5:.3f}", "-i", str(recording)])
    outfile.parent.mkdir(parents=True, exist_ok=True)
    tmp = outfile.with_name(outfile.stem + ".neu.mp4")
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
                    *input_args, "-filter_complex", ";".join(filters),
                    "-map", "[v]", "-an", "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt",
                    "yuv420p", "-movflags", "+faststart", str(tmp)], check=True)
    tmp.replace(outfile)
    print(number, round(sum(b - a for _, a, b in parts), 1), outfile)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("aufnahme", type=Path)
    p.add_argument("film", type=Path)
    p.add_argument("--portal", type=Path, help="Ordner mit separater KIX-Aufnahme und zeit.json")
    args = p.parse_args()
    export(args.aufnahme, args.film, args.portal)

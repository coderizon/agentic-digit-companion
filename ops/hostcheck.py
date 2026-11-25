"""Read-only checks before provisioning the four-T4 VM."""
import argparse
import csv
import ctypes
import os
import platform
import shutil
import socket
import subprocess
from pathlib import Path


def existing_parent(path):
    path = path.resolve()
    while not path.exists():
        path = path.parent
    return path


def hostcheck(root, source, managed_ports):
    errors = []
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        errors.append("Die getestete Strata-Engine benötigt Linux x86_64.")
    missing = [name for name in ("bash", "curl", "tar", "sha256sum", "flock", "nvidia-smi") if not shutil.which(name)]
    if missing:
        errors.append("Fehlende Programme: " + ", ".join(missing))
    if shutil.which("nvidia-smi"):
        probe = subprocess.run(["nvidia-smi", "--query-gpu=index,name,memory.total,driver_version",
                                "--format=csv,noheader,nounits"], capture_output=True, text=True)
        gpus = {row[0].strip(): row for row in csv.reader(probe.stdout.splitlines()) if len(row) == 4}
        selected = os.environ.get("FREIBURG_READER_GPUS", "0,1,2,3").split(",")
        if len(set(selected)) != 4 or len(selected) != 4 or any(i not in gpus or "T4" not in gpus[i][1] for i in selected):
            errors.append("Vier sichtbare Tesla T4 erwartet. Zuordnung über FREIBURG_READER_GPUS prüfen.")
        for index in selected:
            if index in gpus:
                _, name, mib, driver = gpus[index]
                print(f"GPU {index}: {name.strip()}, {mib.strip()} MiB, Treiber {driver.strip()}")
        for env, default in (("FREIBURG_SEARCH_GPU", "3"), ("FREIBURG_RERANK_GPU", "2")):
            if os.environ.get(env, default) not in gpus:
                errors.append(f"{env} verweist auf keine sichtbare GPU.")
        try:
            cuda = ctypes.CDLL("libcuda.so.1")
            version = ctypes.c_int()
            if cuda.cuInit(0) or cuda.cuDriverGetVersion(ctypes.byref(version)) or version.value < 13000:
                errors.append("Der aktive NVIDIA-Treiber muss die CUDA-13.0-Engine laden können.")
        except OSError:
            errors.append("Die CUDA-Treiberbibliothek libcuda.so.1 ist für den VM-Benutzer nicht erreichbar.")
    disk = existing_parent(root)
    if not disk.is_dir() or not os.access(disk, os.W_OK | os.X_OK):
        errors.append(f"Kein beschreibbarer Installationspfad unter {disk}.")
    print(f"Installationspfad: {root}. Auf {disk} sind {shutil.disk_usage(disk).free / 2**30:.0f} GiB frei.")
    if Path("/proc/meminfo").is_file():
        kib = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemTotal:")))
        ram = kib * 1024
        for limit in (Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")):
            if limit.is_file() and (cap := limit.read_text().strip()).isdecimal():
                ram = min(ram, int(cap))
        print(f"Hauptspeicher für diesen Host bzw. Container: {ram / 2**30:.1f} GiB.")
    print("Für eine vollständige neue Installation rund 200 GB persistenten Speicher einplanen.")
    model_dir = Path(os.environ.get("STRATA_ASSETS", root / "assets")) / "models/unsloth-UD-Q4_K_XL"
    missing_bytes = 0
    for index, size in enumerate((10946624, 49859583136, 49376141504, 12087983520), 1):
        shard = model_dir / f"Qwen3.8-Flash-Next-UD-Q4_K_XL-{index:05}-of-00004.gguf"
        if shard.is_file() and shard.stat().st_size == size:
            continue
        partial = shard.with_suffix(".gguf.part")
        progress = partial.stat().st_size if partial.is_file() else 0
        missing_bytes += size - progress if 0 <= progress <= size else size
    available = shutil.disk_usage(existing_parent(model_dir)).free
    if missing_bytes > available:
        errors.append(f"Für die ausstehenden Qwen-Dateien werden {missing_bytes / 2**30:.1f} GiB benötigt, auf dem Modellvolume sind {available / 2**30:.1f} GiB frei.")
    if len(os.fsencode(root / "run/supervisor.sock")) >= 108:
        errors.append("Der Pfad ist für einen Linux-Unix-Socket zu lang. Kürzeres FREIBURG_ROOT wählen.")
    ports = []
    for name, default in (("WEBUI", 8077), ("GATEWAY", 8090), ("SEARCH", 8083), ("READER", 18189)):
        try:
            port = int(os.environ.get(f"FREIBURG_{name}_PORT", default))
            if not 1 <= port <= 65535 or port in ports:
                raise ValueError
            ports.append(port)
            if str(port) not in managed_ports:
                with socket.socket() as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind(("127.0.0.1", port))
        except ValueError:
            errors.append(f"Ungültiger oder doppelt belegter Port für {name}.")
        except OSError as exc:
            errors.append(f"Port {port} für {name} ist nicht verfügbar ({exc.strerror}).")
    if source == "--fresh":
        if not shutil.which("tesseract") or "deu" not in subprocess.run(
                ["tesseract", "--list-langs"], capture_output=True, text=True).stdout.splitlines():
            errors.append("Der Neuaufbau braucht Tesseract mit Sprache deu. Unter Ubuntu: sudo apt install tesseract-ocr-deu. Alternativ --snapshot DATEI verwenden.")
    elif source == "--existing":
        for name in ("sources.json", "fritz.sqlite", "textindex/plan.json", "bildindex/katalog.json"):
            if not (root / "data/import" / name).is_file():
                errors.append(f"Im aktiven Datenstand fehlt {name}.")
    elif not Path(source).is_file():
        errors.append("Snapshot-Datei fehlt: " + source)
    if errors:
        raise SystemExit("\n".join("• " + message for message in errors))
    print("Hardware, Pfade und lokale Ports geprüft.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--source", required=True)
    parser.add_argument("--managed-ports", default="", help="Previously configured ports of this instance's verified running supervisor")
    args = parser.parse_args()
    hostcheck(args.root.resolve(), args.source, set(args.managed_ports.split(",")))

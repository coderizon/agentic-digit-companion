"""Open a local, single-user rehearsal of the structured DIGITOM workflow in a browser."""
import argparse
import json
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "app"))
from digitom_workflow import respond


def serve(folder, port=0, open_browser=True):
    directory = folder.resolve(strict=True)
    demo = directory == (HERE / "digitom").resolve()
    files = {}
    for path in sorted(directory.iterdir()):
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in {".txt", ".md"}:
            if path.stat().st_size > 400_000:
                raise ValueError(f"{path.name} ist für die lokale Textprobe zu groß.")
            ident = str(uuid5(NAMESPACE_URL, path.as_uri()))
            files[ident] = {"file": ident, "name": path.name, "page": None, "text": path.read_text(encoding="utf-8-sig")}
    if not files:
        raise ValueError("Der gewählte Ordner enthält keine Textunterlagen (.txt oder .md).")
    kb = {"id": str(uuid5(NAMESPACE_URL, directory.as_uri())), "name": directory.name, "hits": list(files.values())}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def reply(self, status, data, mime):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; form-action 'self'")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.headers.get("Host") != address:
                return self.send_error(403)
            assets = {"/": ("lokal.html", "text/html"), "/demo.js": ("lokal.js", "text/javascript"),
                      "/demo.css": ("lokal.css", "text/css")}
            if self.path in assets:
                name, mime = assets[self.path]
                return self.reply(200, (HERE / name).read_bytes(), mime + "; charset=utf-8")
            if self.path == "/info":
                data = {"quelle": "Fiktiver Übungskatalog" if demo else directory.name, "anzahl": len(files)}
                return self.reply(200, json.dumps(data).encode(), "application/json")
            match = re.fullmatch(r"/api/v1/files/([0-9a-f-]{36})/content", self.path)
            if match and match[1] in files:
                return self.reply(200, files[match[1]]["text"].encode(), "text/plain; charset=utf-8")
            self.send_error(404)

        def do_POST(self):
            if self.path != "/bedarf" or self.headers.get("Host") != address or self.headers.get("Origin") != "http://" + address:
                return self.send_error(403)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 120_000:
                    return self.send_error(413)
                turns = json.loads(self.rfile.read(size))["turns"]
                if not isinstance(turns, list) or not 1 <= len(turns) <= 10 or not all(
                        isinstance(q, str) and 0 < len(q.strip()) <= 3600 for q in turns):
                    return self.send_error(400)
                for i in range(len(turns) - 1, 0, -1):
                    if re.match(r"^(?:neuer|anderer|neues)\s+(?:bedarf|anliegen|thema)", turns[i], re.I):
                        turns = turns[i:]
                        break
                text = respond({"demo": demo, "collections": [kb], "turns": turns, "question": turns[-1]})
                self.reply(200, json.dumps({"antwort": text}).encode(), "application/json")
            except (KeyError, ValueError, TypeError):
                self.send_error(400)

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as http:
        address = f"127.0.0.1:{http.server_port}"
        print(f"DIGITOM-Demo: http://{address}/  ·  Strg+C zum Beenden", flush=True)
        if open_browser:
            threading.Timer(.3, webbrowser.open, args=("http://" + address + "/",)).start()
        try:
            http.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--quellen", type=Path, default=HERE / "digitom", help="Ordner mit lokalen Katalogeinträgen im mitgelieferten Textformat")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    serve(args.quellen, args.port, not args.no_browser)

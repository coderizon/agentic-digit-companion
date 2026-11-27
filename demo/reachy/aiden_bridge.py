"""Freibots natives Sprachwerkzeug über einen lokalen SSH-Tunnel mit der KIX-Demo verbinden."""
import argparse
import hmac
import json
import os
import re
import secrets
import threading
import time
import unicodedata
import uuid
from pathlib import Path

import paramiko
from websockets.sync.client import connect

from companion import ssh


def asks_to_send(text, expected=""):
    # Only the last question can establish consent. A spoken offer can end in a period.
    text = unicodedata.normalize("NFKC", text)
    sentences = [part.strip() for part in re.split(r"[.!?。]+", text) if part.strip()]
    question = sentences[-1] if sentences else ""
    def words(value):
        return re.sub(r"[^\w]+", "", unicodedata.normalize("NFKC", value).casefold())
    if expected and words(question) == words(expected):
        return True
    if re.search(r"\b(nicht|kein\w*|oder|später)\b", question, re.I):
        return False
    return all(re.search(pattern, question, re.I) for pattern in (
        r"\b(ticket|testticket|ticketentwurf|entwurf)\b",
        r"\b(soll|möchtest|willst|darf|bestätig\w*|einverstanden)\b",
        r"\b(erstellen|anlegen|aufnehmen|schicken|abschicken|senden|absenden|übermitteln|weiterleiten|freigeben|eröffnen|anlege|erstelle)\b",
    ))


class Gespraech:
    def __init__(self, args):
        self.args = args
        self.events = threading.Condition()
        self.calls = threading.Lock()
        self.connected = False
        self.generation = 0
        self.pending = None
        self.new_session(False)

    def new_session(self, connected):
        with self.events:
            self.connected = connected
            self.generation += 1
            self.pending = None
            self.advice = None
            self.asked = False
            self.last_user = ""
            self.recent_user = ("", 0.0)
            self.events.notify_all()

    def observe(self):
        while True:
            try:
                with connect(f"ws://{self.args.host}:7860/rpc", proxy=None, open_timeout=3, close_timeout=2) as ws:
                    self.new_session(True)
                    for raw in ws:
                        msg = json.loads(raw)
                        p = msg.get("params", {})
                        if msg.get("method") == "conversation.transcript" and p.get("final"):
                            self.transcript(p["role"], p["text"])
            except (OSError, ValueError, TimeoutError, RuntimeError):
                pass
            except Exception as exc:
                print("Gesprächsverbindung:", type(exc).__name__, flush=True)
            finally:
                self.new_session(False)
            time.sleep(1.5)

    def transcript(self, role, text):
        with self.events:
            if role == "user" and text.strip():
                self.recent_user = (text.strip()[:1200], time.monotonic())
                self.events.notify_all()
            if not self.pending:
                return
            if role == "assistant" and text.strip():
                offer = asks_to_send(text, self.pending[3])
                if offer or re.search(r"[?？¿]|\b(?:geholfen|gelöst)\b", text, re.I):
                    self.asked = offer
                    self.last_user = ""
            elif role == "user" and self.asked:
                self.last_user = text.strip()[:1200]
            self.events.notify_all()

    def kix(self, session, text, previous="", context=None, language="de", query_de=""):
        cmd = "cd /workspace/freiburg-rag && run/fachbereiche-mcp/bin/python demo/fachbereiche/freibot_ticket.py"
        body = json.dumps({"text": text, "previous": previous, "session": session,
                           "handbuch": not previous, "context": context, "language": language,
                           "query_de": query_de}).encode()
        return json.loads(ssh(self.args, self.args.t4, cmd, body, timeout=50))

    def handle(self, request):
        with self.calls:
            action = request.get("aktion")
            language = request.get("language")
            if language not in ("de", "en", "fr", "es", "ja"):
                language = None
            if action == "vorbereiten" or (action == "entscheidung" and not self.pending):
                with self.events:
                    generation = self.generation
                    self.pending = None
                    self.asked = False
                    self.last_user = ""
                    if not self.connected:
                        return {"status": "offline", "speech": "Die Sprachbestätigung ist noch nicht verbunden."}
                    self.events.wait_for(lambda: self.recent_user[0] and time.monotonic() - self.recent_user[1] < 25, timeout=4)
                    text, heard = self.recent_user
                    self.recent_user = ("", 0.0)
                    if not text or time.monotonic() - heard >= 25:
                        return {"status": "rueckfrage", "speech": "Bitte beschreibe die Störung noch einmal."}
                    advice = self.advice
                    if advice and time.monotonic() >= advice[2]:
                        advice = None
                session = advice[0] if advice else str(uuid.uuid4())
                language = language or (advice[3] if advice else "de")
                result = self.kix(session, text, context=advice[1] if advice else None,
                                  language=language, query_de=request.get("query_de", ""))
                with self.events:
                    if self.generation != generation:
                        return {"status": "offline", "speech": "Bitte melde die Störung in der neuen Sitzung erneut."}
                    self.advice = None
                    if result["status"] in ("hilfe", "rueckfrage") and result.get("issue"):
                        context = {"status": result["status"], "issue": result["issue"]}
                        self.advice = (session, context, time.monotonic() + 300, language)
                    elif result["status"] == "entwurf":
                        self.pending = (session, result["reply"], time.monotonic() + 180,
                                        result["confirmation_question"], language)
            elif request.get("aktion") == "entscheidung":
                with self.events:
                    pending = self.pending
                    if not pending or time.monotonic() >= pending[2]:
                        self.pending = None
                        return {"status": "kein_entwurf", "speech": "Bitte schildere zuerst die Störung."}
                    self.events.wait_for(lambda: self.last_user or self.pending is not pending, timeout=5)
                    if self.pending is not pending or not self.asked or not self.last_user:
                        return {"status": "rueckfrage", "speech": pending[3], "confirmation_question": pending[3]}
                    user_turn = self.last_user
                    self.last_user = ""
                    self.recent_user = ("", 0.0)
                    self.asked = False
                language = language or pending[4]
                result = self.kix(pending[0], user_turn, pending[1], language=language)
                with self.events:
                    if self.pending is pending and result.get("confirmation_question"):
                        self.pending = (*pending[:3], result["confirmation_question"], language)
                    elif self.pending is pending and result["status"] in ("gesendet", "verworfen"):
                        self.pending = None
            else:
                return {"status": "fehler", "speech": "Unbekannte Ticketaktion."}
            print("Ticketwerkzeug:", result["status"], result.get("ref", ""), result.get("number", ""), flush=True)
            return {k: result[k] for k in ("status", "speech", "quelle", "number", "confirmation_question") if k in result}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="reachy-mini.local")
    p.add_argument("--t4", default="digit-t4-demo")
    p.add_argument("--ssh-config", type=Path, required=True)
    args = p.parse_args()
    key = secrets.token_urlsafe(32)
    dialog = Gespraech(args)

    def channel_handler(ch, origin, server):
        try:
            ch.settimeout(65)
            with ch.makefile("rb") as stream:
                request = json.loads(stream.readline(8192))
            if not hmac.compare_digest(request.pop("key", ""), key):
                return
            ch.sendall(json.dumps(dialog.handle(request)).encode() + b"\n")
        except Exception as exc:
            print("Werkzeug nicht bestätigt:", type(exc).__name__, flush=True)
        finally:
            ch.close()

    with paramiko.SSHClient() as robot:
        robot.load_host_keys(str(Path.home() / ".ssh" / "known_hosts_freibot"))
        robot.connect(args.host, username="pollen", password=os.environ.pop("FREIBOT_SSH_PASSWORD", None), timeout=6)
        transport = robot.get_transport()
        transport.set_keepalive(15)
        transport.request_port_forward("127.0.0.1", 18763, handler=lambda *a: threading.Thread(
            target=channel_handler, args=a, daemon=True).start())
        with robot.open_sftp() as sftp:
            folder = ".config/freibot-digit"
            try:
                sftp.stat(folder)
            except FileNotFoundError:
                sftp.mkdir(folder, 0o700)
            path = folder + "/bridge.key"
            tmp = path + "." + uuid.uuid4().hex
            with sftp.open(tmp, "wx") as f:
                sftp.chmod(tmp, 0o600)
                f.write(key)
            sftp.posix_rename(tmp, path)
        threading.Thread(target=dialog.observe, daemon=True).start()
        print("Sprachbrücke bereit.", flush=True)
        while transport.is_active():
            time.sleep(1)


if __name__ == "__main__":
    main()

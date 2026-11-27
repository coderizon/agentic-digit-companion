import asyncio
import base64
import json
from pathlib import Path

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


class DigitSehen(Tool):
    name = "digit_sehen"
    description = ("Look through Freibot's camera on an explicit request. Send one current frame "
                   "to the DIGIT vision server; read a displayed error code or describe the object.")
    parameters_schema = {"type": "object", "properties": {"question": {"type": "string"}},
                         "required": ["question"]}

    async def __call__(self, deps: ToolDependencies, question: str, **kwargs):
        if not deps.camera_enabled or (Path.home() / ".local/state/freibot/ruhe").exists():
            return {"error": "Kamerazugriff ist ausgeschaltet."}
        if not question.strip():
            return {"error": "Was soll ich anschauen?"}
        frame = deps.reachy_mini.media.get_frame_jpeg()
        if not frame or len(frame) > 3_000_000:
            return {"error": "Kein geeignetes Kamerabild verfügbar."}
        packet = json.dumps({"question": question[:400], "jpeg": base64.b64encode(frame).decode("ascii")})
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-T", "-F", str(Path.home() / ".config/freibot-digit/vision_ssh"), "digit-vision",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            raw, _ = await asyncio.wait_for(proc.communicate(packet.encode()), timeout=85)
            if proc.returncode == 0:
                return json.loads(raw)
            return {"error": "Der DIGIT-Bilddienst ist gerade nicht erreichbar."}
        except (ValueError, TimeoutError):
            return {"error": "Die Bildauswertung hat keine rechtzeitige Antwort geliefert."}
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

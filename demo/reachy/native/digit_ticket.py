"""Tool for the native Reachy Mini Conversation App. The demo bridge is on robot loopback."""
import asyncio
import json
from pathlib import Path

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


class DigitTicket(Tool):
    name = "digit_ticket"
    description = ("Use for every IT question and troubleshooting follow-up. 'vorbereiten' searches the handbook. "
                   "Give the sourced steps or ask the returned clarifying question and wait. Only offer a ticket "
                   "when the result is 'entwurf'. Use 'entscheidung' for the spoken decision on that draft. "
                   "The bridge checks the actual user transcript. Never invent a ticket number.")
    parameters_schema = {
        "type": "object",
        "properties": {
            "aktion": {"type": "string", "enum": ["vorbereiten", "entscheidung"]},
            "language": {"type": "string", "enum": ["de", "en", "fr", "es", "ja"],
                         "description": "The language the user is speaking or has requested for this turn."},
            "query_de": {"type": "string", "description": "For non-German vorbereiten only: translate the actual "
                         "current utterance into German for handbook lookup, preserving error codes, uncertainty "
                         "and negation. Never translate or supply a decision on a draft."},
        },
        "required": ["aktion", "language"],
    }

    async def __call__(self, deps: ToolDependencies, aktion: str, language: str = "de", query_de: str = "", **kwargs):
        writer = None
        try:
            key = (Path.home() / ".config/freibot-digit/bridge.key").read_text().strip()
            reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", 18763), 3)
            request = {"key": key, "aktion": aktion, "language": language,
                       "query_de": query_de[:1200] if aktion == "vorbereiten" else ""}
            writer.write(json.dumps(request).encode() + b"\n")
            await writer.drain()
            result = await asyncio.wait_for(reader.readline(), 60)
            return json.loads(result)
        except (OSError, ValueError, TimeoutError):
            return {"status": "unbekannt", "speech": "Ich erreiche den Ticketdienst gerade nicht. Mir liegt keine bestätigte Ticketnummer vor."}
        finally:
            if writer:
                writer.close()

from pathlib import Path

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


class DigitRuhe(Tool):
    name = "digit_ruhe"
    description = ("Immediately pause this conversation silently on a direct request for quiet. "
                   "Examples: Sei mal ruhig, Sag mal ruhig, Freibot Ruhe, Freiburg Ruhe. "
                   "Resume only after the local wake phrase. Ignore quoted or negated commands.")
    parameters_schema = {"type": "object", "properties": {}, "required": []}
    needs_response = False

    async def __call__(self, deps: ToolDependencies, **kwargs):
        marker = Path.home() / ".local/state/freibot/ruhe"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(mode=0o600)
        return {"status": "ruhe"}

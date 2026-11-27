"""Eine Sprachdemo-Sitzung im synthetischen KIX-Schulungssystem bearbeiten."""
import json
import re
import sys
import unicodedata
import uuid

from it_service import OFFER, REF, decision, process
from sprachfrage import answer, demo_token


SPOKEN_APPROVAL = re.compile(
    r"(?:(?:ja|jo|klar|okay|ok|gerne?) )?"
    r"(?:(?:das )?kannst du (?:das )?(?:(?:bitte|gerne?|ruhig|so) )?(?:für mich )?(?:machen|anlegen|erstellen)"
    r"|mach (?:das|es|mal|ruhig)(?: (?:mal|ruhig|gerne?))?"
    r"|(?:bitte )?(?:erstell(?:e)?|schick(?:e)?|sende) (?:das |ein )?ticket(?: ab)?"
    r"|(?:ja )?(?:klar|gerne?|okay|ok|einverstanden|natürlich))"
    r"(?: bitte| danke)?"
)

TICKET_QUESTION = "Soll i dir e Ticket aalege?"

# Match the whole utterance so that a qualification such as „aber erst morgen“
# cannot be lost. ASR may transcribe the same permission in dialect or standard German.
PERMISSION = (
    r"(?:(?:das |des )?(?:darfst|kannst) du|du (?:darfst|kannst)|"
    r"(?:des |das |du )?(?:darfsch|derfsch|kannsch)(?: du)?)"
    r"(?: (?:das|des|es|(?:das|des|ein|a|e) ticket))?"
)
ACTION = r"(?:machen|mache|macha|anlegen|anlege|anlega|aalege|anleje|erstellen|erstelle|eröffnen|abschicken)"
SPOKEN_PERMISSION = re.compile(
    rf"(?:(?:ja|jo|klar|okay|ok) )?{PERMISSION}(?: (?:gern|gerne|ruhig|bitte|so))?"
    rf"(?: {ACTION})?(?: bitte| danke)?"
)
SPOKEN_REFUSAL = re.compile(
    rf"(?:(?:ja|jo|nein|nei|noi) )?{PERMISSION}(?: bitte)? (?:noch |lieber )?(?:nicht|nit|net|ned)"
    rf"(?: {ACTION})?(?: bitte| danke)?"
)


def phrase(text):
    text = unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    return " ".join(re.sub(r"[,!.?…¿¡、。]", " ", text).split())


def expressions(*texts):
    return {phrase(text) for text in texts}


APPROVE = expressions(
    "jo", "jo bitte", "jo mach des", "ja mach des", "mach des", "mach des bitte",
    "jo mach des bitte", "des kannsch mache", "jo des kannsch mache", "kannsch mache",
    "mach das bitte", "leg es an", "leg das ticket an", "leg's an", "mach ruhig", "ja genau",
)
DECLINE = expressions(
    "nei", "noi", "lieber nit", "mach des nit", "no nit", "no net", "noch net", "lass des", "lieber net", "mach des net",
    "lieber nicht", "noch nicht", "lass es", "mach das nicht", "nein bitte nicht",
    "jetzt nicht", "jetzt nit", "jetzt net", "bitte net", "bitte nit", "lass es bleiben", "lass des bleiba",
    "kein ticket", "bitte kein ticket", "bitte kein ticket anlegen", "mach das bitte nicht",
    "mach des bitte net", "nein lass es", "nein lass des", "auf keinen fall", "lass es sii", "lass des si", "kei ticket", "kei ticket bitte",
)


def spoken_decision(text):
    words = phrase(text)
    if words in DECLINE or SPOKEN_REFUSAL.fullmatch(words):
        return "cancel"
    if "?" in text:
        return None
    if words in APPROVE or SPOKEN_PERMISSION.fullmatch(words):
        return "send"
    return decision(text) or ("send" if SPOKEN_APPROVAL.fullmatch(words) else None)


def handbook_turn(text, context):
    """Look up the spoken question before a draft is eligible for confirmation."""
    state = context.get("status")
    original = context.get("issue", "")[:1200]
    words = " ".join(re.findall(r"[\w-]+", text.lower()))
    reply = spoken_decision(text)
    if original and ((reply == "cancel" and words not in {"nein", "nei", "noi"}) or
                     re.search(r"\b(?:kein|kei|ohne) ticket\b", words)):
        return {"status": "beendet", "speech": "Alles klar, i lass des."}, None
    failed = bool(re.search(r"\b(?:nicht geholfen|nicht (?:geklappt|funktioniert)|immer noch|weiterhin|"
                            r"(?:hilft|geht|klappt|funktioniert) (?:leider )?(?:nicht|nit|net|ned)|"
                            r"(?:nix|nit|net) (?:gholfe|gholfa|geholfen|brocht|bracht)|immer no|ohne erfolg)\b", words))
    negative = reply == "cancel" or words.startswith("nein ")
    if original and state == "hilfe":
        if not failed and not negative and (words in {"ja", "jo", "ja danke", "danke", "super danke", "des hot gholfa", "des het gholfe"} or
                re.fullmatch(r"(?:ja )?(?:das |es )?(?:hat (?:mir )?geholfen|hat (?:geklappt|funktioniert)|"
                             r"(?:geht|klappt|funktioniert) (?:jetzt|wieder|jetzt wieder))"
                             r"(?: danke)?", words)):
            return {"status": "geloest", "speech": "Wunderbar, denn het sich des erledigt."}, None
        if not failed and reply == "send" and "ticket" not in words:
            return {"status": "hilfe", "issue": original,
                    "speech": "Het dir de Tipp gholfe, oder goht’s immer no nit?"}, None
        wants_ticket = bool(re.search(r"\bticket\b", words) and
                            re.search(r"\b(?:bitte|möchte|erstell\w*|anlegen)\b", words) and
                            not re.search(r"\b(?:nicht|kein\w*)\b", words))
        if failed or negative or wants_ticket:
            return None, (original, text)
    if original and state == "rueckfrage" and words in {"ja", "ja bitte"}:
        return {"status": state, "issue": original, "speech": "Was für e Meldig oder was für e Fehlercode siehsch?"}, None
    if original and state == "rueckfrage" and (negative or failed or
            re.search(r"\b(?:kein\w* (?:code|fehlermeldung)|weiß ich nicht)\b", words)):
        return None, (original, text)
    code = re.compile(r"(?:r\s*[- ]?\s*p|er\s+pe)\s*[- ]?\s*(zwölf|zwoelf|twelve|douze|doce|十二|\d{2,5})(?!\d)", re.I)
    def error_code(match):
        return "RP-" + (match[1] if match[1].isdigit() else "12")
    query = code.sub(lambda m: " " + error_code(m) + " ", unicodedata.normalize("NFKC", text))
    result = answer(query)
    if result.get("datei"):
        return {"status": "hilfe", "issue": text, "quelle": result["quelle"],
                "speech": result["quelle"] + ". " + result["antwort"] + " Het dir des gholfe?"}, None
    if original and state == "rueckfrage":
        return None, (original, text)
    return {"status": "rueckfrage", "issue": text, "speech": "Do find i im IT-Handbuch RaumPlan "
            "kei passende Anleitung. Was für e Fehlermeldig siehsch genau?"}, None


def turn(request):
    session = "freibot-" + str(uuid.UUID(request["session"]))
    text = " ".join(request["text"].split())
    if not text or len(text) > 1200:
        raise ValueError("Leere oder zu lange Spracheingabe.")
    previous = request.get("previous", "")
    offer = TICKET_QUESTION
    support = request.get("handbuch", False) and not previous
    if support:
        result, escalation = handbook_turn(text, request.get("context") or {})
        if result is not None:
            return result
        original, feedback = escalation
        text = (original + " – Rückmeldung: " + feedback)[:1800]
    if previous:
        action = spoken_decision(text)
        if action is None:
            return {"status": "unverstanden", "speech": "Des war mir no nit ganz klar. " + offer, "confirmation_question": offer}
        text = "Ja" if action == "send" else "Nein"
    question = text if previous else "Fiktive Freibot-Testmeldung. " + text
    reply = process({"token": demo_token(), "chat": session, "question": question, "previous": previous,
                     "handbook_followup_draft": support})
    ref = REF.search(reply)
    number = re.search(r"Ticketnummer (\d+)", reply)
    if number:
        return {"status": "gesendet", "number": number[1], "ref": ref[0],
                "speech": "Di Ticket isch jetzt in KIX. D Nummer isch "
                          + ", ".join(number[1]) + "."}
    if OFFER in reply and ref:
        return {"status": "entwurf", "reply": reply, "ref": ref[0],
                "speech": "Des hab i ufgschriebe: " + text + " " + offer,
                "confirmation_question": offer}
    if "Entwurf verworfen" in reply:
        return {"status": "verworfen", "speech": "Alles klar, i lass des."}
    return {"status": "hinweis", "speech": reply.split("\n\n")[0], "reply": reply}


if __name__ == "__main__":
    print(json.dumps(turn(json.load(sys.stdin)), ensure_ascii=False))

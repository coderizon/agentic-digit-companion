"""Quellen und klar gekennzeichnete Übungsunterlagen für den Fall Kleineschholz."""
from pathlib import Path
import hashlib
import json


PLAN_DOC = "eb9696074d64b9c90990c3"
RAT_DOC = "318040f2dce5294f269abe"
PLAN_HASH = "92a04ef3c04cfffb0391f06fbaef38d3ab41d8c63a7e8401e3ce340d199d7a17"
PLAN_IMAGE = "29e501757f75a870b07733e6c637c6273b5a5751d10015ec4137c7395a08768a"
RAT_HASH = "2703f556956b5b2f860672680c296cbe2562ea6d89e4fb4c36b0ace1be85868e"
COLLECTIONS = {"gemeinsam": "Kleineschholz · Öffentliche Unterlagen",
               "planung": "Stadtplanung · Interne Prüfunterlagen",
               "finanzen": "Kämmerei · Interne Finanzvermerke"}
PEOPLE = {"planung": ("Lea · Stadtplanung", "Stadtplanung", "lea.planung"),
          "finanzen": ("Milan · Kämmerei", "Kämmerei", "milan.kaemmerei")}
PIPE = "digit_fachbereiche"
MODEL = "digit-companion-fachbereiche"
PLAN_FILE = "Entwicklungssatzung Kleineschholz mit Lageplan.pdf"
LEGENDE = "Lesefassung der Planlegende.txt"
RAT_FILE = "Ratsvorlage G-26-167.txt"
PLAN_NOTE = "Uebungsfall - Arbeitsvermerk Stadtplanung.txt"
FIN_NOTE = "Uebungsfall - Finanzvermerk Vermessung.txt"
MARKER = "DIGIT Companion · Schulungsfall Kleineschholz v1"


def originals(root):
    catalog = json.loads((root / "data/import/sources.json").read_bytes())
    def read(key, digest):
        src = catalog[key]
        content = (root / "data/originale" / Path(src["original"]).name).read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("Die referenzierte Originalquelle hat einen anderen Datenstand.")
        return src, content
    plan, pdf = read(PLAN_DOC, PLAN_HASH)
    rat, raw = read(RAT_DOC, RAT_HASH)
    paper = json.loads(raw)
    legend = (
        "Lesefassung der amtlichen Planlegende, Stadt Freiburg im Breisgau.\n"
        "Die rote Linie kennzeichnet den Umgriff der städtebaulichen Entwicklungsmaßnahme (SEM) Kleineschholz. "
        "Die Legende gibt die Fläche des Umgriffs mit circa 7,03 Hektar an. Der Maßstab beträgt 1:2500. "
        "Der Lageplan trägt den Stand 17.09.2021. Er ist Anlage 1 auf PDF-Seite 3 der Entwicklungssatzung. "
        "Für die Abgrenzung ist nach § 2 der Satzung dieser Plan maßgeblich. "
        "Quelle: " + plan["title"] + ", Seiten 2 und 3. " + plan["url"])
    record = (f"Stadt Freiburg im Breisgau · Ratsinformationssystem\n{paper['reference']} · {paper['name']}\n"
              f"Datum der Vorlage: {paper['date']}. Dokumenttyp: {paper['paperType']}.\n"
              "Dieser Registereintrag enthält Betreff, Datum und Kennung. Er enthält keinen Kostenbetrag.\n"
              + rat["url"])
    planning = ("FIKTIVER ÜBUNGSFALL · Interner Arbeitsvermerk der Stadtplanung\n"
                "Fall Kleineschholz: Prüfung einer Flurstücksanfrage. Die genaue Lage der angefragten Fläche ist offen. "
                "Für die Lageprüfung wird die Flurstücksnummer benötigt. Fehlt sie, fordert die Stadtplanung "
                "die Flurstücksnummer bei der anfragenden Stelle an. Erst dann gleicht die Stadtplanung die Fläche "
                "mit Anlage 1 der Entwicklungssatzung ab. Ein Entwurf dokumentiert den Prüfbedarf; eine "
                "Stellungnahme oder Freigabe ist damit nicht erteilt.")
    finance = ("FIKTIVER ÜBUNGSFALL · Interner Finanzvermerk der Kämmerei\n"
               "Für die externe Vermessung im Übungsfall Kleineschholz gilt eine fiktive Prüfschwelle von "
               "12.500 Euro brutto. Ab dieser Summe prüft die Kämmerei vor einer Beauftragung den Kostenvoranschlag. "
               "Die Prüfschwelle ist keine Bewilligung und kein städtischer Haushaltsansatz. "
               "Die Baukosten der Quartiersgarage sind in diesem Vermerk nicht angegeben.")
    return {"gemeinsam": [(PLAN_FILE, pdf, "application/pdf"), (LEGENDE, legend.encode(), "text/plain"),
                           (RAT_FILE, record.encode(), "text/plain")],
            "planung": [(PLAN_NOTE, planning.encode(), "text/plain")],
            "finanzen": [(FIN_NOTE, finance.encode(), "text/plain")]}

# DIGITOM · Ein Bedarfsgespräch vorführen

Ein Fachamt möchte Schäden an Spielgeräten per Handy melden. Gibt es bereits eine geeignete Leistung, wer verantwortet sie und welche Information fehlt für einen Bedarfsteckbrief? Der Musterkatalog, seine Teams und der frühere Fall mit Parkbänken sind erfunden.

## In zwei Nachrichten ausprobieren

1. In Open WebUI **DIGITOM · Bedarfsassistent** auswählen. Schreiben: „Unser Außendienst möchte kaputte Spielgeräte per Handy mit Foto und Standort melden. Gibt es dafür schon einen Service?“ Der Assistent findet die mobile Mängelmeldung, die verantwortliche Stelle und einen früheren Bedarf. Ein Klick auf die Quelle öffnet die Übungsunterlage.
2. Antworten: „Die Meldungen sollen beim Bauhof landen. Ziel ist, Schäden innerhalb von zwei Arbeitstagen zu sichten. Bitte erstelle den Steckbrief.“ Der Entwurf enthält Anliegen, Zielgruppe, Empfänger und Erfolgskriterium. Der Bauhof kommt aus dem Gespräch. Die Serviceverantwortung kommt aus dem Katalog.

Zum Gegencheck einen neuen Chat starten und nach einer **Terminbuchung für eine Sprechstunde** oder nach der **Abrechnung von Dienstreisen** fragen. Für die Terminbuchung gibt es eine andere Leistung. Bei den Reisekosten bleibt die Zuständigkeit offen.

## Auf dem Laptop vorführen

Unter Windows mit Python 3 im entpackten Projekt **DIGITOM-Demo-starten.cmd** doppelklicken. Unter Linux oder macOS `python3 demo/lokal.py` starten. Der Browser öffnet die lokale Gesprächsprobe. Mit **Neuer Bedarf** beginnt ein frisches Gespräch. Mit Strg+C im Startfenster endet der Dienst.

Die Laptop-Version liest die Textdateien im Unterordner `demo/digitom` und nutzt denselben strukturierten Ablauf wie der Server. Ein eigener Ordner mit Katalogeinträgen im Musterformat lässt sich über `python demo/lokal.py --quellen "Pfad zum Ordner"` auswählen. Für frei formulierte Quelldokumente, Modellprüfung und getrennte Benutzerrechte steht die vollständige Open-WebUI-Version bereit.

## Eigene Unterlagen auf der freigegebenen Umgebung

1. Als Admin in **Arbeitsbereich → Wissen** eine Sammlung anlegen. Einen Auszug aus dem Servicekatalog mit Zuständigkeiten, einen ähnlichen früheren Bedarf und die gewünschte Steckbriefvorlage hochladen. Für das Gespräch die Leserechte für Sammlung und Modell vergeben.
2. Im Bedarfsassistenten einen neuen Chat öffnen, **#** eingeben und die Sammlung auswählen. Eine solche Auswahl ersetzt die Beispielsammlung für die Suche. Ein über die Modelleinstellungen dauerhaft zugewiesener eigener Bestand ist ebenfalls möglich.
3. Anhand eines bekannten Falls die verlinkten Originale, die zuständige Stelle und die Fragen im Entwurf fachlich prüfen. Die Freigabe und Übernahme nach KIX oder Confluence liegen beim Team.

Für strukturierte Kataloge dienen die vier Textdateien als Muster. Ein Vergleichsbedarf trägt die Kennung seines Bezugsservices. Bei einer Vorlage mit dem Feld „Pflichtangaben“ übernimmt die Anwendung die dort genannten Felder. Für normalen Fließtext nutzt der Server die ausgewählten Fundstellen, lässt Qwen die Antwort formulieren und prüft die Quellenbelege. Der Test mit einer eigens hochgeladenen Arbeitsnotiz hat einen neuen Servicenamen und eine neue Zuständigkeit aus dem Original richtig ermittelt.

## In einer frischen Installation aktivieren

Der [Installationsbefehl](../README.md) richtet Modell, Wissensfilter und die vier Übungsquellen ein und prüft das Ergebnis im angemeldeten Chat. Das Prüfprotokoll liegt in `run/abnahme.json` der Installation. Ein erneuter Aufruf verwendet die vorhandene Übungssammlung.

Die End-to-End-Prüfung umfasst die Serviceauswahl, das Folgegespräch, fehlende Angaben, Originaldateien, einen Katalogwechsel und eine unformatierte Arbeitsnotiz. [Prüfergebnisse](../results/digitom-bedarf.json)

Für die Vorführung mit zwei Fachbereichen gibt es einen eigenen Fall mit amtlichem Lageplan, Gruppenrechten und einem persönlichen Prüfvermerk aus dem Chat. [Kleineschholz vorführen](fachbereiche/Vorfuehrung.md)

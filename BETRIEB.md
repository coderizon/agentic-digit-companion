# Freiburger Stadtchat auf vier Tesla T4

Open WebUI beantwortet Bürgerfragen aus öffentlichen Freiburger Quellen. Für Haushalt, bestimmte FR.ITZ-Statistiken, OParl-Metadaten und VAG-Linien liest der Dienst die Originaldatei. Bei freien Fragen kombiniert er BM25 und die TopK-Vektorsuche. BGE bewertet die Treffer, Strata betreibt Qwen3.8-Flash-Next 125B in der Quantisierung UD-Q4_K_XL. Jede ausgegebene Fundstelle verweist auf das öffentliche Original.

In derselben Oberfläche begleitet der eigenständige **DIGITOM · Bedarfsassistent** ein Fachamt vom ersten Anliegen bis zum Steckbrief. Er arbeitet mit einem Servicekatalog, früheren Bedarfen und einer Vorlage aus der gewählten Wissenssammlung. Der mitgelieferte Fall verwendet fiktive Unterlagen. Zum Vorführen auf einem Windows-Laptop mit Python genügt ein Doppelklick auf `DIGITOM-Demo-starten.cmd`. [Vorführung und eigene Daten](demo/MORGEN.md)

## Einzelne Dienste und Diagnose

Der Standardweg für eine neue VM ist `bash setup.sh` im Clone. Er installiert Open WebUI nativ in einer eigenen Python-Umgebung, richtet den Bedarfsassistenten ein und prüft ihn. Ein Supervisor verwaltet die vier Prozesse. [Schnellstart und Architektur](README.md) · [Betrieb auf der Ziel-VM](AGENTS.md)

Für eine gezielte Diagnose kann ein Betreiber einen angehaltenen Einzeldienst mit `bash "$FREIBURG_ROOT/ops/run.sh" search`, `reader`, `gateway` oder `webui` im Vordergrund starten. Dafür zuerst die Laufzeitwurzel exportieren und `run/settings.sh` dieser Installation laden. Die älteren Bausteine `ops/install.sh` und `ops/webui-native-install.sh` richten Pakete und Modelle ein. Wer den Gesamtinstaller benutzt, findet alle vier ausführbaren Dienste bereits im Installationsverzeichnis. Die ergänzende Docker-Variante steht in `ops/webui.sh`.

Der Gateway-Healthcheck antwortet mit HTTP 200, sobald der Suchindex und Strata erreichbar sind. Der Anmelde- und Quellentest steht in `run/abnahme.json`. Der authentifizierte Gateway-Endpunkt ist über die lokale Laufzeitdatei `app/chat.key` nutzbar. Ein regulärer Abruf aus einem Browser läuft über Open WebUI und die dortigen Benutzerrechte.

Für einen neu erhobenen öffentlichen Datenstand im Clone `bash setup.sh --fresh` verwenden. Der Aufbau benötigt Tesseract mit deutschem Sprachpaket sowie Zeit für den Abruf und das Einbetten der Seiten. Eine fertig geprüfte Generation wird atomar aktiviert. Anschließend starten die eigenen Dienste neu und der Installer prüft den Bedarfsassistenten. Die erwarteten Zahlen der mitgelieferten öffentlichen Tests beziehen sich auf den 24. November 2026.

## Antwortwege

| Frage | Verarbeitung | Nachweis |
|---|---|---|
| Betrag, Bevölkerungszahl, Ratsvorlagen-Metadatum, Linienweg | Direktzugriff auf registrierte Originaldateien | CSV-Zelle, Registereintrag oder GTFS-Zeile |
| Einzelne Regel aus einer Satzung | Hybridsuche, BGE, Auswahl eines zusammenhängenden Originalabsatzes durch Qwen | Wortlaut und PDF-Seite |
| Eindeutige Einzelangabe zu Preis, Zeit oder Maß | Deutscher extraktiver Leser auf der CPU prüft Kandidaten auf Antwortbarkeit und eine passende Textspanne. Nur bei klarer Übereinstimmung mit dem BGE-Dokument wird direkt der vollständige Originalabsatz ausgegeben. | Wortlaut und PDF-Seite |
| Frage mit mehreren Quellen oder erforderlicher Erklärung | Hybridsuche, BGE, Modellantwort, lokale Zitatprüfung und zweiter Modellaufruf | Geprüfte Zitate und Fundstellen |
| Karte aus einer Satzung | Seitenauswahl, EVIE-Bildsuche und Abgleich mit den Beschriftungen | Verlinkte und per SHA-256 geprüfte Originalansicht |
| Fehlender Livewert oder fehlende Belege | Benennung der Datenlücke | Hinweis zum verfügbaren Datenstand |

Häufig wiederholte identische Einzelfragen können ein belegtes Ergebnis aus dem Zwischenspeicher erhalten. Er ist an Prozess und Datenstand gebunden. Rückfragen mit Gesprächskontext werden jeweils neu ausgewertet. Direkte Tabellenanfragen haben einen eigenen Verarbeitungspfad, damit lange Modellantworten sie nicht blockieren. Für Dokumentfragen begrenzen eine Warteschlange und vier Modellplätze die gleichzeitige Last. Bei voller Dokumentwarteschlange antwortet der Dienst mit HTTP 429 und `Retry-After`.

## Einstellungen in Open WebUI

Beim ausgelieferten Modell `freiburg-stadtdaten` steuert der Gateway die internen Qwen-Aufrufe. Die Temperatur ist auf `0` gesetzt, jeder Arbeitsschritt hat seinen eigenen Systemprompt. Änderungen dieser beiden UI-Felder erreichen Qwen in dieser Version nicht. Bei einer Tabellenfrage bestimmt bereits die belegte Originalzelle die Ausgabe. Ein zusätzlicher Systemprompt im Gespräch kann bei Dokumentfragen den Cache für einzelne Fragen umgehen.

Als Admin unter **Arbeitsbereich → Wissen** eine Sammlung anlegen, Dateien hochladen und die Leserechte festlegen. Im Nachrichtenfeld `#` tippen und den Namen der Sammlung aus der Liste auswählen. Mehrere Sammlungen lassen sich nacheinander anhängen. Ohne Anhang läuft der öffentliche Stadtchat wie im Lasttest. Mit einer Wissensauswahl arbeitet die Pipeline mit den freigegebenen Dateien. Wenn die Frage ausdrücklich zum Vergleich mit dem Freiburger Stadtrecht oder öffentlichen Daten auffordert, wird ergänzend der Stadtindex abgefragt.

Für ein normales Benutzerkonto zwei Lesefreigaben setzen. Unter **Einstellungen → Modelle** den Zugriff auf *Freiburger Stadtdaten* freigeben. Unter **Arbeitsbereich → Wissen** die gewünschten Sammlungen mit dem Benutzer oder seiner Gruppe teilen. Die Rechte auf ein Modell allein geben keinen Zugriff auf eine private Sammlung.

Für die Sammlungen nutzt die WebUI lokale mehrsprachige E5-Embeddings. Der Filter prüft beim Abruf Benutzerrechte und Dateizugehörigkeit und übergibt die passenden Originalpassagen an den Stadtchat. Dort ordnet BGE die Stellen nach Relevanz. Qwen formuliert die Antwort, die Pipeline prüft Zitate und Aussagen und verlinkt die zugehörige Originaldatei. Die Originaldatei selbst prüft beim Öffnen erneut die Leseberechtigung. Antworten aus Sammlungen werden vom gemeinsamen Antwortcache ausgeschlossen. Die Anzahl der vorab gefundenen Stellen pro Sammlung lässt sich im Adminbereich bei **Dokumente → Top K** einstellen (1 bis 12). Das Zerlegen von Dateien in Abschnitte wirkt beim neuen Einlesen. [Open WebUI zu Chatparametern](https://docs.openwebui.com/features/chat-conversations/chat-features/chat-params/) · [Wissenssammlungen und `#`](https://docs.openwebui.com/features/workspace/knowledge/)

## Eigene Messungen

Die gemessenen Wartezeiten und die Zahl der richtig belegten Antworten stehen im [Testbericht für die vier T4](results/MESSUNG.md). Vier Modellplätze bedeuten, dass Qwen gleichzeitig an bis zu vier Antworten schreiben kann. Eine Frage nach einem eindeutig bezeichneten Haushaltsbetrag belegt keinen dieser Plätze.

Die Tests laufen gegen den angemeldeten Chat-Gateway. Die erwarteten Ergebnisse für die 53 Haushaltsfragen enthalten den Betrag und die genaue Originalzelle oder die erwartete Rückfrage. Bei den weiteren Bürgerfragen werden Antwortmerkmale und verlinkte Originalseite geprüft. Für Karten wird zusätzlich das Bild aus dem Dienst geladen und dessen SHA-256 verglichen.

```bash
export FREIBURG_ROOT="${FREIBURG_ROOT:-$HOME/.local/share/agentic-rag-digitom}"
cd "$FREIBURG_ROOT"
. run/settings.sh
export FREIBURG_CHAT_URL="http://127.0.0.1:$FREIBURG_GATEWAY_PORT/v1/chat/completions"
.venv/bin/python eval/check.py --suite budget --parallel 8 --output results/haushalt.json
.venv/bin/python eval/check.py --suite documents --uncached --parallel 2 --output results/buergerfragen.json
.venv/bin/python eval/check.py --suite mixed --count 200 --parallel 200 --uncached --output results/last-200.json
.venv/bin/python eval/check.py --suite mixed-distinct --count 200 --parallel 200 --uncached --output results/last-200-verschieden.json
.venv/bin/python eval/reader_load.py --parallel 4 --output results/reines-modell.json
```

Die Testmischung `mixed` umfasst vier Tabellenfragen je Dokumentfrage. Bei großen Gruppen wiederholt sie einen dokumentierten Katalog. `--uncached` schaltet für den Vergleich den Ergebniscache und die Zusammenlegung gleicher Modellanfragen aus. Retrieval- und Engine-Caches können warm sein. Die Gateway-Zeit schließt die Quellenprüfung ein. Der separate Strata-Test misst zusätzlich die Zeit bis zum ersten sichtbaren Antworttoken.

In `mixed-distinct` fragt jede fünfte Person nach einem anderen Paragrafen. Bei 200 gleichzeitigen Anfragen sind das 160 verschiedene Haushaltspositionen und 40 verschiedene Erklärfragen. Die Messuhr läuft auf dem Testserver. Der Weg vom Browser über das Internet kommt im Alltag hinzu.

Für einen Burst mit unterschiedlichen Beträgen erzeugt `--suite distinct --count 200 --parallel 200` zweihundert verschiedene Fragen und vergleicht jede Antwort mit ihrer eigenen Originalzelle. `--suite docburst --count 200 --parallel 200 --uncached` erzeugt dagegen zweihundert verschiedene Erklärfragen zu nummerierten PDF-Abschnitten. Bei diesem Kapazitätstest zählt der automatische Quellentest die erwartete Originalseite. Die inhaltliche Prüfung frei formulierter Fragen erfolgt mit `--suite documents` und `--suite holdout`.

## Technische Grundlagen

Die Quellenrecherche übernimmt Teile des bestehenden Richtlinien-Assistenten aus einem festgehaltenen Git-Stand. Die Stadtanwendung verwendet ausschließlich ihren Freiburger Index. [Referenzarchitektur](https://github.com/coderizon/Richtlinien-Assistant/tree/762c9b3db4e999f142c27d814ecbd81b21108974)

Die vier Modellplätze teilen sich die T4 und den Hauptspeicher. Strata beschreibt für Einzelanfragen und gebündelte Anfragen unterschiedliche Pfade für die spekulative Decodierung. [Strata-Batching](https://github.com/Niko1221/Strata/blob/main/docs/BATCHING.md) · [Festgelegte Qwen-Gewichte](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF/tree/38bb39ee97821de2c9009abb7e93950eec396e66/UD-Q4_K_XL)

Der kleine deutsche Leser bewertet mögliche Antwortspannen und unbeantwortbare Fragen. Die Anwendung prüft zusätzlich die Einheit, den Abstand zum nächstbesten Kandidaten, die Dokumentzuordnung und den zusammenhängenden Originalwortlaut. [Modellkarte des deutschen extraktiven Lesers](https://huggingface.co/deutsche-telekom/electra-base-de-squad2)

# Benchmarks auf vier Tesla T4

Gemessen am **25. November 2026** auf der gemieteten Referenz-VM mit vier Tesla T4 zu je 15.360 MiB, zwei Xeon Gold 6132 und rund 483 GiB nutzbarem RAM. Strata stellt vier Modellplätze für Qwen3.8-Flash-Next 125B bereit. Die Zeiten reichen bis zur vollständigen Antwort einschließlich der im jeweiligen Antwortweg vorgesehenen Belegprüfung. Alle Lastgeneratoren liefen auf der VM. Netzwerkweg zum Rathaus, Darstellung im Browser und menschliche Lesezeit kommen beim Einsatz hinzu. [Hardware und Laufzeit](results/system.json) · [Software- und Quellenrevisionen](ops/provenance.json)

## DIGITOM mit eigenen Benutzerkonten

Jede simulierte Person war als **anderer regulärer Open-WebUI-Benutzer** angemeldet. Alle schickten gleichzeitig dieselbe Spielplatzfrage. Direkt nach der ersten Antwort forderte jede Person ihren eigenen Steckbrief für einen anderen Bauhofbezirk an. Der Test prüfte Service, verantwortliches Team, Links zum Original und Vergleichsfall sowie den individuellen Empfänger, das Erfolgskriterium und die verlinkte Steckbriefvorlage. 200 Personen erzeugen bei vollständigem Durchlauf **400 Nachrichten in zwei Gesprächsrunden**.

Für diese getrennte Abnahmeinstanz galten derselbe synthetische Katalog und die normalen Leserechte. Gemessen wurde am angemeldeten WebUI-Endpunkt, von der ersten Frage bis zur fertigen Folgeantwort, ohne Denkpause zwischen den Nachrichten. Die erste Abfrage war für alle gleich. Der strukturierte Musterfall konnte aus den Katalogfeldern beantwortet werden. Aus diesem Test lässt sich die Kapazität für unterschiedliche frei formulierte Modellfragen nicht ableiten.

**Erster Lauf mit dem ursprünglichen Linux-Prozesslimit von 1.024 offenen Dateien:** 10/10 korrekte Dialoge in 3,61 s, 50/50 in 23,93 s und 161/200 in 93,65 s bis zum letzten Abschluss oder Fehler. Bei 200 Konten endeten 23 erste Nachrichten und 16 Folgeanfragen mit HTTP-Fehler. Open WebUI protokollierte dabei mehrfach ausgeschöpfte Dateideskriptoren. [10 Konten](results/digitom-konten-10.json) · [50 Konten](results/digitom-konten-50.json) · [200 Konten](results/digitom-konten-200.json)

## Haushaltszahlen aus der Originaltabelle

Jede Anfrage betraf eine andere Position im öffentlichen Freiburger Haushalt. Die Prüfung verglich **Betrag, Original-URL, Zeile und Spalte**. Der Gateway las die Werte direkt aus der Tabelle. In beiden Gateway-Durchläufen war der Antwortcache deaktiviert.

| Gleichzeitig gestartete Fragen | Korrekt belegt | Hälfte der Antworten da | 95 % der Antworten da | Alle fertig | Wiederholung, alle fertig |
|---:|---:|---:|---:|---:|---:|
| 10 | 10/10 | 0,033 s | 0,048 s | 0,050 s | 0,049 s |
| 50 | 50/50 | 0,154 s | 0,226 s | 0,234 s | 0,232 s |
| 200 | 200/200 | 0,568 s | 0,878 s | 0,897 s | 0,894 s |

Am **angemeldeten Chat-Endpunkt von Open WebUI** waren 10 Haushaltsantworten nach **0,321 s** fertig. Für 200 brauchte die Oberfläche in zwei Läufen **8,73 s** und **8,06 s**. Jeweils stimmten alle Werte samt Quellen. Die Aufrufe nutzten eine Testsitzung und einen aktiven Wissensfilter mit der normalen Cache-Einstellung. [Gateway 10](results/distinct-10.json) · [50](results/distinct-50.json) · [200](results/distinct-200.json) · [Wiederholung 200](results/zweitlauf-200.json) · [Open WebUI 10](results/webui-haushalt-10.json) · [Open WebUI 200](results/webui-haushalt-200.json) · [Open WebUI Wiederholung](results/webui-haushalt-200-mitfilter-zweitlauf.json)

## Erklärfragen mit Recherche und Modell

| Gleichzeitig | Gefragte Arbeit | Korrekt belegt | Gemessene Wartezeit |
|---:|---|---:|---|
| 10 | Verschiedene Bürgerfragen zu Satzungen | 10/10 | Median 52,8 s, letzte Antwort 96,4 s |
| 200 | 160 verschiedene Haushaltspositionen und 40 verschiedene Paragrafenfragen | 160/160 Tabellenfragen und 9/40 Paragrafenfragen | Alle Tabellenwerte innerhalb von 0,84 s. 10 der Erklärfragen erreichten das Client-Zeitlimit von 850 s. |
| 200 | 200 unterschiedliche Paragrafenfragen | 5/200 mit der erwarteten Originalseite | 7 HTTP-Erfolge, davon 5 mit passender Seite. 184 Auslastungsmeldungen (429), 2 Dienstfehler (503), 7 Zeitüberschreitungen. Median der 7 HTTP-Erfolge 617 s. |

Für den isolierten Modelltest lag die passende Fundstelle bereits vor. Ein einzelner kurzer Satz war nach **16,1 s** fertig, der erste sichtbare Text nach **10,4 s**. Bei vier gleichzeitigen Aufrufen waren alle vier Sätze nach **39,8 s** fertig. Vier Modellplätze begrenzen die gleichzeitig generierten Antworten. Direkte Tabellenabfragen benötigen keinen dieser Plätze. [10 Bürgerfragen](results/documents-10.json) · [200 gemischte Anfragen](results/mixed-200-distinct.json) · [200 Paragrafenfragen](results/200-dokumentfragen.json) · [Modell einzeln](results/reader-1.json) · [Modell vierfach](results/reader-4.json)

## Fragen, Quellen und Berechtigungen

- Der Bedarfsassistent bestand **13/13 Funktionsprüfungen**, darunter Serviceauswahl mit Quelle, Folgefrage, Steckbrief, Wechsel der Wissenssammlung und eine unstrukturierte Arbeitsnotiz. Der gesonderte Rechtetest bestand **10/10** Fälle, einschließlich nachträglich entzogener Leserechte. [Bedarfsdialoge](results/digitom-bedarf.json) · [Berechtigungen](results/webui-wissensrechte.json)
- Im Stadtchat bestanden **16/16 Bürgerfragen** und **10/10 zusätzliche Prüffragen** mit erwarteten Antwortmerkmalen und Originalquellen. Die Kapazitätsgrenze bei vielen gleichzeitigen Dokumentfragen ist in der Tabelle oben ausgewiesen. [Bürgerfragen](results/buergerfragen-zweitlauf.json) · [Zusätzliche Prüffragen](results/holdout.json)

## Auf der eigenen VM wiederholen

Die folgenden Aufrufe nutzen die Daten und Zugangsdaten der lokalen Testinstallation. Die Haushaltsprüfung leitet ihre erwarteten Werte unabhängig aus den Originalzellen ab. Für reproduzierbare Vergleiche den geprüften Snapshot importieren. Große Lastläufe auf einer dafür eingeplanten Testinstanz ausführen.

```bash
export FREIBURG_ROOT="${FREIBURG_ROOT:-$HOME/.local/share/agentic-rag-digitom}"
cd "$FREIBURG_ROOT"
. run/settings.sh
export FREIBURG_CHAT_URL="http://127.0.0.1:$FREIBURG_GATEWAY_PORT/v1/chat/completions"
for n in 10 50 200; do
  .venv/bin/python eval/check.py --suite distinct --count "$n" --parallel "$n" --uncached --output "run/haushalt-$n.json"
done
.venv/bin/python eval/check.py --suite text --count 10 --parallel 10 --uncached --output run/satzungen-10.json
```

Für parallele Bedarfsgespräche `eval/digitom_load.py` verwenden. Die Option `--accounts-file` erwartet eine private JSON-Liste gültiger Sitzungstokens mit Dateimodus 600. Für jede parallele Person braucht es ein **eigenes reguläres Testkonto** mit Leserecht auf Übungssammlung und Modell. Das Skript prüft die unterschiedlichen Konten vor der Zeitmessung. Die Ergebnisdatei enthält nur Prüfergebnisse, Zeiten und HTTP-Status. Für den zweiten Schritt eines Gesprächs wird die erste Antwort als Verlauf mitgegeben.

```bash
.venv/bin/python eval/digitom_load.py --port "$FREIBURG_WEBUI_PORT" --parallel 10 --accounts-file /geschuetzter/pfad/konten.json --output run/bedarf-10.json
```

[Ausführlicher Messbericht mit weiteren Versuchen](results/MESSUNG.md)

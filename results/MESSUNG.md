# Freiburger Stadtchat auf vier Tesla T4

Am 25. November 2026 hat der Stadtchat zwei Testläufe mit je **200 verschiedenen Haushaltsfragen** in **0,897 und 0,894 Sekunden** abgeschlossen. In beiden Läufen stimmten **200 von 200 Beträgen** samt Link, Zeile und Spalte der Freiburger Originaldatei. Dafür waren **null Aufrufe an Qwen** nötig. Alle HTTP-Anfragen wurden gleichzeitig auf dem Testserver gestartet. Die Messuhr endete mit der letzten vollständigen Antwort. [Einzelantworten des ersten Laufs](distinct-200.json) · [Wiederholung](zweitlauf-200.json)

| Gleichzeitig abgeschickte Tabellenfragen | Korrekt belegt | Bis alle Antworten da waren, erster Lauf |
|---|---:|---:|
| 10 | 10 von 10 | 0,05 s |
| 50 | 50 von 50 | 0,23 s |
| 200 | 200 von 200 | 0,90 s |

Alle 200 Fragen folgten derselben Satzvorlage für unterschiedliche Originalzellen. Beispielsweise ergab die Abfrage zu Produkt 263000, Amt 400, für 2025 bei den Musikschulen **10.000 Euro geplante Erträge** (Zeile 153). Im separaten Test mit Bürgerformulierungen lieferte „Wie viel will Freiburg 2026 für sein Theater ausgeben?“ **22.590.272 Euro geplante Aufwendungen**. „Mit welchen Einnahmen rechnet die VHS 2026?“ ergab **330.150 Euro**. Bei der Stadtbibliothek fragte der Chat nach dem gewünschten Bereich. [Haushalts- und Rückfragentest](budget-complete.json)

**Freie Bürgerfragen dauern länger.** Im Vergleich mit denselben vier Fragen zu Hundesteuer, Bewohnerparken und Bibliothek sank die Zeit für die gesamte Gruppe von **324 auf 46 Sekunden**. Jeweils zwei Fragen waren gleichzeitig unterwegs. Alle vier Antworten enthielten die erwartete Information und die richtige Originalseite. Zwei Antworten wählte Qwen aus. Bei zwei weiteren fand ein kleiner deutscher Leser auf der CPU die eindeutige Textstelle. [Ausgangsmessung](doc-initial.json) · [Zwischenschritt](doc-tuned-four.json) · [Optimierter Stand](doc-final-four.json)

Die Stadtanwendung arbeitet mit 2.149 öffentlichen Quellen im Register, 5.411 Textpassagen und 48 Bildseiten. Die gemietete VM hat vier Tesla T4 mit je 15.360 MiB sichtbarem Grafikspeicher und zwei Intel Xeon Gold 6132. Für die Lasttests standen dem Container rund 483 GiB Hauptspeicher zur Verfügung. Strata meldete vier aktive Modellplätze. Jeder Platz kann an einer Antwort arbeiten. Die Plätze teilen sich die vier GPUs. [Messwerte der Hardware und Laufzeit](system.json) · [Datenstand und Modellrevision](../ops/provenance.json)

## Wartezeit bei Modellfragen

Bei **zehn verschiedenen gleichzeitig gestellten Bürgerfragen zu Satzungen** stimmten zehn Antworten und Quellen. Die Hälfte der Antworten war nach spätestens 52,8 Sekunden da, die letzte nach 96,4 Sekunden. [Vollständiger Stadtchat mit Recherche](documents-10.json)

Für einen getrennten Versuch bekam Qwen die richtige Fundstelle schon mit der Frage. Bei einer Anfrage erschien der erste sichtbare Text nach **10,4 Sekunden**, der vollständige kurze Satz nach **16,1 Sekunden**. Bei vier gleichzeitigen Anfragen hatten alle nach **33,4 Sekunden** den ersten Text und nach **39,8 Sekunden** ihre vollständige Antwort. Das ist die Zeit am direkten Strata-Endpunkt. Die Stadtoberfläche liefert eine Dokumentantwort nach der Quellenprüfung aus. [Einzelanfrage](reader-1.json) · [Vier gleichzeitige Anfragen](reader-4.json)

In einem gleichzeitigen Ansturm von **160 unterschiedlichen Tabellenfragen und 40 unterschiedlichen Erklärfragen** waren alle 160 Beträge samt Fundstelle spätestens nach 0,84 Sekunden da. Bei den 40 Erklärfragen erreichten neun die erwartete Originalseite, eine wurde falsch als Haushaltsfrage eingeordnet, 15 bekamen eine Auslastungsmeldung, fünf eine Dienstfehlermeldung und zehn erreichten nach 850 Sekunden das Zeitlimit des Clients. Der dokumentierte Trefferfehler betraf eine satzungsrechtliche Aufwandsentschädigung. Die nachgeprüfte Frage wird inzwischen von der Tabellenroute ausgeschlossen. Beim Einzelaufruf nach dem Lasttest meldete der Dienst HTTP 503. Eine richtige Endantwort für diese konkrete Frage ist damit weiterhin offen. [Einzelwerte der gemischten Last](mixed-200-distinct.json) · [Nachprüfung der Tabellenroute](paragraf-routing-kontrolle.json)

## Über Open WebUI

Bei einem unmittelbar anschließenden Burst aus 200 unterschiedlichen Paragrafenfragen kamen sieben Antworten mit HTTP 200 zurück, fünf davon mit der erwarteten Originalseite. 184 Anfragen erhielten HTTP 429, zwei HTTP 503 und sieben erreichten das Client-Zeitlimit. Bei den erfolgreichen HTTP-Antworten lag der Median bei rund 617 Sekunden. Für 200 gleichzeitig frei formulierte Rechtsfragen war diese Konfiguration unter der gemessenen Last überfordert. [Paragrafenfragen](200-dokumentfragen.json)

Über den lokalen Chatendpunkt der angemeldeten Open-WebUI-Testinstanz haben wir denselben Haushaltskatalog mit 200 verschiedenen Fragen geprüft. Der erste Lauf mit aktiviertem Wissensfilter war nach **8,73 Sekunden** fertig, der zweite nach **8,06 Sekunden**. Alle Beträge und Fundstellen stimmten. Bei einem Vergleichslauf auf derselben Testoberfläche mit deaktiviertem Filter waren es **7,34 Sekunden**. Die Wissensauswahl blieb für diese Tabellenfragen leer. Die Messungen fanden während weiterer Serverlast statt. Sie schließen Open WebUI und seinen Berechtigungsweg ein. Der Browseraufbau auf dem Endgerät kommt hinzu. [Mit Filter](webui-haushalt-200.json) · [Wiederholung](webui-haushalt-200-mitfilter-zweitlauf.json) · [Vergleich ohne Filter](webui-haushalt-200-ohnefilter.json)

## Was geprüft wurde

Der DIGITOM-Bedarfsassistent bestand 13 Prüfungen über Open WebUI. Dazu gehörten die Serviceauswahl samt Vergleichsfall, Entwürfe mit offenen oder ergänzten Feldern, Rückfragen bei mehreren passenden Services, drei Originalquellen, der Wechsel zu einem Ersatzkatalog und eine unformatierte Arbeitsnotiz. Die letzte Abfrage durchlief die Modellprüfung im letzten Durchlauf in rund 34 Sekunden. Der kurze strukturierte Musterfall brauchte im Test pro Anfrage rund 0,1 Sekunden auf dem Server. Ein echter Browserdurchlauf prüfte zusätzlich Modellwahl, Folgefrage und Zugriff auf das Original über die angemeldete Sitzung. Zehn separate Prüfungen betrafen die Zugriffsrechte auf Wissenssammlungen. [DIGITOM-Abnahme](digitom-bedarf.json) · [Test am bereitgestellten Chat](digitom-live.json) · [Browserprobe](digitom-browser.json) · [Wissensrechte](webui-wissensrechte.json)

Im abschließenden Durchlauf bestanden **16 von 16 Bürgerfragen** und **10 von 10 zusätzlichen Prüffragen**. Ein früherer Lauf hatte eine allgemeine Bewohnerparkgebühr fälschlich auf einen Hubschrauber übertragen. Nach einer Prüfung, ob die Originalstelle auch den konkret erfragten Gegenstand abdeckt, gab der Chat die belegte Gebühr bei der normalen Nachfrage aus und meldete bei der Sondernutzung den fehlenden Nachweis. [Bürgerfragen](buergerfragen-zweitlauf.json) · [Zusatzfragen vor der Korrektur](holdout-before.json) · [Zusatzfragen danach](holdout.json)

Bei Beträgen vergleicht der Test mit der öffentlichen CSV-Datei. Bei Bürgerfragen vergleicht er erwartete Antwortmerkmale und die Originalseite. Bei der Kartenfrage prüft er zusätzlich den SHA-256-Wert der ausgelieferten Originalansicht. Die Messungen umfassen den lokalen Chatdienst einschließlich Recherche und Quellenprüfung. Netzlaufzeit vom Endgerät und Darstellung im Browser kommen hinzu. [Fragen und Prüflogik](../eval/check.py)

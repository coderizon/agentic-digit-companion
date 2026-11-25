# Installationsprüfung vom 25. November 2026

Der Gesamtinstaller wurde aus einem Git-Archiv auf der gemieteten Vier-T4-Maschine ausgeführt. Dafür erhielt der Test eigene Anwendungspfade, Ports, eine neue Open-WebUI-Datenbank sowie eigene Gateway-, Such- und Wissensschlüssel. Der laufende Vorführchat blieb währenddessen erreichbar.

| Geprüfter Schritt | Beobachtung |
|---|---|
| Vorprüfung | Vier Tesla T4 mit je 15.360 MiB, NVIDIA-Treiber 595.91.07, rund 483,4 GiB nutzbarer RAM. Ein absichtlich gewählter belegter Fremdport wurde vor dem Start gemeldet. |
| Abhängigkeiten | Die Locks ließen sich für eine leere Python-3.12-Umgebung auflösen. Das waren 105 Pakete für das RAG und 242 für die separate WebUI. Bei der Installation wurden vorbereitete Python-Pakete und Modellgewichte wiederverwendet. |
| Stadtindex und Modell | Der Import prüfte das Archiv und seine Originale. Zwei separate Installationsproben lösten Freiburger Statistik und Haushaltszelle am neuen Pfad auf. Die vier GGUF-Dateien wurden per SHA-256 überprüft. Der Strata-Installer bestätigte die Release-Signatur. [Ergebnis des Datenimports](installationsprobe.json) |
| Dienste | Supervisor startete die Dienste auf separaten Loopback-Ports. Zum Schutz der laufenden Vorführung nutzte die Abnahme den bereits auf den vier T4 geladenen Strata-Reader über einen lokalen Testadapter. Die zusätzliche TopK-Suche und der Reranker der Probe liefen auf der CPU. Der eigentliche Vier-GPU-Modellstart und das GPU-Retrieval sind im [ursprünglichen Hardwaretest](system.json) dokumentiert. |
| Neue WebUI | Das erste lokale Administratorkonto entstand mit geschlossener öffentlicher Registrierung. Anschließend wurde eine weitere Selbstregistrierung mit HTTP 403 abgelehnt. |
| Wiederholung | Erster Lauf und erneutes `bash setup.sh` bestanden je **10/10 Prüfungen** über Open WebUI. Die Übungssammlung enthielt danach weiterhin genau vier Quelldateien. Der Test deckt Service, Rückfragen, Steckbrief und Originalabruf ab. |
| Benutzer-systemd | Die mitgelieferte Unit wurde separat unter Linux mit einem Testprozess und einem Installationspfad mit Leerzeichen gestartet. Der Dienst erhielt die richtige Laufzeitwurzel und das Argument `foreground`. Auf der gemieteten VM wurde der Supervisor ohne systemd betrieben. |
| Dokumentation | Das Diagramm der Rechner und Dienste ließ sich mit acht Kacheln und zwei Rechnerrahmen als SVG rendern. Der zweite Plan zeigt die Abzweigung in Text- und Bildsuche. |

**Offen für die Übergabe an DIGIT:** einmal auf der städtischen VM `bash setup.sh --check` ausführen und anschließend den Live-Abnahmetest mit dem dort freigegebenen Netzweg und persistenten Speicher abschließen. Der Neuabruf sämtlicher Gewichte und öffentlichen Datenquellen wurde in dieser Wiederholungsprobe ausgelassen. Die Zeiten der Installationsprobe sind daher keine Prognose für einen Kaltstart.

[Lasttests mit Benutzerkonten und Quellenprüfung](../BENCHMARKS.md)

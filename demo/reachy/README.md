# Reachy spricht mit dem DIGIT Companion

Die Wireless-Version von Reachy Mini nimmt auf Tastendruck eine Frage auf. Die T4 transkribiert sie mit dem vorhandenen deutschen Whisper-Modell. Der DIGIT Companion prüft die Leseberechtigung des Schulungskontos Stadtplanung und durchsucht die Sammlung „IT-Service · Handbuch RaumPlan“. Die deutsche Windows-Stimme liest die gefundene Anleitung und die Quelle über Reachys Lautsprecher vor. Eine unbeantwortete Frage verweist auf den Ticketentwurf in Open WebUI.

**Beispielfrage**

„Wie melde ich mich zum ersten Mal bei RaumPlan an?“

## Ausprobieren

1. Reachy einschalten und in Reachy Mini Control ins gleiche WLAN wie den Laptop bringen. Im Browser lässt sich der Dienst unter `http://reachy-mini.local:8000/docs` oder mit der in der App angezeigten IP prüfen. [Einrichtung des Wireless-Modells](https://huggingface.co/docs/reachy_mini/en/platforms/reachy_mini/get_started)
2. Das Beispiel verwendet zwei eigene, geprüfte SSH-Verbindungen vom Laptop. Den Roboter als `reachy-eigen` und die T4 als `digit-t4` in der persönlichen SSH-Konfiguration eintragen. Für den Roboter das vom Eigentümer freigegebene Konto verwenden und den Hostschlüssel bei der ersten Anmeldung prüfen. Das Python-SDK wird auf dem Roboter unter `/venvs/apps_venv/bin/python` verwendet.
3. Auf der T4 wird `demo/fachbereiche/sprachfrage.py` neben `it_service.py` ausgeführt. Es verwendet das vorhandene `.audio`-Environment und das für diese fiktive Demo bereitgestellte Schulungskonto. Die Audioaufnahme bleibt im Arbeitsspeicher. Das Öffnen von KIX-Tickets erfolgt im angemeldeten Chat.
4. Auf Windows im Projektordner starten. Die Aufnahme beginnt erst nach dem Drücken der Eingabetaste und endet nach sieben Sekunden.

```powershell
python demo/reachy/companion.py --t4 digit-t4 --robot reachy-eigen
```

Zum Testen der Software vor der Kopplung können `--text "Wie melde ich mich zum ersten Mal bei RaumPlan an?"` oder `--wav frage.wav` den Parameter `--robot` ersetzen. Mit `--antwort-wav antwort.wav` lässt sich die lokale Sprachausgabe prüfen. Eigene SSH-Konfigurationen werden mit `--ssh-config DATEI` geladen.

## Ticket am Roboter melden

Für den beaufsichtigten Test mit erfundenen Störungen dient `tickettest.py`. Der Windows-Laptop verbindet Freibots Mikrofon und Lautsprecher mit der T4. Jede Aufnahme beginnt auf Tastendruck. Die T4 transkribiert die Aufnahme und bearbeitet die Meldung mit dem vorhandenen IT-Service des DIGIT Companion. Bei einer offenen Störung liest Freibot den Entwurf vor. Ein eindeutiges Ja zum vorgelesenen Entwurf löst das KIX-Werkzeug aus. Bei Nein verwirft der Dienst den Entwurf. Die KIX-Nummer erscheint im Testfenster und wird über Freibots Lautsprecher vorgelesen.

Auf dem Laptop werden `requests`, `paramiko` und eine deutsche Windows-Stimme benötigt. Der Roboter muss als `Freibot` erreichbar sein und sein SSH-Hostschlüssel bereits bestätigt sein. Das Programm verwendet den SSH-Agenten oder fragt das Passwort im Terminal ab. Auf der T4 müssen `demo/fachbereiche/freibot_ticket.py`, `sprachfrage.py` und die eingerichtete KIX-Schulungsumgebung vorhanden sein. Das verwendete Schulungskonto heißt `planung`. Echte personenbezogene Störungen gehören außerhalb dieser Demo bearbeitet.

```powershell
python demo/reachy/tickettest.py --t4 digit-t4 --ssh-config PFAD_ZUR_SSH_CONFIG
```

Mit `--akustiktest` spricht der Laptop den erfundenen Druckerfall und die Testfreigabe selbst. Freibot nimmt beide über sein echtes Mikrofon auf. Der Test kann ein Ticket in der verbundenen KIX-Testinstanz erzeugen. Ohne diesen Schalter kommen Meldung und Entscheidung vom Menschen.

### Freies Gespräch mit Aiden

Im Profil `user_personalities/digit_service` hört Freibots Conversation App über das Robotermikrofon zu. Hugging Face Realtime verarbeitet das Gespräch und gibt Aidens Stimme über den Roboter aus. Für die Vorführung sind die Störungen erfunden. Frage zum Beispiel „Freibot, RaumPlan zeigt mir beim Anmelden den Fehler RP zwölf. Was kann ich tun?“ Der Roboter sucht im IT-Handbuch, nennt den Abschnitt und erklärt die Schritte. Nach „Hat das geholfen?“ beendet ein Ja den Fall. Bei „Das hat nicht geholfen“ bietet Freibot einen Ticketentwurf an. Fehlt eine Anleitung, fragt er zunächst nach der genauen Fehlermeldung.

Das native Werkzeug `native/digit_ticket.py` ruft über eine SSH-Verbindung zum Laptop das vorhandene KIX-Testwerkzeug auf der T4 auf. Das Profil steht in `native/profile.md`. Die Gesprächs-App lädt es aus ihrem Verzeichnis `user_personalities/digit_service/` und das Werkzeug aus `tools/`. Die Werkzeugverbindung auf dem Roboter lauscht auf `127.0.0.1:18763`. Laptop und T4 müssen für Tickets erreichbar bleiben.

Freibot spricht mit Aiden durchgehend Alemannisch aus Freiburg im Breisgau, auch bei Handbuchtipps und Ticketfragen. Für die Suche im IT-Handbuch dient die gehörte Originalmeldung. Der KIX-Entwurf enthält diese Meldung und die Rückmeldung zum Tipp. Erst nach dem vorgelesenen Entwurf prüft der Dienst die nächste gesprochene Antwort auf Zustimmung. „Ja, darfst du machen“ gibt den Entwurf frei. „Darfst du nicht“ verwirft ihn. Bei einer Bedingung wie „Ja, aber erst morgen“ fragt Freibot nach.

Auf dem eingerichteten Laptop startet `python demo/reachy/aiden_bridge.py --ssh-config PFAD_ZUR_SSH_CONFIG` die Verbindung. Sie verwendet den hinterlegten SSH-Hostschlüssel der Datei `~/.ssh/known_hosts_freibot` und den SSH-Agenten oder das einmalig übergebene Prozessgeheimnis `FREIBOT_SSH_PASSWORD`. Auf „Soll i dir e Ticket aalege?“ genügen „Ja“, „Kannst du machen“ oder „Jo, mach des bitte“. Bei „Vielleicht“ fragt Freibot nach, bei „Lieber nicht“ verwirft er den Entwurf. Das Werkzeug prüft die transkribierte Nutzerantwort und übernimmt die Nummer aus KIX. Das Qwen-125B-Modell der T4 ist für diesen Ticketweg nicht erforderlich.

### Ruhe und Kamera im Workshop

„Sei mal ruhig“, „Sag mal ruhig“, „Freibot, Ruhe“ und „Freiburg, Ruhe“ pausieren das Gespräch. Der Roboterdienst `freibot-ruhe.service` beendet die Conversation App und erkennt „Hallo Freibot“ lokal mit dem deutschen Vosk-Modell `vosk-model-small-de-0.15`. Das Mikrofon bleibt für das Aufwecken aktiv. Der Dienst verarbeitet die Audiodaten im Arbeitsspeicher. Die separat geplanten Mittagsansagen werden über die Lautsprecher abgespielt. Der Dienst läuft mit dem Konto `pollen`; der Zustand liegt in `~/.local/state/freibot/ruhe`. Bei Bedarf kann der Betreiber die Pause durch Entfernen dieser Datei aufheben.

Bei einer ausdrücklichen Bitte, etwas anzuschauen, nimmt `native/digit_sehen.py` ein einzelnes JPEG auf. Das Werkzeug sendet es direkt über den auf dem Roboter eingerichteten SSH-Alias `digit-vision` an die T4. Der dedizierte Schlüssel ist auf den Aufruf von `demo/reachy/vision_reader.py` beschränkt. Der Bilddienst verarbeitet das Foto im Arbeitsspeicher. Nur die Beschreibung und der Modellname gehen zurück an die Conversation App mit Aiden. Beim Wechsel der T4-Instanz sind der SSH-Alias, der geprüfte Hostschlüssel und die Freigabe dieses Bildkommandos auf der neuen Instanz einzurichten.

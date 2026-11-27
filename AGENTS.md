# DIGITOM auf der Ziel-VM einrichten

Arbeite im Clone dieses Repositorys. Ziel ist eine **eigene Installation** des Freiburger Stadtchats und des DIGITOM-Bedarfsassistenten. Die gemietete Referenz-VM lief mit Ubuntu 24.04, vier Tesla T4 und rund 483 GiB nutzbarem RAM. Das kommunale Zielsystem kann anders administriert sein. Architektur, Ports und Datenwege sind in der [README](README.md) abgebildet. Für die optionalen Module siehe [Fachbereiche, Excel und KIX](demo/fachbereiche/README.md) sowie [Freibot](demo/reachy/README.md).

## Zielsystem feststellen

1. Auf der von DIGIT benannten Linux-VM als vorgesehener Dienstbenutzer anmelden. Die IP, der Benutzer, ein eventuelles VPN und ein möglicher Sprungserver kommen von DIGIT. Die VM muss in *einer* Sitzung vier T4 sehen. Mit `nvidia-smi`, `lscpu`, `free -h` und `df -h` Geräte, CPU, RAM und persistenten Platz prüfen. Das getestete Strata-Binary braucht die CUDA-13-Treiberschnittstelle. Treiber und GPU-Zuteilung verwaltet der Betreiber.
2. Ein leeres, für diesen Benutzer beschreibbares, persistentes Installationsverzeichnis wählen. Vorgabe ist `$HOME/.local/share/agentic-rag-digitom`. Für ein Datenvolume vor allen Befehlen z. B. `export FREIBURG_ROOT=/daten/digitom` setzen. Git-Checkout und Laufzeitdaten in eigenen Verzeichnissen halten. Fremde Dienste auf den Zielports dem Betreiber melden.
3. Die Installation benötigt Linux x86_64, Bash, Python 3 als Bootstrap, `curl`, `tar`, `sha256sum`, `flock` und Zugriff auf den NVIDIA-Treiber. Der Installer beschafft fehlendes `uv` und Python 3.12. Standardmäßig rund 200 GB freien Speicher einplanen. Für den ersten Bezug müssen GitHub mit Release-Assets, PyPI mit Paketdateien, das PyTorch-Wheel-Repository und Hugging Face mit den Modell-Downloadendpunkten erreichbar sein. Ein Neuaufbau benötigt die öffentlichen Freiburger Quellen und Tesseract mit `deu`. Bei gesperrtem Netzzugang die Artefaktbereitstellung mit DIGIT klären. Für den Betrieb mit dem importierten Bestand erfolgen Suche und Inferenz lokal.
4. Für die reproduzierbare Übergabe den bestehenden Snapshot `stadt-20261124.tar.gz` über den freigegebenen Zugang nach `~/stadt-20261124.tar.gz` übertragen. Er gehört neben das Repository. SHA-256 lautet `4c41191019044dd248de7203f040ab850434382ea175d4c3b28ee63784e12534`. [Versionen und Herkunft](ops/provenance.json)

## Installieren und abnehmen

Im Clone `bash setup.sh --check` und danach `bash setup.sh` ausführen. Der Installer erkennt den Snapshot im Home-Verzeichnis. `--snapshot DATEI` nimmt einen expliziten Pfad, `--fresh` baut die öffentlichen Indizes neu. Ein normaler Wiederanlauf nutzt die aktive Datenversion. Den interaktiven Admin-Dialog führt die zuständige Person aus. Unbeaufsichtigt ist eine dem aktuellen Benutzer gehörende Datei mit Modus 600 und den JSON-Feldern `email` und `password` über `--admin-file DATEI` möglich. Bei einem bestehenden Admin mit SSO unterstützt `--admin-token-file DATEI` ein gültiges Sitzungstoken. Zugangsdaten außerhalb des Clone halten. Die Registrierung weiterer Benutzer ist beim neuen Standardstart geschlossen.

Der Installer legt separate Secrets unter `app/{chat,reader,topk,ui}.key` an und prüft vorhandene Berechtigungen. Persönliche SSH-Schlüssel bleiben auf dem Arbeitsplatz. Auf der VM genügen ausgehend die freigegebenen Installationsquellen. Intern lauschen alle vier Dienste auf `127.0.0.1`. Für den Standard-Zugang reicht die vorhandene SSH-Verbindung.

| Lokaler Dienst | Vorgabe | Konfiguration vor dem Setup |
|---|---:|---|
| Open WebUI | 8077 | `FREIBURG_WEBUI_PORT` |
| FastAPI-Gateway | 8090 | `FREIBURG_GATEWAY_PORT` |
| TopK-Suche | 8083 | `FREIBURG_SEARCH_PORT` |
| Strata mit Qwen | 18189 | `FREIBURG_READER_PORT` |

Strata verwendet als Vorgabe GPUs 0,1,2,3, vier Modellplätze, 16k Kontext und MTP. TopK nutzt GPU 3, der BGE-Reranker GPU 2, beide teilen sich die betreffenden Geräte mit dem Modell. Open WebUI bettet Wissenssammlungen mit E5 auf der CPU ein. Bei abweichender Gerätenummerierung `FREIBURG_READER_GPUS`, `FREIBURG_SEARCH_GPU` und `FREIBURG_RERANK_GPU` gemeinsam passend setzen. Der Installer persistiert die wirksamen Betriebsparameter in `run/settings.sh`.

```bash
R="${FREIBURG_ROOT:-$HOME/.local/share/agentic-rag-digitom}"
bash "$R/ops/services.sh" status
. "$R/run/settings.sh"
curl -fsS "http://127.0.0.1:$FREIBURG_GATEWAY_PORT/health"
cat "$R/run/abnahme.json"
```

Erfolg bedeutet vier laufende Dienste, `topk: true` und ein Lesemodell im Health-Endpunkt sowie zehn bestandene Prüfungen. Die Abnahme legt die vier fiktiven Quellen in einer eigenen Wissenssammlung an, prüft ein Gespräch mit Folgefrage, einen zweiten Fall, eine ungeklärte Zuständigkeit sowie den Abruf dreier Originaldateien. Beim Wiederholen verwendet sie dieselben vier Dateien. Die Browserprobe für Menschen steht in [demo/MORGEN.md](demo/MORGEN.md).

## Betrieb und Daten

- Ist ein Benutzer-systemd verfügbar, installiert das Setup `agentic-rag-digitom.service`. Mit `systemctl --user status agentic-rag-digitom.service` prüfen, mit `systemctl --user restart agentic-rag-digitom.service` neu starten. Soll der Dienst bereits beim Booten vor jeder Anmeldung laufen, mit DIGIT den Dauerbetrieb des Benutzermanagers vereinbaren (`loginctl enable-linger`). Ohne Benutzermanager startet der Supervisor im Hintergrund. Nach einem Reboot dann `bash "$R/ops/services.sh" start` ausführen. `--no-systemd` wählt diese Variante ausdrücklich.
- Für Diagnose zuerst `$R/logs/{reader,search,gateway,webui}.log` lesen. Nach einem Fehler die Ursache beheben und denselben Setup-Befehl wiederholen. Auf dem benutzereigenen Socket verwaltet der Supervisor nur die Dienste dieser Installation. Im manuellen Modus beendet `bash "$R/ops/services.sh" stop` diese Dienste. Bei systemd `systemctl --user stop agentic-rag-digitom.service` verwenden.
- Öffentliche Originale und Indexgenerationen liegen unter `$R/data`, die aktive Generation unter `data/import`. Nur der Wiederaufbau oder Import wechselt die Generation. Modellgewichte liegen unter `$R/assets`, Hilfsmodelle unter `$R/models`. Bestehende Datenbanken des Richtlinien-Assistenten gehören zu einer anderen Anwendung.
- Chats, berechtigte Sammlungen, lokale Vektordaten und Uploads liegen im persistenten `$R/webui-data` (überschreibbar über `FREIBURG_WEBUI_DATA`). Der im Chat erzeugte Steckbrief ist Teil seiner Unterhaltung. Für ein konsistentes Betriebsbackup die Dienste geordnet anhalten, dieses **gesamte** Datenverzeichnis, `app/*.key`, den aktiven Quellenstand mit seinen Originalen und `run/settings.sh` sichern und die Dienste wieder starten. Backup und Berechtigungen mit dem Betreiber verwalten.
- Echte Unterlagen auf der freigegebenen Installation unter **Arbeitsbereich → Wissen** hochladen. Leserechte für Modell und Sammlung nach Zielgruppe vergeben. Im Bedarfschat `#` tippen, die gewünschte Sammlung auswählen und einen bekannten Fall mit dem fachlich zuständigen Team prüfen. Die ausdrückliche Auswahl ersetzt für diesen Bedarf die Übungssammlung. Der Übergang nach Confluence, KIX oder in einen offiziellen Freigabeprozess ist eine manuelle Übergabe.
- Ins Git gehören Code, Modellrevisionen, synthetische Demoquellen und anonymisierte Testergebnisse. Stadtarchiv, Echtunterlagen, Chatdaten, Passwörter, Dienst- und SSH-Schlüssel verbleiben außerhalb. Lokale Abnahmeprotokolle unter `run/` sind vom Repository ausgenommen.

Für den parallelen Betrieb hebt der Dienststarter sein weiches Dateideskriptorlimit bis 8.192 an, soweit das bestehende harte Benutzerlimit das zulässt. Das wirksame Limit lässt sich in `/proc/PID/limits` des WebUI-Prozesses prüfen. Das Betriebssystem und andere Nutzer behalten ihre eigenen Limits.

[Antwortwege und Quellenprüfung](BETRIEB.md) · [Benchmarks mit 10, 50 und 200 Anfragen](BENCHMARKS.md)

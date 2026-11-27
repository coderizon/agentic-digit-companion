# Fachbereichsdemo für DIGIT Companion

Die Erweiterung setzt auf der laufenden Freiburger T4-Referenzinstallation mit Open WebUI, Stadtchat-Gateway und Strata auf. Der geprüfte Aufbau verwendete Open WebUI 0.13.4 und den Freiburger Quellenstand vom 24. November 2026. [Filmablauf und Fragen](Vorfuehrung.md)

## Was der Chat verwendet

| Wissenssammlung | Inhalt | Leseberechtigung |
| --- | --- | --- |
| Kleineschholz · Öffentliche Unterlagen | Amtliches PDF mit Lageplan, geprüfte Textfassung der Planlegende und Eintrag zur Ratsvorlage G-26/167 | Beide Fachbereiche |
| Stadtplanung · Interne Prüfunterlagen | Fiktiver interner Arbeitsvermerk zur Anfrage ohne Flurstücksnummer | Gruppe Stadtplanung |
| Kämmerei · Interne Finanzvermerke | Fiktiver Prüfvermerk zur externen Vermessung | Gruppe Kämmerei |
| IT-Service · Handbuch RaumPlan (optional) | Erfundenes IT-Handbuch der Übungsstadt Lindenau mit vier Abschnitten | Beide Fachbereiche |

Das sind native Open-WebUI-Sammlungen. Die optionalen Module für Bedarfsgespräche oder Meetings haben eigene Quellen. Die Freigaben der Fachbereichsdemo berechtigen ihre beiden Konten ausschließlich für die angegebenen Sammlungen und die zugehörigen Chatmodelle. Die Basissuche in öffentlichen Freiburger Daten ist ebenfalls erreichbar.

Eine Frage mit Quellenabruf durchläuft die bestehende Suche des Freiburger Stadtchats über die ausgewählten Sammlungen. Open WebUI prüft die Zugriffsrechte des angemeldeten Kontos. Die vorhandene Integration sucht per Embedding, prüft die Dateizugehörigkeit und signiert die Fundstellen. Das Gateway prüft die Belege und verlinkt die Originale. Über die Raute im Chat lassen sich freigegebene Sammlungen weiter eingrenzen. Der Planwert stammt aus der lesbaren Fassung der amtlichen Legende. Der Originallink öffnet dieselbe Planseite zur Sichtprüfung.

Ein ausdrücklicher Auftrag für den Übungsfall aktiviert einen lokalen MCP-Client in der Fachbereichs-Pipe. Er initialisiert den MCP-Server über **stdio** und liest dessen Werkzeugliste. Qwen wählt in zwei begrenzten Schritten jeweils zwischen einem zugelassenen Werkzeug und Abbruch. Zuerst liest `lagepruefung_lesen` die Quellen im aktuellen Benutzerkontext. Danach darf `pruefvermerk_anlegen` einen persönlichen Prüfentwurf speichern. Der Server prüft dafür die aktuellen Rechte erneut und verwendet die Notes-API desselben Kontos. Ein wiederholter Auftrag führt zur bestehenden Notiz. Die Zugangsdaten erreichen weder das Modell noch ein externes Fachverfahren.

Neben der Notiz erscheint **Excel-Liste herunterladen**. Für einen früheren Entwurf genügt „Exportiere meinen Prüfvermerk als Excel“. Das Werkzeug liest die aktuell gespeicherte Notiz und legt die XLSX als persönliche Datei in Open WebUI ab. Wiederholungen mit gleichem Inhalt verwenden dieselbe Datei. Nach einer Änderung an der Notiz erhält der nächste Export einen neuen Downloadlink.

## Auf einer weiteren Referenzinstallation vorführen

### IT-Hilfe mit KIX Start

Die Frage „RaumPlan zeigt RP-12. Was soll ich tun?“ führt zur Anleitung und zum Abschnitt im Handbuch. Für RP-74 zeigt der Chat einen persönlichen Entwurf mit der beschriebenen Störung und den bereits versuchten Schritten. „Nein“ verwirft ihn. Mit „Ja“ ruft der Chat das Python-MCP-Werkzeug `kix_ticket_anlegen` auf. Das Werkzeug erstellt das Ticket über die KIX-REST-API, liest die Ticketnummer zurück und verlinkt das Agentenportal. Das Portal setzt ein eigenes KIX-Agentenkonto voraus. Mehrfache Freigaben desselben Entwurfs verwenden dieselbe Ticketnummer.

Die Testinstanz verwendet die offiziellen Container von [KIX Start](https://github.com/kix-service-software/kix-on-premise). Auf der gemieteten T4 laufen sie in einem Debian-Gast. KIX-API und Portal sind an Loopback gebunden. Für einen anderen Standort müssen die Zielinstanz, die Service-Desk-Rollen und die Portaladresse feststehen. Die beiden Admin-Anmeldedateien bleiben auf dem eigenen Server. Die Verzeichnisse für den IT-Dienst sind benutzerprivat.

```bash
cd "$FREIBURG_ROOT"
run/fachbereiche-mcp/bin/python demo/fachbereiche/kix_einrichten.py \
    --admin-file /geschuetzt/kix-admin.json --api http://127.0.0.1:19080/api/v1 \
    --role 'Agent User' --role 'Ticket Agent Base Permission' --role 'Ticket Agent (Servicedesk)' \
    --queue 1 --priority 3 --type 2 --state 1
.venv/bin/python demo/fachbereiche/it_einrichten.py \
    --admin-file /geschuetzt/webui-admin.json --portal http://127.0.0.1:8791
.venv/bin/python demo/fachbereiche/kix_abnahme.py
```

Die Beispiel-IDs entsprechen Service Desk, normaler Priorität, Störung und dem Status „neu“ der KIX-Testinstallation. Die Abnahme erzeugt ein echtes Ticket im angegebenen **Testsystem**. Die Filmaufnahme verwendet anschließend einen eigenen Vorgang.

Den Ordner auf den vorbereiteten Linux-Server kopieren und als Dienstbenutzer ausführen. Die Installation erwartet den bestehenden Datenimport mit seinen Originalen, eine erreichbare WebUI und deren lokale Admin-Anmeldedatei mit Modus 600. Die Datei enthält die Felder `email` und `password`. Der erste Bezug des Python-MCP-SDK erfordert Zugriff auf die konfigurierte Paketquelle.

```bash
export FREIBURG_ROOT="/pfad/zur/laufenden/freiburg-rag-installation"
bash installieren.sh /geschuetzter/pfad/admin-anmeldung.json
```

Der Befehl richtet die drei Sammlungen und zwei Übungskonten ein, installiert den lokalen MCP-Dienst in einer eigenen Python-Umgebung und prüft die Funktionen über die normale WebUI-API. Das Ergebnis steht in `$FREIBURG_ROOT/run/fachbereiche/abnahme.json`. Der Test erzeugt einen privaten Beispielentwurf.

Die generierten Anmeldedateien liegen nur für den Dienstbenutzer lesbar unter `$FREIBURG_ROOT/run/fachbereiche/planung.credentials.json` und `finanzen.credentials.json`. Mit jedem Konto im Modell **DIGIT Companion · Fachbereiche** anmelden. Optional lässt sich eine bereits eingerichtete Übung mit folgendem Befehl erneut prüfen.

```bash
"$FREIBURG_ROOT/.venv/bin/python" "$FREIBURG_ROOT/demo/fachbereiche/abnahme.py"
```

Die Prüffälle decken beide Rollen, erlaubte und gesperrte Originaldateien, den gemeinsamen Lageplan, die private Finanzfrage einschließlich Umformulierung, eine fremde Sammlungs-ID, den Notizlink und die Wiederholung des Schreibauftrags ab. Das Übergabepaket enthält ein Ergebnisprotokoll vom T4-Test. Für den kommunalen Betrieb müssen die jeweiligen fachlichen Eigentümer ihre Wissensquellen und Freigaben festlegen. [Open WebUI – Rollen und Berechtigungen](https://docs.openwebui.com/features/authentication-access/rbac/permissions/) · [Model Context Protocol](https://modelcontextprotocol.io/)

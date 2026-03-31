# Änderungsprotokoll

## Grid Stabilization – Vollständige Änderungshistorie

**Dokumentversion:** 1.1  
**Zeitraum:** 24. Januar 2026 – 18. Februar 2026  
**Basisdokument:** Technische Beschreibung v1.0 (24.01.2026)  
**Klassifizierung:** Änderungsprotokoll für den Auftraggeber

---

## Inhaltsverzeichnis

1. [Zusammenfassung](#1-zusammenfassung)
2. [Umbenennung des Systems](#2-umbenennung-des-systems)
3. [Regelungslogik – Kernverbesserungen](#3-regelungslogik--kernverbesserungen)
4. [Physikalische Leistungsmessung (Stromzähler)](#4-physikalische-leistungsmessung-stromzähler)
5. [Kalibrierte Nennleistung](#5-kalibrierte-nennleistung)
6. [Netzwerk- und Infrastruktur](#6-netzwerk--und-infrastruktur)
7. [Dashboard und Benutzeroberfläche](#7-dashboard-und-benutzeroberfläche)
8. [Fernzugriff](#8-fernzugriff)
9. [Konvergenztests und Ergebnisse](#9-konvergenztests-und-ergebnisse)
10. [Spannungsüberwachung und Netzausfallschutz](#10-spannungsüberwachung-und-netzausfallschutz)
11. [Datenpersistenz und historische Anzeige](#11-datenpersistenz-und-historische-anzeige)
12. [Vollständige Commit-Historie](#12-vollständige-commit-historie)

---

## 1. Zusammenfassung

Seit der technischen Erstbeschreibung vom 24. Januar 2026 wurden insgesamt **39 Änderungen** am System vorgenommen. Die Arbeiten umfassen folgende Schwerpunkte:

| Bereich | Änderungen | Beschreibung |
|---------|------------|--------------|
| Regelungslogik | 16 | Feedback-Schleife, Puffer, Cooldowns, Kalibrierung |
| Stromzähler-Integration | 5 | Physikalische Leistungsmessung, Spannungsüberwachung |
| Sicherheit | 2 | Netzausfallschutz über Spannungserkennung |
| Datenpersistenz | 3 | SQLite-Speicherung, Schema-Migration, historische Diagramme |
| Dashboard / UI | 7 | Mobilansicht, Sortierung, Spannung, historische Daten |
| Infrastruktur | 4 | Netzwerkumstellung, Docker-Konfiguration, Fernzugriff |
| Dokumentation | 1 | API-Dokumentation |
| Tests | 2 | Konvergenztest-Skript |

**Wesentliche Verbesserungen im Überblick:**

- Konvergenzzeit zur Zielleistung: **von >4 Minuten auf ~2 Minuten** reduziert
- Nennleistung: **kalibriert auf reale Werte** (103 kW statt überschätzter 122 kW)
- Mess-Grundlage: **Physikalischer Stromzähler** integriert (vorher nur Schätzwerte)
- Umbenennung: **„Net Stabilization" → „Grid Stabilization"**
- Responsives Design: **Mobile Endgeräte** werden vollständig unterstützt
- Netzausfallschutz: **Spannungsüberwachung** mit automatischem Idle bei Phasenausfall
- Datenpersistenz: **Historische Diagramme** aus SQLite-Datenbank, auch nach Neustart verfügbar

---

## 2. Umbenennung des Systems

**Datum:** 16. Februar 2026  
**Commit:** `f7636ed`

Das System wurde von **„Net Stabilization"** in **„Grid Stabilization"** umbenannt. Dies betrifft:

- Anwendungstitel und Favicon (neues SVG-Icon)
- Docker-Container-Name: `grid-stabilization`
- Systemd-Service-Name
- Dashboard-Überschriften und Fußzeile
- Alle Konfigurationsdateien

---

## 3. Regelungslogik – Kernverbesserungen

### 3.1 Feedback-Schleife und Grundstabilität

**Datum:** 30. Januar 2026  
**Commits:** `febedf0`, `eb16ee2`, `95b59d9`

Die ursprüngliche Regelung wurde grundlegend überarbeitet:

| Änderung | Vorher | Nachher |
|----------|--------|---------|
| Regelungsart | Einmalige Zuweisung | Kontinuierliche Feedback-Schleife |
| Toleranzband | Fest | ±5% vom Zielwert |
| Warmup-Phase | Keine | 120 Sekunden nach Aktivierung |
| Regulierungs-Cooldown | Keiner | Richtungsabhängig (Ramp-up / Trim) |
| Temperaturgrenze | Niedrig | Angehoben für Produktionsbetrieb |

### 3.2 Stabilität und Fehlerbehebung

**Datum:** 30. Januar 2026  
**Commits:** `95b59d9`, `f057024`, `cd81d41`, `3028a46`

- **Race-Condition behoben:** Regulierungsschleife konnte Miner nach Deaktivierung fälschlich reaktivieren
- **Blockierende Transition:** Regulierung wurde von Minern im Übergangszustand blockiert — korrigiert
- **Idle-Zustand:** System erzwingt jetzt korrekt den Idle-Zustand bei Standby
- **Statusanzeige:** STANDBY wird nach Deaktivierung korrekt gemeldet

### 3.3 Intelligente Miner-Auswahl

**Datum:** 30. Januar 2026  
**Commits:** `a6e7863`, `a56a961`

Miner mit Vnish-Firmware werden priorisiert ausgewählt:

- Aktivierung: Vnish-fähige Geräte werden bevorzugt eingeschaltet
- Deaktivierung: Nicht-Vnish-Geräte werden zuerst abgeschaltet
- Erkennung basiert auf `firmware_type`-Attribut statt Namensfeld

### 3.4 Zielleistung und Kapazitätsbegrenzung

**Datum:** 9. Februar 2026  
**Commits:** `744ab57`, `467d279`, `2dbfc6d`, `ab36326`, `002a928`

- **Kapazitätsbegrenzung:** Zielleistung wird auf die tatsächlich steuerbare Kapazität begrenzt
- **Fallback-Aktivierung:** Nicht-Vnish-Miner können als Fallback geweckt werden
- **Vnish-Zugangsdaten:** Konfigurierbare API-Zugangsdaten für Vnish-Firmware
- **Leistungsschätzung:** Fehlende Leistungswerte werden geschätzt statt als Null gemeldet
- **Clamping korrigiert:** Regulierungsschleife senkte fälschlich die Zielleistung — behoben

### 3.5 Dynamische Leistungsschätzung

**Datum:** 9. Februar 2026  
**Commits:** `002a928`, `4d0b872`

- Berechnung des durchschnittlichen Verbrauchs pro Miner basiert auf dem **tatsächlichen Flottendurchschnitt** statt eines festen Schätzwerts
- Ersetzt den festen Wert von 1.460 W/Miner durch den realen Messwert (~1.200 W/Miner)

### 3.6 Overshoot-then-Trim-Strategie

**Datum:** 9. Februar 2026  
**Commit:** `4d0b872`

Neue Aktivierungsstrategie für schnellere Konvergenz:

| Parameter | Wert |
|-----------|------|
| Initiale Übersteuerung | 10% über Zielwert |
| Ramp-up-Cooldown | 60 Sekunden |
| Trim-Cooldown | 30 Sekunden |
| Regulierungsintervall | 30 Sekunden |

**Ablauf:**
1. Bei Aktivierung: 10% mehr Miner einschalten als berechnet
2. Warmup-Phase (120s) abwarten
3. Regulierungsschleife trimmt Überschuss in 30-Sekunden-Schritten
4. Hochfahren erfolgt mit 60-Sekunden-Cooldown (vorsichtiger)

### 3.7 Richtungsabhängiger Cooldown

**Datum:** 16. Februar 2026  
**Commits:** `f0f2700`, `ff91aa0`

Die Regulierungsschleife verwendet nun unterschiedliche Cooldowns je nach Richtung:

| Richtung | Cooldown | Begründung |
|----------|----------|------------|
| Ramp-up (Miner einschalten) | 60 Sekunden | Miner brauchen Hochlaufzeit |
| Trim (Miner abschalten) | 30 Sekunden | Sofortige Wirkung beim Abschalten |

### 3.8 Regelungsquelle: Miner-Leistung

**Datum:** 17. Februar 2026  
**Commit:** `62a0676`

Nach Rücksprache mit dem Auftraggeber wurde festgelegt:

> **Die Regelung basiert auf der gemessenen Miner-Leistung (`miners_total_power_kw`), nicht auf der Gesamtanlagenleistung.**

| Parameter | Wert |
|-----------|------|
| Regelgröße | `miners_total_power_kw` (Stromzähler) |
| Anzeige | `plant_total_power_kw` (nur zur Information) |
| Nennleistung | Kalibrierter Miner-Durchschnitt × Anzahl Miner |

*Hinweis: Zuvor wurde kurzfristig auf Anlagenleistung umgestellt (Commit `7b2a7ec`, 16.02.2026), dies wurde nach Klärung mit dem Auftraggeber zurückgenommen.*

---

## 4. Physikalische Leistungsmessung (Stromzähler)

**Datum:** 16. Februar 2026  
**Commits:** `8319ad4`, `79737c1`

### 4.1 Integration

Ein physikalischer Stromzähler (BESS EMS) wurde integriert:

| Parameter | Wert |
|-----------|------|
| Adresse | `192.168.95.4:8044` |
| Protokoll | HTTP REST API |
| Abfrage-Intervall | Bei jedem Statusupdate |

### 4.2 Bereitgestellte Messwerte

| Messwert | Beschreibung | Verwendung |
|----------|--------------|------------|
| `miners_total_power_kw` | Leistung aller Miner | **Regelgröße** |
| `plant_total_power_kw` | Gesamtleistung der Anlage | Anzeige |

### 4.3 Vorher/Nachher

| Aspekt | Vorher | Nachher |
|--------|--------|---------|
| Leistungsmessung | Geschätzt aus Hashrate/Frequenz | Physikalisch gemessen |
| Genauigkeit | ±15-20% | ±1-2% |
| Nennleistung | 121,8 kW (überschätzt) | 103,2 kW (kalibriert) |

### 4.4 Neues Modul

Neues Service-Modul `app/services/power_meter.py` (123 Zeilen):
- Asynchrone HTTP-Abfrage des Stromzählers
- Fehlertoleranz bei Verbindungsausfällen
- Fallback auf Schätzwerte bei Nichterreichbarkeit

---

## 5. Kalibrierte Nennleistung

**Datum:** 16. Februar 2026  
**Commits:** `61604bf`, `5cf83df`

### 5.1 Problem

Die Miner melden per Firmware eine Nennleistung von ~1.460 W, verbrauchen tatsächlich jedoch nur ~1.200 W. Dies führte zu:

- Überschätzte Nennleistung der Flotte (121,8 kW statt real ~103 kW)
- Falsche Berechnung der benötigten Miner-Anzahl bei Aktivierung
- Langsamere Konvergenz durch zu vorsichtiges Einschalten

### 5.2 Lösung

| Parameter | Beschreibung | Wert |
|-----------|--------------|------|
| `_actual_per_miner_kw` | EMA-geglätteter Verbrauch pro Miner | ~1,20 kW |
| `_plant_overhead_kw` | Verbrauch der Anlage ohne Miner | ~6,0 kW |
| Kalibrierungsmethode | Exponentieller gleitender Durchschnitt (EMA) | α = 0,1 |
| Startwert | Basierend auf Erfahrungswerten | 1,20 kW/Miner |

### 5.3 Berechnung der Nennleistung

```
rated_power_kw = _actual_per_miner_kw × total_miner_count
```

**Beispiel (aktuelle Flotte):**  
`103,2 kW = 1,20 kW × 86 Miner`

---

## 6. Netzwerk- und Infrastruktur

### 6.1 Netzwerkumstellung

**Datum:** 13. Februar 2026  
**Commits:** `cf5978d`, `9d73e3c`

| Parameter | Vorher | Nachher |
|-----------|--------|---------|
| Miner-Subnetz | `192.168.1.0/24` | `192.168.95.0/24` |
| Docker-Netzwerk | Bridge mit Port-Mapping | `network_mode: host` |
| Server-IP | Dynamisch | `192.168.95.6` (statisch) |
| Gateway | Default | `192.168.95.2` |

### 6.2 Docker-Konfiguration

**Commit:** `9d73e3c`

Umstellung auf `network_mode: host` für direkte LAN-Kommunikation mit Minern. Port-Mapping entfällt, der Container nutzt das Host-Netzwerk direkt.

### 6.3 Statische IP-Konfiguration

**Datum:** 16. Februar 2026  
**Commit:** `24ed06e`

Skript zur Konfiguration der statischen IP-Adresse des Servers:
- IP: `192.168.95.6/24`
- Gateway: `192.168.95.2`

---

## 7. Dashboard und Benutzeroberfläche

### 7.1 Stromzähler-Anzeige

**Datum:** 16. Februar 2026  
**Commit:** `79737c1`

Neue Anzeige im Dashboard:

| Element | Beschreibung |
|---------|--------------|
| Miner-Leistung (gemessen) | Physikalisch gemessene Miner-Leistung |
| Anlagenleistung (gesamt) | Gesamtverbrauch der Anlage |
| Farbcodierung | Grün/Gelb/Rot je nach Abweichung vom Zielwert |
| Aktualisierungszeitpunkt | Zeitstempel der letzten Messung |

### 7.2 Diagramm- und Sortierverbesserungen

**Datum:** 16. Februar 2026  
**Commit:** `757fd2f`

- Miner werden nach IP-Adresse sortiert angezeigt
- ±5% Toleranzlinien im Leistungsdiagramm
- Farbcodierung der Messwerte (Meter)
- Zeitstempel der letzten Aktualisierung
- Fußzeile mit Systeminformationen
- Dynamische Achsenbeschriftung der Leistung

### 7.3 Cache-Busting

**Datum:** 16. Februar 2026  
**Commit:** `799c561`

Statische Ressourcen (CSS, JavaScript) erhalten automatisch einen Versions-Parameter, um Browser-Caching bei Updates zu verhindern.

### 7.4 Responsives Design (Mobilansicht)

**Datum:** 16. Februar 2026  
**Commit:** `8d22fad`

Vollständiges responsives Design für mobile Endgeräte:

| Bildschirmgröße | Optimierung |
|-----------------|-------------|
| Desktop (>1200px) | Standardansicht |
| Tablet (768-1200px) | Angepasstes Layout |
| Smartphone (<768px) | Vollständig umstrukturiert |

- Touch-optimierte Bedienelemente
- Stapelbare Karten-Layouts
- Angepasste Diagrammgrößen
- Lesbare Tabellen auf kleinen Bildschirmen

### 7.5 Graph-Korrekturen

**Datum:** 30. Januar 2026  
**Commit:** `95b59d9`

- Graph verwendet Leistungswerte vom Backend (vorher teilweise lokale Berechnung)
- Standardansicht auf Rack-Ansicht gesetzt
- Leerlaufleistung für Racks korrigiert

---

## 8. Fernzugriff

**Datum:** 16. Februar 2026  
**Commit:** `06f618e`

### 8.1 Reverse-SSH-Tunnel

Neues Installations-Skript `scripts/setup_remote_access.sh` (222 Zeilen):

| Funktion | Beschreibung |
|----------|--------------|
| Tailscale VPN | Mesh-VPN für sicheren Fernzugriff |
| Reverse SSH Tunnel | Permanenter SSH-Tunnel als Fallback |
| Systemd-Service | Automatischer Neustart bei Verbindungsabbruch |
| AutoSSH | Überwacht und erneuert SSH-Verbindungen |

### 8.2 Zugriffsdaten

| Parameter | Wert |
|-----------|------|
| LAN-IP | `192.168.95.6` |
| Tailscale-IP | `100.125.153.88` |
| SSH-Port | 22 |
| Web-Dashboard | Port 8080 |

---

## 9. Konvergenztests und Ergebnisse

### 9.1 Testinfrastruktur

**Datum:** 16. Februar 2026  
**Commit:** `7b2a7ec` (initial), fortlaufend aktualisiert

Automatisiertes Testskript `scripts/test_convergence.py`:

| Parameter | Wert |
|-----------|------|
| Abfrageintervall | 5 Sekunden |
| Timeout | 6 Minuten |
| Erfolgskriterium | Leistung innerhalb ±5% des Zielwerts |
| Testfälle | 60 kW, 90 kW, Nennleistung |

### 9.2 Finale Testergebnisse

**Datum:** 17. Februar 2026 (nach allen Optimierungen)

| Zielleistung | Konvergenzzeit | Ergebnis | Anmerkung |
|--------------|----------------|----------|-----------|
| 60 kW | 120 s (2,0 min) | ✅ BESTANDEN | Innerhalb ±5% |
| 90 kW | 120 s (2,0 min) | ✅ BESTANDEN | Innerhalb ±5% |
| 103 kW (Nennleistung) | 349 s (5,8 min) | ❌ TIMEOUT | Hardware-Einschränkung |

### 9.3 Erläuterung zum Nennleistungstest

Der Test bei Nennleistung (103 kW) schlägt fehl, weil:

- Von 86 Minern reagieren nur ~76 auf Steuerbefehle
- ~10 Miner sind aufgrund von Hardware-Defekten nicht ansprechbar
- Die erreichbare Maximalleistung liegt daher bei ~91 kW
- **Dies ist kein Software-Fehler, sondern eine Hardware-Limitation**
- Die betroffenen Geräte müssen vom Wartungsteam überprüft werden

### 9.4 Vergleich: Vorher/Nachher

| Metrik | Vorher (Jan 2026) | Nachher (Feb 2026) |
|--------|-------------------|-------------------|
| Konvergenzzeit (60 kW) | >4 Minuten | 2,0 Minuten |
| Konvergenzzeit (90 kW) | >4 Minuten | 2,0 Minuten |
| Nennleistung (gemeldet) | 121,8 kW | 103,2 kW |
| Leistungsmessung | Geschätzt | Physikalisch gemessen |
| Pro-Miner-Schätzung | 1.460 W (fest) | 1.200 W (kalibriert) |

---

## 10. Spannungsüberwachung und Netzausfallschutz

### 10.1 Hintergrund

**Datum:** 17.–18. Februar 2026  
**Commits:** `7414396`, `35a4914`

Zur Absicherung gegen Netzausfälle (z. B. Phasenausfall) wurde eine Spannungsüberwachung implementiert. Der Stromzähler liefert drei Phasenspannungen (L1-N, L2-N, L3-N), deren Durchschnitt als Referenzwert dient.

### 10.2 Funktionsweise

| Parameter | Wert |
|-----------|------|
| **Spannungsschwelle** | 100 V |
| **Normalspannung** | ~230 V |
| **Erkennung** | Durchschnitt aller drei Phasen < 100 V |
| **Reaktion** | Alle Miner sofort auf Idle setzen |
| **Wiederherstellung** | 3 Retry-Runden nach Spannungsrückkehr |

Bei Unterschreitung der Schwelle (erkennt 2+ Phasenausfälle) wird die gesamte Flotte in den Idle-Zustand versetzt. Nach Rückkehr der Spannung erfolgt eine kontrollierte Wiederherstellung mit `_safe_idle_after_power_restore()`.

### 10.3 Dashboard-Anzeige

Die aktuelle Netzspannung wird im Dashboard neben der Stromzähler-Leistung angezeigt. Bei Werten unter 200 V erfolgt eine farbliche Warnung (rot).

---

## 11. Datenpersistenz und historische Anzeige

### 11.1 Hintergrund

**Datum:** 17.–18. Februar 2026  
**Commits:** `c343943`, `2ce6794`, `f9fd762`

Zuvor gingen alle Diagrammdaten bei einem Container-Neustart oder VPN-Unterbrechung verloren, da sie nur im Browser-Speicher (localStorage) gehalten wurden. Die Daten werden nun serverseitig in der SQLite-Datenbank gespeichert und beim Öffnen des Dashboards automatisch geladen.

### 11.2 Per-Miner-Snapshots

Alle 60 Sekunden wird für jeden aktiven Miner ein Datensatz gespeichert:

- IP-Adresse, Hostname
- Hashrate (GH/s), Temperatur (°C)
- Leistungsverbrauch (W), Frequenz (MHz)
- Status (mining/idle/offline)
- Zeitstempel

### 11.3 Fleet-Snapshots (erweitert)

Die Fleet-Snapshots wurden um zusätzliche Felder erweitert:

- `measured_power_kw` – Gemessene Leistung vom Stromzähler
- `plant_power_kw` – Anlagenleistung
- `voltage` – Aktuelle Netzspannung
- `target_power_kw` – Aktuelle Zielleistung

### 11.4 Schema-Migration

Bestehende SQLite-Datenbanken werden automatisch migriert (`_run_migrations()`). Neue Spalten werden per `ALTER TABLE` hinzugefügt, ohne Datenverlust.

### 11.5 API-Endpunkte

| Endpunkt | Methode | Beschreibung |
|----------|---------|--------------|
| `/dashboard/api/fleet-snapshots` | GET | Fleet-Verlauf (Standard: 24h, max. 1440 Punkte) |
| `/dashboard/api/miner-snapshots/{ip}` | GET | Miner-Verlauf (Standard: 24h, max. 1440 Punkte) |

Parameter: `hours` (Zeitraum), `limit` (maximale Datenpunkte).

### 11.6 Frontend-Integration

- **Seitenstart:** `loadHistoryFromServer()` lädt Fleet-Snapshots aus der Datenbank
- **Miner-Modal:** `loadMinerHistoryFromServer()` lädt Per-Miner-Daten beim Öffnen
- **Zeitbereich:** Neuer 24H-Button im Miner-Modal
- **Fallback:** localStorage wird weiterhin als Backup verwendet
- **Aufbewahrung:** 24h für Snapshots, 7 Tage für Befehlshistorie

---

## 12. Vollständige Commit-Historie

Chronologische Auflistung aller Änderungen seit der technischen Beschreibung (Commit `418b43f`, 24.01.2026):

| Nr. | Datum | Commit | Beschreibung |
|-----|-------|--------|--------------|
| 1 | 26.01.2026 | `1549f07` | Dokumentation: Kleinere Aktualisierung der API-Dokumentation |
| 2 | 30.01.2026 | `febedf0` | Regelung: Feedback-Schleife, Idle/Deaktivierung, Temperaturschwelle |
| 3 | 30.01.2026 | `eb16ee2` | Regelung: Warmup-Phase, Cooldown, Transitionsprüfung |
| 4 | 30.01.2026 | `95b59d9` | Regelung: Toleranz auf ±5% geändert |
| 5 | 30.01.2026 | `95b59d9` | Dashboard: Graph nutzt Backend-Werte, Rack-Ansicht, Idle-Korrektur |
| 6 | 30.01.2026 | `f057024` | Bugfix: Race-Condition in Regulierungsschleife |
| 7 | 30.01.2026 | `cd81d41` | Bugfix: Blockierung durch Miner im Übergangszustand |
| 8 | 30.01.2026 | `a6e7863` | Feature: Intelligente Miner-Auswahl (Vnish bevorzugt) |
| 9 | 30.01.2026 | `a56a961` | Bugfix: Vnish-Erkennung über firmware_type |
| 10 | 30.01.2026 | `3028a46` | Bugfix: STANDBY-Status nach Deaktivierung korrekt |
| 11 | 09.02.2026 | `744ab57` | Regelung: Zielleistung auf Kapazität begrenzen |
| 12 | 09.02.2026 | `467d279` | Feature: Fallback-Aktivierung für Nicht-Vnish-Miner |
| 13 | 09.02.2026 | `883e912` | Konfiguration: Vnish-API-Zugangsdaten |
| 14 | 09.02.2026 | `2dbfc6d` | Bugfix: Leistungsschätzung bei fehlenden Werten |
| 15 | 09.02.2026 | `ab36326` | Bugfix: Regulierung senkte Zielleistung fälschlich |
| 16 | 09.02.2026 | `002a928` | Regelung: Flottendurchschnitt statt Festwert |
| 17 | 09.02.2026 | `4d0b872` | Feature: Overshoot-then-Trim-Strategie mit separaten Cooldowns |
| 18 | 13.02.2026 | `cf5978d` | Infrastruktur: Subnetz auf 192.168.95.0/24 umgestellt |
| 19 | 13.02.2026 | `9d73e3c` | Infrastruktur: Docker auf network_mode: host umgestellt |
| 20 | 16.02.2026 | `8319ad4` | Feature: Physikalischer Stromzähler (BESS EMS) integriert |
| 21 | 16.02.2026 | `79737c1` | Dashboard: Stromzähler-Anzeige, Ansichtsmodus-Fix |
| 22 | 16.02.2026 | `24ed06e` | Infrastruktur: Statische IP 192.168.95.6 |
| 23 | 16.02.2026 | `799c561` | Dashboard: Cache-Busting für statische Ressourcen |
| 24 | 16.02.2026 | `757fd2f` | Dashboard: Sortierung, Toleranzlinien, Zeitstempel, Fußzeile |
| 25 | 16.02.2026 | `f7636ed` | Umbenennung: Grid Stabilization + Bugfixes |
| 26 | 16.02.2026 | `8d22fad` | Feature: Vollständiges responsives Design (Mobilansicht) |
| 27 | 16.02.2026 | `06f618e` | Feature: Reverse-SSH-Tunnel für Fernzugriff |
| 28 | 16.02.2026 | `7b2a7ec` | Regelung: Umstellung auf Anlagenleistung (später rückgängig) |
| 29 | 16.02.2026 | `61604bf` | Feature: Meter-kalibrierte Nennleistung und EMA-Glättung |
| 30 | 16.02.2026 | `5cf83df` | Bugfix: Kalibrierung ab Systemstart aktiv |
| 31 | 16.02.2026 | `f0f2700` | Regelung: Richtungsabhängiger Cooldown |
| 32 | 16.02.2026 | `ff91aa0` | Regelung: Ramp-up-Cooldown 90s→60s |
| 33 | 17.02.2026 | `62a0676` | Regelung: Zurück auf Miner-Leistung (nach Klärung) |
| 34 | 17.02.2026 | `7414396` | Sicherheit: Spannungsüberwachung und Netzausfallschutz |
| 35 | 17.02.2026 | `9cd45ac` | Dashboard: Cache-Bust v6 für neue Spannungsanzeige |
| 36 | 17.02.2026 | `c343943` | Feature: Per-Miner-Snapshots in SQLite alle 60s |
| 37 | 17.02.2026 | `2ce6794` | Feature: Schema-Migration für erweiterte Fleet-Snapshots |
| 38 | 18.02.2026 | `35a4914` | Sicherheit: Spannungsschwelle auf 100 V angepasst |
| 39 | 18.02.2026 | `f9fd762` | Feature: Historische Daten aus DB laden + 24H-Modal |

---

## Aktuelle Systemparameter (Stand: 18.02.2026)

| Parameter | Wert |
|-----------|------|
| **Systemname** | Grid Stabilization |
| **Flottengröße** | 86 Antminer S9 |
| **Aktive Miner** | ~76 (10 Hardware-Defekte) |
| **Nennleistung** | 103,2 kW |
| **Erreichbare Leistung** | ~91 kW |
| **Pro-Miner-Verbrauch** | ~1,20 kW (kalibriert) |
| **Regelgröße** | Miner-Leistung (`miners_total_power_kw`) |
| **Toleranzband** | ±5% |
| **Warmup-Phase** | 120 Sekunden |
| **Ramp-up-Cooldown** | 60 Sekunden |
| **Trim-Cooldown** | 30 Sekunden |
| **Regulierungsintervall** | 30 Sekunden |
| **Snapshot-Intervall** | 60 Sekunden |
| **Subnetz** | 192.168.95.0/24 |
| **Server-IP** | 192.168.95.6 |
| **Stromzähler** | 192.168.95.4:8044 |
| **Stromzähler-Endpunkt** | `/api/miners/get-measurement-data` |
| **Spannungsschwelle** | 100 V (Netzausfallschutz) |
| **Datenbank** | SQLite (`grid_stabilization.db`) |
| **Dashboard-Cache** | v7 |

---

## Offene Punkte

| Nr. | Thema | Status | Zuständigkeit |
|-----|-------|--------|---------------|
| 1 | ~10 nicht ansprechbare Miner | Offen | Wartungsteam vor Ort |
| 2 | Nennleistungstest (103 kW) | Blockiert durch Nr. 1 | — |
| 3 | Fernzugriff-Skript ausführen | Bereit zur Installation | Systemadministration |

---

*Dieses Dokument protokolliert alle Änderungen am System Grid Stabilization seit der Erstellung der Technischen Beschreibung v1.0 vom 24. Januar 2026. Es dient dem Auftraggeber als vollständige Nachverfolgung der durchgeführten Arbeiten.*

*Letzte Aktualisierung: 18. Februar 2026*

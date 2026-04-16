# Diagnose-Bericht Miner Fleet — 192.168.95.0/24

**Datum:** 15. April 2026  
**Erstellt von:** Grid Stabilization System  
**Fleet:** 175 Geräte im Subnet, davon 170 aktive Miner

---

## 1. KRITISCH: Rogue Miner (sofort handeln)

| IP | Problem | Leistung | Aktion |
|---|---|---|---|
| **192.168.95.144** | Hasht aktiv bei 13,4 TH/s seit 5,4 Tagen. **Nicht im Fleet Manager** (Port 80 tot, kein Web-Interface). Watchdog startet bmminer automatisch neu — remote nicht stoppbar. | **~1,2 kW** | **Physisch ausschalten** oder per SSH Watchdog deaktivieren |

> Beim Test: `quit` via CGMiner-API (Port 4028) stoppt bmminer, aber Watchdog startet ihn innerhalb von 5 Sekunden neu. Nur physisches Eingreifen hilft.

---

## 2. Miner mit defekten Hashboards (0 GH/s)

bmminer lief, aber keine Hashrate — Boards initialisieren nicht. Per CGI gestoppt, kein Watchdog.

| IP | Elapsed | Aktion |
|---|---|---|
| 192.168.95.116 | 11 min | Hashboard-Verbindungen prüfen |
| 192.168.95.184 | 75 min | Hashboard-Verbindungen prüfen |
| 192.168.95.30 | 28 min | Hashboard-Verbindungen prüfen |
| 192.168.95.38 | 77 min | Hashboard-Verbindungen prüfen |

---

## 3. Gesperrter Miner (Login fehlgeschlagen)

| IP | Problem | Aktion |
|---|---|---|
| **192.168.95.130** | Antminer (Digest Auth realm="antMiner Configuration"), aber root:root wird abgelehnt (HTTP 401). Port 4028 geschlossen (schläft). Systemuhr steht auf 1970. | Passwort zurücksetzen (Factory Reset / SD-Card Recovery) |

---

## 4. Fan-Sensor-Fehler (7 Miner)

Melden >10.000 RPM — Sensor defekt, nicht reale Drehzahl. Fan-Sensor-Kabel prüfen/ersetzen.

| IP | Letzte Temp | Fan-Wert (RPM) |
|---|---|---|
| 192.168.95.115 | 80°C | 17.700 |
| 192.168.95.114 | 90°C | 17.640 |
| 192.168.95.72 | 80°C | 17.400 |
| 192.168.95.157 | 80°C | 17.340 |
| 192.168.95.55 | 80°C | 16.860 |
| 192.168.95.98 | 80°C | 16.800 |
| 192.168.95.188 | 81°C | 16.740 |

---

## 5. Überhitzung beim letzten Betrieb (>=90°C)

Diese Miner hatten beim letzten Betrieb gefährlich hohe Temperaturen. Jetzt schlafen sie korrekt (Port 4028 geschlossen, bestätigt). Kühlung/Airflow prüfen bevor sie wieder aktiviert werden.

| IP | Letzte Temp | Fan (RPM) |
|---|---|---|
| 192.168.95.140 | 96°C | 5.820 |
| 192.168.95.150 | 95°C | 5.040 |
| 192.168.95.222 | 95°C | 5.880 |
| 192.168.95.152 | 94°C | 6.060 |
| 192.168.95.214 | 91°C | 5.880 |
| 192.168.95.111 | 90°C | 5.160 |
| 192.168.95.114 | 90°C | 17.640 (Sensor-Fehler) |

---

## 6. Keine Miner — aus Fleet entfernen

| IP | Gerät | Erkennung |
|---|---|---|
| 192.168.95.2 | Gateway / Router | HTTP 200, kein Antminer |
| 192.168.95.6 | Unser Server (Fleet Manager) | Eigene IP |
| 192.168.95.10 | Unbekanntes Web-Gerät | HTTP 200, generische HTML-Seite |
| 192.168.95.131 | Netzwerk-Gerät (Switch o.ä.) | HTTP 200, Redirect zu /nextgen/ui/ |

---

## 7. Leistungsanalyse

**Meter zeigt 9,9 kW** bei allen Minern im Schlafmodus.

| Posten | kW |
|---|---|
| 192.168.95.144 — Rogue Miner (hasht aktiv) | ~1,2 |
| 170 schlafende Miner × ~51W PSU-Standby (AC-seitig) | ~8,7 |
| **Gesamt (Meter)** | **~9,9** |

**Erklärung der 51W pro Miner:** Die Boards ziehen im Schlaf 18–28W (DC). Das APW3++ Netzteil hat bei sehr geringer Last aber nur ~50% Effizienz. Deshalb zieht jeder schlafende Miner ~50W von der Steckdose.

**Test:** Nach dem Stoppen von .144 fiel der Meter sofort von 9,9 auf 8,7 kW. Nach 5s hat der Watchdog bmminer neugestartet → zurück auf 9,9 kW.

---

## 8. Zusammenfassung

| Status | Anzahl |
|---|---|
| Miner schlafen korrekt (Port 4028 geschlossen, Subnet-Scan bestätigt) | **170** |
| Rogue Miner — hasht unkontrolliert, physisch eingreifen | **1** (.144) |
| Defekte Hashboards (0 GH/s) — jetzt gestoppt | **4** (.116, .184, .30, .38) |
| Login gesperrt — Factory Reset nötig | **1** (.130) |
| Fan-Sensor-Fehler — Verkabelung prüfen | **7** |
| Überhitzung beim letzten Betrieb (>=90°C) — Airflow prüfen | **7** |
| Nicht-Miner-Geräte — aus Fleet ausschließen | **4** (.2, .6, .10, .131) |

### Prioritäten für das On-Site Team

1. **192.168.95.144** physisch ausschalten (zieht 1,2 kW, nicht steuerbar)
2. **192.168.95.130** Factory Reset (kein Zugang)
3. Fan-Sensor-Kabel an 7 Minern prüfen
4. Hashboard-Stecker an .116, .184, .30, .38 prüfen
5. Airflow bei den 7 Überhitzungs-Minern verbessern

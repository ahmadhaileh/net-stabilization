# Grid Stabilization – Änderungsprotokoll v1.1

**Projekt:** Grid Stabilization – Mining-Flottensteuerung  
**Datum:** 18. Februar 2026  
**Bezug:** Technische Beschreibung v1.0 (24.01.2026), Änderungsprotokoll v1.0 (17.02.2026)  
**Änderungen:** 6 (Commits 34–39)

---

## Übersicht

Seit dem letzten Protokoll (v1.0, 17.02.2026) wurden folgende Erweiterungen vorgenommen:

| Bereich | Änderungen |
|---------|------------|
| Sicherheit | 2 – Netzausfallschutz über Spannungserkennung |
| Datenpersistenz | 3 – SQLite-Speicherung, Schema-Migration, historische Diagramme |
| Dashboard | 1 – Cache-Aktualisierung für Spannungsanzeige |

---

## 1. Spannungsüberwachung und Netzausfallschutz

Das System überwacht nun kontinuierlich die Netzspannung über den Stromzähler (Durchschnitt der drei Phasen L1-N, L2-N, L3-N).

**Funktionsweise:**

- Bei Abfall unter **100 V** (normal: ~230 V) wird ein Netzausfall erkannt
- Die gesamte Flotte wird sofort in den **Idle-Zustand** versetzt
- Nach Spannungsrückkehr erfolgt eine kontrollierte Wiederherstellung (3 Retry-Runden)
- Die Schwelle von 100 V erkennt zuverlässig den Ausfall von 2 oder mehr Phasen

**Dashboard:** Die aktuelle Netzspannung wird neben der Stromzähler-Leistung angezeigt. Bei Werten unter 200 V erfolgt eine farbliche Warnung.

---

## 2. Datenpersistenz und historische Anzeige

Bisher gingen alle Diagrammdaten bei Container-Neustart oder VPN-Unterbrechung verloren. Die Daten werden nun **serverseitig in der SQLite-Datenbank** gespeichert und beim Öffnen des Dashboards automatisch geladen.

**Gespeicherte Daten (alle 60 Sekunden):**

- **Per-Miner-Snapshots:** IP, Hashrate, Temperatur, Leistung, Frequenz, Status
- **Fleet-Snapshots (erweitert):** Gemessene Leistung, Anlagenleistung, Spannung, Zielleistung

**Neue API-Endpunkte:**

| Endpunkt | Beschreibung |
|----------|--------------|
| `GET /dashboard/api/fleet-snapshots?hours=24` | Fleet-Verlauf (max. 1440 Punkte) |
| `GET /dashboard/api/miner-snapshots/{ip}?hours=24` | Miner-Verlauf (max. 1440 Punkte) |

**Frontend:**

- Beim Seitenaufruf werden Fleet-Daten automatisch aus der Datenbank geladen
- Im Miner-Detail-Modal werden Per-Miner-Daten beim Öffnen geladen
- Neuer **24H-Button** im Miner-Modal für erweiterten Zeitbereich
- Aufbewahrung: 24h Snapshots, 7 Tage Befehlshistorie

**Schema-Migration:** Bestehende Datenbanken werden automatisch erweitert – kein Datenverlust.

---

## Commit-Historie

| Nr. | Datum | Commit | Beschreibung |
|-----|-------|--------|--------------|
| 34 | 17.02.2026 | `7414396` | Spannungsüberwachung und Netzausfallschutz |
| 35 | 17.02.2026 | `9cd45ac` | Cache-Bust v6 für Spannungsanzeige |
| 36 | 17.02.2026 | `c343943` | Per-Miner-Snapshots in SQLite (60s-Intervall) |
| 37 | 17.02.2026 | `2ce6794` | Schema-Migration für erweiterte Fleet-Snapshots |
| 38 | 18.02.2026 | `35a4914` | Spannungsschwelle auf 100 V angepasst |
| 39 | 18.02.2026 | `f9fd762` | Historische Daten aus DB laden + 24H-Modal |

---

*Grid Stabilization – Änderungsprotokoll v1.1 | 18. Februar 2026*

# Technische Beschreibung

## Net Stabilization – Mining-Flottensteuerung für Netzstabilisierung

**Dokumentversion:** 1.0  
**Datum:** 24. Januar 2026  
**Klassifizierung:** Technische Systemdokumentation

---

## Inhaltsverzeichnis

1. [Systemübersicht](#1-systemübersicht)
2. [Zweck und Einsatzgebiet](#2-zweck-und-einsatzgebiet)
3. [Systemarchitektur](#3-systemarchitektur)
4. [Funktionsbeschreibung](#4-funktionsbeschreibung)
5. [Technische Komponenten](#5-technische-komponenten)
6. [Schnittstellen](#6-schnittstellen)
7. [Datenmanagement](#7-datenmanagement)
8. [Betriebsanforderungen](#8-betriebsanforderungen)
9. [Sicherheitsaspekte](#9-sicherheitsaspekte)
10. [Glossar](#10-glossar)

---

## 1. Systemübersicht

### 1.1 Produktbezeichnung

**Net Stabilization** – Intelligentes Steuerungssystem für die dynamische Leistungsregelung von Mining-Hardware zur Integration in Energiemanagementsysteme (EMS).

### 1.2 Kurzbeschreibung

Das System ermöglicht die ferngesteuerte Regelung des Stromverbrauchs einer Mining-Flotte durch ein übergeordnetes Energiemanagementsystem. Durch präzise Steuerung der Leistungsaufnahme einzelner Mining-Geräte kann die Gesamtlast der Flotte dynamisch an Netzanforderungen angepasst werden.

### 1.3 Versionsinformationen

| Komponente | Version |
|------------|---------|
| Software-Version | 1.0.0 |
| API-Version | 1.0 |
| Protokoll-Version | EMS v1 |

---

## 2. Zweck und Einsatzgebiet

### 2.1 Primärer Anwendungszweck

Das System dient als Schnittstelle zwischen Energiemanagementsystemen (EMS) und Mining-Hardware, um:

- **Laststeuerung:** Dynamische Anpassung des Stromverbrauchs auf Anforderung des EMS
- **Netzstabilisierung:** Bereitstellung von regelbarer Last für Demand-Response-Programme
- **Effizienzoptimierung:** Optimale Verteilung der Leistung auf verfügbare Geräte

### 2.2 Einsatzszenarien

| Szenario | Beschreibung |
|----------|--------------|
| Lastabwurf | Reduzierung der Leistungsaufnahme bei Netzüberlastung |
| Lastaufnahme | Erhöhung des Verbrauchs bei Überangebot erneuerbarer Energien |
| Frequenzregelung | Schnelle Leistungsanpassung zur Netzfrequenzstabilisierung |
| Spitzenlastmanagement | Vermeidung von Lastspitzen durch gezielte Steuerung |

### 2.3 Zielgruppe

- Energieversorgungsunternehmen
- Netzbetreiber
- Betreiber von Mining-Anlagen
- Demand-Response-Aggregatoren

---

## 3. Systemarchitektur

### 3.1 Architekturübersicht

```
┌─────────────────────────────────────────────────────────────────┐
│                    EXTERNE SYSTEME                               │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐              ┌─────────────────┐           │
│  │  Energiemanage- │              │   Web-Dashboard │           │
│  │  mentsystem     │              │   (Monitoring)  │           │
│  │  (EMS)          │              │                 │           │
│  └────────┬────────┘              └────────┬────────┘           │
│           │ REST API                       │ REST API           │
│           │ /api/*                         │ /dashboard/api/*   │
└───────────┼────────────────────────────────┼────────────────────┘
            │                                │
            ▼                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    NET STABILIZATION SERVER                      │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    FastAPI Web Server                    │    │
│  │                    (Port 8080)                           │    │
│  └─────────────────────────────────────────────────────────┘    │
│                              │                                   │
│  ┌───────────────┬───────────┴───────────┬───────────────┐      │
│  │               │                       │               │      │
│  ▼               ▼                       ▼               ▼      │
│ ┌─────────┐ ┌─────────────┐ ┌─────────────────┐ ┌──────────┐   │
│ │ EMS API │ │ Fleet       │ │ Miner Discovery │ │ Power    │   │
│ │ Handler │ │ Manager     │ │ Service         │ │ Control  │   │
│ └─────────┘ └─────────────┘ └─────────────────┘ └──────────┘   │
│                              │                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              SQLite Datenbank (Persistenz)               │    │
│  └─────────────────────────────────────────────────────────┘    │
└───────────────────────────────────────────────────────────────────┘
            │
            │ CGMiner API (Port 4028) + Vnish Web API (Port 80)
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      MINING-HARDWARE                             │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌──────────┐       ┌──────────┐   │
│  │ Miner 1  │  │ Miner 2  │  │ Miner 3  │  ...  │ Miner N  │   │
│  │ (S9)     │  │ (S9)     │  │ (S9)     │       │ (S9)     │   │
│  └──────────┘  └──────────┘  └──────────┘       └──────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 Komponentenübersicht

| Komponente | Funktion |
|------------|----------|
| FastAPI Web Server | HTTP-Server für API-Endpunkte und Dashboard |
| EMS API Handler | Verarbeitung von EMS-Protokoll-Anfragen |
| Fleet Manager | Zentrale Steuerungslogik für die Flotte |
| Miner Discovery | Automatische Erkennung von Mining-Geräten im Netzwerk |
| Power Control | Frequenz- und Leistungssteuerung einzelner Geräte |
| SQLite Datenbank | Persistente Speicherung von Konfiguration und Historie |

---

## 4. Funktionsbeschreibung

### 4.1 Kernfunktionen

#### 4.1.1 Statusabfrage

Das System liefert auf Anfrage den aktuellen Betriebszustand:

| Parameter | Beschreibung | Einheit |
|-----------|--------------|---------|
| Verfügbarkeit | Bereitschaft für Steuerungsbefehle | Boolean |
| Betriebsstatus | Standby (1) oder Aktiv (2) | Integer |
| Nennleistung | Maximale Kapazität der Flotte | kW |
| Aktuelle Leistung | Momentaner Stromverbrauch | kW |

#### 4.1.2 Aktivierung

Startet den Mining-Betrieb bei einer definierten Zielleistung:

- Eingangsparameter: Zielleistung in kW
- Automatische Verteilung auf verfügbare Geräte
- Rückmeldung über Erfolg/Misserfolg

#### 4.1.3 Deaktivierung

Beendet den Mining-Betrieb:

- Alle Geräte werden in den Standby-Modus versetzt
- Leistungsaufnahme wird auf Minimum reduziert
- Idempotente Operation (mehrfacher Aufruf unproblematisch)

### 4.2 Leistungssteuerungsmodi

Das System unterstützt zwei Betriebsmodi für die Leistungsregelung:

#### 4.2.1 On/Off-Modus (Standard)

| Eigenschaft | Wert |
|-------------|------|
| Granularität | Pro Gerät (~1.460 W Stufen) |
| Reaktionszeit | 5-10 Sekunden |
| Zuverlässigkeit | Hoch |
| Anwendungsfall | Standard-Laststeuerung |

#### 4.2.2 Frequenz-Modus

| Eigenschaft | Wert |
|-------------|------|
| Granularität | ~50 W Stufen |
| Frequenzbereich | 350-800 MHz |
| Leistungsbereich | 660-1.900 W pro Gerät |
| Reaktionszeit | 10-30 Sekunden |
| Anwendungsfall | Feinregelung |

### 4.3 Automatische Geräteerkennung

Das System erkennt Mining-Geräte automatisch im konfigurierten Netzwerksegment:

- Netzwerk-Scan via CGMiner-API (Port 4028)
- Identifikation von Gerätetyp und Firmware
- Kontinuierliche Überwachung des Gerätestatus
- Automatische Registrierung neuer Geräte

---

## 5. Technische Komponenten

### 5.1 Software-Stack

| Komponente | Technologie | Version |
|------------|-------------|---------|
| Programmiersprache | Python | 3.11+ |
| Web-Framework | FastAPI | 0.109+ |
| Datenbank | SQLite | 3.x |
| ORM | SQLAlchemy | 2.0+ |
| HTTP-Client | HTTPX | 0.26+ |

### 5.2 Unterstützte Hardware

| Gerät | Firmware | Leistungsbereich |
|-------|----------|------------------|
| Antminer S9 | Vnish 3.9.x | 660-1.900 W |

### 5.3 Leistungskurve (Antminer S9)

| Frequenz (MHz) | Leistung (W) | Hashrate (TH/s) |
|----------------|--------------|-----------------|
| 350 | 660 | 7,5 |
| 450 | 950 | 11,0 |
| 525 | 1.145 | 12,0 |
| 575 | 1.285 | 13,0 |
| 650 | 1.460 | 13,7 |
| 750 | 1.850 | 16,0 |
| 800 | 1.900 | 17,0 |

---

## 6. Schnittstellen

### 6.1 EMS-Protokoll-Schnittstelle

Primäre Schnittstelle für die Integration mit Energiemanagementsystemen.

#### 6.1.1 Endpunkte

| Methode | Endpunkt | Funktion |
|---------|----------|----------|
| GET | `/api/status` | Statusabfrage |
| POST | `/api/activate` | Aktivierung mit Zielleistung |
| POST | `/api/deactivate` | Deaktivierung |

#### 6.1.2 Datenformate

**Statusantwort:**
```json
{
  "isAvailableForDispatch": true,
  "runningStatus": 2,
  "ratedPowerInKw": 2.92,
  "activePowerInKw": 2.45
}
```

**Aktivierungsanfrage:**
```json
{
  "activationPowerInKw": 2.0
}
```

**Befehlsantwort:**
```json
{
  "accepted": true,
  "message": "Fleet activated successfully"
}
```

#### 6.1.3 Zeitanforderungen

| Operation | Maximale Antwortzeit |
|-----------|---------------------|
| Statusabfrage | ≤ 1 Sekunde |
| Aktivierung | ≤ 2 Sekunden |
| Deaktivierung | ≤ 2 Sekunden |

### 6.2 Dashboard-Schnittstelle

Interne API für Monitoring und manuelle Steuerung.

| Endpunkt | Funktion |
|----------|----------|
| `/dashboard/api/status` | Erweiterte Statusinformationen |
| `/dashboard/api/miners` | Liste aller Geräte |
| `/dashboard/api/override` | Manuelle Übersteuerung |
| `/dashboard/api/power-mode` | Steuerungsmodus wechseln |
| `/dashboard/api/history` | Befehlshistorie |

### 6.3 Miner-Kommunikation

| Protokoll | Port | Funktion |
|-----------|------|----------|
| CGMiner API | 4028 | Status, Hashrate, Temperatur |
| Vnish Web API | 80 | Konfiguration, Frequenzsteuerung |

---

## 7. Datenmanagement

### 7.1 Datenbank-Schema

Das System verwendet eine SQLite-Datenbank mit folgenden Haupttabellen:

| Tabelle | Inhalt |
|---------|--------|
| `miner_records` | Registrierte Geräte |
| `miner_snapshots` | Historische Gerätedaten |
| `fleet_snapshots` | Historische Flottendaten |
| `command_history` | Befehlsprotokoll |
| `dashboard_settings` | Konfigurationseinstellungen |

### 7.2 Datenaufbewahrung

| Datentyp | Aufbewahrungsdauer | Intervall |
|----------|-------------------|-----------|
| Geräte-Snapshots | 24 Stunden | 60 Sekunden |
| Flotten-Snapshots | 24 Stunden | 60 Sekunden |
| Befehlshistorie | Unbegrenzt | Bei Ereignis |

### 7.3 Datenmenge (Schätzung)

Bei 600 Geräten:
- ~14.400 Geräte-Snapshots/Tag
- ~1.440 Flotten-Snapshots/Tag
- Automatische Bereinigung nach 24 Stunden

---

## 8. Betriebsanforderungen

### 8.1 Systemanforderungen

#### Server

| Anforderung | Minimum | Empfohlen |
|-------------|---------|-----------|
| CPU | 2 Kerne | 4 Kerne |
| RAM | 2 GB | 4 GB |
| Speicher | 10 GB | 50 GB |
| Betriebssystem | Linux, macOS, Windows | Linux |

#### Netzwerk

| Anforderung | Wert |
|-------------|------|
| Bandbreite | 10 Mbit/s |
| Latenz zu Minern | < 100 ms |
| Ports | 8080 (Server), 4028 (Miner API), 80 (Miner Web) |

### 8.2 Konfigurationsparameter

| Parameter | Standardwert | Beschreibung |
|-----------|--------------|--------------|
| `HOST_PORT` | 8080 | Server-Port |
| `MINER_NETWORK_CIDR` | 192.168.1.0/24 | Netzwerk für Gerätesuche |
| `POLL_INTERVAL_SECONDS` | 5 | Abfrageintervall |
| `SNAPSHOT_INTERVAL_SECONDS` | 60 | Speicherintervall |
| `POWER_CONTROL_MODE` | on_off | Steuerungsmodus |

### 8.3 Skalierung

| Flottengröße | Ressourcenbedarf | Hinweise |
|--------------|------------------|----------|
| 1-50 Geräte | Minimal | Standardkonfiguration |
| 50-200 Geräte | Moderat | Erhöhtes Polling-Intervall empfohlen |
| 200-1000 Geräte | Hoch | Dedizierter Server, SSD empfohlen |

---

## 9. Sicherheitsaspekte

### 9.1 Netzwerksicherheit

| Maßnahme | Beschreibung |
|----------|--------------|
| Netzwerksegmentierung | Mining-Geräte in separatem VLAN |
| Firewall | Zugriff auf API-Ports beschränken |
| VPN/Tunnel | Für externen Zugriff verschlüsselte Verbindung |

### 9.2 Authentifizierung

| Komponente | Methode |
|------------|---------|
| EMS-API | Netzwerkbasiert (keine Authentifizierung) |
| Dashboard-API | Netzwerkbasiert (keine Authentifizierung) |
| Miner-Zugriff | HTTP Digest Authentication |

### 9.3 Empfohlene Sicherheitsmaßnahmen

1. **Netzwerkisolation:** System nur im internen Netzwerk betreiben
2. **Reverse Proxy:** Nginx/Traefik mit TLS für externen Zugriff
3. **Zugriffskontrolle:** IP-Whitelist für EMS-Verbindungen
4. **Monitoring:** Protokollierung aller Steuerungsbefehle
5. **Backup:** Regelmäßige Sicherung der Datenbank

---

## 10. Glossar

| Begriff | Definition |
|---------|------------|
| **EMS** | Energy Management System – Übergeordnetes System zur Energiesteuerung |
| **Demand Response** | Anpassung des Stromverbrauchs auf Signal des Netzbetreibers |
| **CGMiner** | Open-Source Mining-Software, stellt API bereit |
| **Vnish** | Custom-Firmware für Antminer-Geräte mit erweiterten Funktionen |
| **Hashrate** | Rechenleistung eines Mining-Geräts (TH/s = Terahash pro Sekunde) |
| **Standby** | Betriebszustand ohne aktives Mining (minimale Leistungsaufnahme) |
| **Swing Miner** | Gerät mit variabler Frequenz für Feinregelung der Gesamtleistung |
| **Fleet** | Gesamtheit aller gesteuerten Mining-Geräte |

---

## Anhang

### A. Kontaktinformationen

Für technische Rückfragen zur Systemintegration wenden Sie sich bitte an den Systembetreiber.

### B. Änderungshistorie

| Version | Datum | Änderungen |
|---------|-------|------------|
| 1.0 | 24.01.2026 | Erstversion |

---

*Dieses Dokument dient der technischen Beschreibung des Systems Net Stabilization und ist für die Registrierung und Dokumentation beim Auftraggeber bestimmt.*

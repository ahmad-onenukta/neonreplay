# NeonReplay – GTA 6 Live-Clipping

Live-Streams → Ringpuffer → Highlight-Trigger (Chat + Audio) → 9:16-Clip mit Hook + Untertiteln (EN/DE/UR) → Dashboard-Queue → Metricool → TikTok/IG/YouTube.

## Installation (Hetzner, Ubuntu 24.04)
```bash
curl -fsSL https://raw.githubusercontent.com/ahmad-onenukta/neonreplay/main/install.sh | REPO=https://github.com/ahmad-onenukta/neonreplay.git bash
```

## Konfiguration
| Datei | Inhalt |
|---|---|
| `.env` | API-Keys (Anthropic, Metricool), Dashboard-Login |
| `config.yaml` | Streamer (nur Kampagnen mit Erlaubnis), Trigger-Schwellen, Sprachen, Metricool-Brands (blogId je Sprache) |

Änderungen übernehmen: `cd /opt/neonreplay && docker compose restart`

## Posting-Modus
- `queue`: Clips landen im Dashboard und gehen nach **Freigeben** raus (empfohlen für die ersten Tage)
- `auto`: alles geht automatisch raus (Rate-Limits: max. 5 pro Account/Tag, 30 Min Abstand)

## Logs
`docker compose logs -f worker`

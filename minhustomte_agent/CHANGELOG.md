## 3.1.5

- Fixar Home Assistant WebSocket-auth för `camera/stream`.
- Förbättrar HLS/FFmpeg-diagnostik för kamerarelay.

## 3.1.4
- Kameror: fallback från `camera_proxy` till Home Assistants riktiga HLS-livevideo när stillbilds-API:t ger fel.
- Kamerafel och vald streammetod rapporteras tillbaka till MinHustomte.
- FFmpeg används lokalt i add-onen för att relay:a HLS som JPEG-rutor över den befintliga krypterade hubbkanalen.

## 3.1.3
- Dashboard Design V2-stöd.
- Kan läsa och sätta MinHustomte som systemets standarddashboard via Home Assistants frontend storage API.
- Återställer Overview innan en standard-Minhustomte-dashboard tas bort.

# Changelog

## 3.1.1
- Stänger av WebSocket-komprimering mot MinHustomte för kompatibilitet med websocket-client.
- Stabilare exponentiell reconnect-backoff; återställs först efter en stabil anslutning.
- Tydligare HTTP-fel från entity sync.

## 3.1.0
- Stöd för MinHustomte-hanterad Lovelace-dashboard via Home Assistants WebSocket API.
- Skapar endast en separat `minhustomte-home` storage-dashboard och rör aldrig Overview.
- Säker status, publicering och borttagning med lokal ägarstate i `/data`.


## 3.0.0
- Ny generell Hub Agent.
- Supabase helt borttaget.
- Permanent utgående WebSocket.
- Generell Home Assistant RPC.
- Entity discovery/sync.
- Kamera JPEG-stream över binär WebSocket.
- Persistent hub-token efter AUTH-CODE-parkoppling.

## 3.1.2
- Flyttar `Sec-WebSocket-Extensions`-headern till `WebSocketApp(...)`; `run_forever()` stöder inte `header=` i den installerade websocket-client-versionen.
- Behåller reconnect-backoff och tydliga entity-sync-fel från 3.1.1.

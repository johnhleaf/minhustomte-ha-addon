# Changelog

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

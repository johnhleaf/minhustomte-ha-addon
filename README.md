# MinHustomte Portal Integration

Denna add-on ansluter din HomeAssistant-installation till MinHustomte portalen.

## Installation

1. Kopiera denna mapp till din HomeAssistant add-ons katalog
2. Starta om HomeAssistant
3. Gå till Inställningar → Add-ons → Lokala add-ons
4. Installera "MinHustomte Portal Integration"
5. **Öppna konfigurationen** och ange din auth-kod från portalen
6. Starta add-on

Add-on kommer automatiskt att:
- Skapa anslutning till portalen
- Skapa ett admin-konto för kommunikation
- Göra dagliga backuper av din konfiguration
- Installera MinHustomte-temat

## Hämta Auth-kod

1. Logga in på MinHustomte portalen (portal.minhustomte.se)
2. Lägg till en ny hub via "Lägg till hub" knappen
3. Kopiera auth-koden som genereras (format: HST-XXXXXX)
4. Klistra in auth-koden i add-on konfigurationen

## Tema

Add-on inkluderar ett anpassat tema som matchar MinHustomte-portalen.
För att aktivera temat:

1. Gå till **Inställningar** → **Anpassa** i HomeAssistant
2. Välj **MinHustomte** under "Tema"
3. Temat finns även i mörkt läge: **MinHustomte Dark**

## Konfiguration

- **auth_code**: Din unika autentiseringskod från portalen (krävs)
- **api_endpoint**: Portal API endpoint (standard: https://qqmxykhzatbdsabsarrd.supabase.co)
- **backup_enabled**: Aktivera dagliga backuper (standard: true)
- **backup_schedule**: Cron-schema för backuper (standard: 03:00 varje dag)

## Support

Vid problem, kontakta MinHustomte support.

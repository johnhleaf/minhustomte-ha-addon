#!/usr/bin/env python3
import os
import json
import time
import requests
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MinHustomteIntegration:
    def __init__(self):
        self.load_config()
        self.authenticated = False
        self.cabin_id = None
        self.ha_username = None
        self.ha_password = None

    # ... (behåll load_config, authenticate, create_ha_user, install_theme, backup_config som de är) ...

    def get_cameras_from_ha(self):
    token = os.environ.get('HASSIO_TOKEN')
    if not token:
        logger.error("HASSIO_TOKEN not found in environment")
        return []

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }

    # Försök flera kända vägar (en av dem fungerar nästan alltid)
    urls = [
        "http://supervisor/core/api/states",
        "http://homeassistant:8123/api/states",
        "http://homeassistant/local/api/states",
    ]

    for url in urls:
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                # Success!
                break
        except:
            continue
    else:
        logger.error("All attempts to reach Home Assistant API failed")
        return []
            
            cameras = []
            for entity in response.json():
                if entity.get('entity_id', '').startswith('camera.'):
                    attrs = entity.get('attributes', {})
                    cameras.append({
                        'entity_id': entity['entity_id'],
                        'name': attrs.get('friendly_name', entity['entity_id']),
                        'status': 'online' if entity.get('state') != 'unavailable' else 'offline',
                        'manufacturer': attrs.get('brand', 'ONVIF'),
                        'model': attrs.get('model'),
                        'supports_stream': True
                    })
            
            logger.info(f"Found {len(cameras)} cameras in HomeAssistant")
            return cameras
            
        except Exception as e:
            logger.error(f"Error fetching cameras: {str(e)}")
            return []

    def sync_cameras(self):
        """Sync cameras to MinHustomte portal"""
        if not self.authenticated:
            logger.warning("Not authenticated, skipping camera sync")
            return
        
        cameras = self.get_cameras_from_ha()
        
        if not cameras:
            logger.info("No cameras found to sync")
            return
        
        try:
            response = requests.post(
                f"{self.api_endpoint}/functions/v1/camera-sync",
                json={
                    'cabin_id': self.cabin_id,
                    'ha_username': self.ha_username,
                    'ha_password': self.ha_password,
                    'cameras': cameras
                },
                headers={'Content-Type': 'application/json'}
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"Camera sync successful: {result.get('synced', 0)} cameras synced")
            else:
                logger.error(f"Camera sync failed: {response.text}")
                
        except Exception as e:
            logger.error(f"Camera sync error: {str(e)}")

    def run(self):
        """Main run loop"""
        logger.info("MinHustomte Portal Integration starting...")

        # Try to load existing credentials
        if os.path.exists('/data/credentials.json'):
            try:
                with open('/data/credentials.json', 'r') as f:
                    creds = json.load(f)
                    self.cabin_id = creds['cabin_id']
                    self.ha_username = creds['ha_username']
                    self.ha_password = creds['ha_password']
                    self.authenticated = True
                    logger.info("Loaded existing credentials")
            except Exception as e:
                logger.warning(f"Could not load credentials: {str(e)}")

        # Authenticate if not already
        if not self.authenticated:
            if not self.authenticate():
                logger.error("Initial authentication failed. Waiting for retry...")
                time.sleep(60)
                return self.run()

        # Initial backup and camera sync
        if self.backup_enabled:
            self.backup_config()
        
        # Sync cameras on startup
        self.sync_cameras()

        # Main loop - backup and sync every 24 hours
        while True:
            try:
                time.sleep(86400)  # 24 hours

                if self.backup_enabled:
                    self.backup_config()
                
                # Sync cameras daily
                self.sync_cameras()

            except KeyboardInterrupt:
                logger.info("Shutting down...")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {str(e)}")
                time.sleep(60)

if __name__ == '__main__':
    integration = MinHustomteIntegration()
    integration.run()

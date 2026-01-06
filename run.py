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
        self.ha_token = None

    def load_config(self):
        """Load add-on configuration"""
        with open('/data/options.json', 'r') as f:
            config = json.load(f)

        self.auth_code = config.get('auth_code', '')
        self.api_endpoint = config.get('api_endpoint', 'https://qqmxykhzatbdsabsarrd.supabase.co')
        self.backup_enabled = config.get('backup_enabled', True)
        self.backup_schedule = config.get('backup_schedule', '0 3 * * *')

        logger.info(f"Configuration loaded. API endpoint: {self.api_endpoint}")

    def authenticate(self):
        """Authenticate with MinHustomte portal"""
        if not self.auth_code:
            logger.error("No auth code provided!")
            return False

        logger.info("Authenticating with portal...")

        try:
            response = requests.post(
                f"{self.api_endpoint}/functions/v1/raspberry-auth",
                json={
                    "auth_code": self.auth_code,
                    "device_info": {
                        "model": "Raspberry Pi",
                        "ha_version": os.environ.get('SUPERVISOR_VERSION', 'unknown')
                    }
                },
                headers={'Content-Type': 'application/json'}
            )

            if response.status_code == 200:
                data = response.json()
                self.cabin_id = data['cabin_id']
                self.ha_username = data['ha_username']
                self.ha_password = data['ha_password']
                self.authenticated = True

                # Save credentials
                with open('/data/credentials.json', 'w') as f:
                    json.dump({
                        'cabin_id': self.cabin_id,
                        'ha_username': self.ha_username,
                        'ha_password': self.ha_password
                    }, f)

                logger.info(f"Authentication successful! Cabin ID: {self.cabin_id}")

                # Create HomeAssistant admin user
                self.create_ha_user()

                return True
            else:
                logger.error(f"Authentication failed: {response.text}")
                return False

        except Exception as e:
            logger.error(f"Authentication error: {str(e)}")
            return False

    def create_ha_user(self):
        """Create admin user in HomeAssistant"""
        logger.info("Creating HomeAssistant admin user...")

        try:
            supervisor_token = os.environ.get('SUPERVISOR_TOKEN')

            # Create user via supervisor API
            response = requests.post(
                'http://supervisor/core/api/onboarding/users',
                json={
                    'username': self.ha_username,
                    'password': self.ha_password,
                    'name': 'MinHustomte Portal'
                },
                headers={
                    'Authorization': f'Bearer {supervisor_token}',
                    'Content-Type': 'application/json'
                }
            )

            if response.status_code in [200, 201]:
                logger.info("HomeAssistant user created successfully")
                # Install theme after user creation
                self.install_theme()
            else:
                logger.warning(f"User creation response: {response.status_code}")

        except Exception as e:
            logger.error(f"Error creating HA user: {str(e)}")

    def install_theme(self):
        """Install MinHustomte theme to HomeAssistant"""
        logger.info("Installing MinHustomte theme...")

        try:
            import shutil

            # Copy theme file to HomeAssistant themes directory
            themes_dir = '/config/themes'
            if not os.path.exists(themes_dir):
                os.makedirs(themes_dir)

            addon_themes_dir = '/themes'
            if os.path.exists(addon_themes_dir):
                for theme_file in os.listdir(addon_themes_dir):
                    src = os.path.join(addon_themes_dir, theme_file)
                    dst = os.path.join(themes_dir, theme_file)
                    shutil.copy2(src, dst)
                    logger.info(f"Installed theme: {theme_file}")

            # Reload themes via API
            supervisor_token = os.environ.get('SUPERVISOR_TOKEN')
            response = requests.post(
                'http://supervisor/core/api/services/frontend/reload_themes',
                headers={
                    'Authorization': f'Bearer {supervisor_token}',
                    'Content-Type': 'application/json'
                },
                json={}
            )

            if response.status_code == 200:
                logger.info("Themes reloaded successfully")
            else:
                logger.warning(f"Theme reload response: {response.status_code}")

        except Exception as e:
            logger.error(f"Error installing theme: {str(e)}")

    def get_ha_token(self):
        """Get or refresh HA long-lived access token"""
        if self.ha_token:
            return self.ha_token
        
        # Use supervisor token for API calls
        self.ha_token = os.environ.get('SUPERVISOR_TOKEN')
        return self.ha_token

    def sync_cameras(self):
        """Sync camera entities to portal"""
        if not self.authenticated:
            return

        logger.info("Syncing cameras...")

        try:
            token = self.get_ha_token()
            
            # Get all camera entities from HA
            response = requests.get(
                'http://supervisor/core/api/states',
                headers={'Authorization': f'Bearer {token}'}
            )

            if response.status_code != 200:
                logger.error(f"Failed to get states: {response.status_code}")
                return

            states = response.json()
            cameras = []

            for entity in states:
                entity_id = entity.get('entity_id', '')
                if entity_id.startswith('camera.'):
                    attrs = entity.get('attributes', {})
                    cameras.append({
                        'entity_id': entity_id,
                        'name': attrs.get('friendly_name', entity_id),
                        'manufacturer': attrs.get('manufacturer'),
                        'model': attrs.get('model'),
                        'status': entity.get('state', 'unknown'),
                        'supports_stream': attrs.get('frontend_stream_type') is not None
                    })

            if cameras:
                logger.info(f"Found {len(cameras)} cameras, syncing to portal...")
                
                sync_response = requests.post(
                    f"{self.api_endpoint}/functions/v1/camera-sync",
                    json={
                        'cabin_id': self.cabin_id,
                        'ha_username': self.ha_username,
                        'ha_password': self.ha_password,
                        'cameras': cameras
                    },
                    headers={'Content-Type': 'application/json'}
                )

                if sync_response.status_code == 200:
                    logger.info("Cameras synced successfully")
                else:
                    logger.error(f"Camera sync failed: {sync_response.text}")
            else:
                logger.info("No cameras found")

        except Exception as e:
            logger.error(f"Camera sync error: {str(e)}")

    def sync_electricity(self):
        """Sync electricity sensor data to portal"""
        if not self.authenticated:
            return

        logger.info("Syncing electricity data...")

        try:
            token = self.get_ha_token()
            
            # Get all sensor entities from HA
            response = requests.get(
                'http://supervisor/core/api/states',
                headers={'Authorization': f'Bearer {token}'}
            )

            if response.status_code != 200:
                logger.error(f"Failed to get states: {response.status_code}")
                return

            states = response.json()
            electricity_data = {
                'current_power': None,
                'today_usage': None,
                'month_usage': None,
                'total_import': None,
                'total_export': None,
                'voltage': None,
                'current_amps': None,
                'power_factor': None,
                'phase_l1_power': None,
                'phase_l2_power': None,
                'phase_l3_power': None,
                'phase_l1_voltage': None,
                'phase_l2_voltage': None,
                'phase_l3_voltage': None,
                'phase_l1_current': None,
                'phase_l2_current': None,
                'phase_l3_current': None
            }

            # Keywords to identify electricity sensors
            power_keywords = ['power', 'watt', 'energy', 'electricity', 'consumption']
            
            for entity in states:
                entity_id = entity.get('entity_id', '').lower()
                state = entity.get('state')
                attrs = entity.get('attributes', {})
                unit = attrs.get('unit_of_measurement', '').lower()
                
                if not entity_id.startswith('sensor.'):
                    continue
                    
                # Try to parse numeric value
                try:
                    value = float(state)
                except (ValueError, TypeError):
                    continue

                # Match sensors based on entity_id patterns and units
                if any(kw in entity_id for kw in power_keywords):
                    if unit in ['w', 'kw']:
                        if unit == 'kw':
                            value = value * 1000
                        if electricity_data['current_power'] is None:
                            electricity_data['current_power'] = value
                            logger.info(f"Found power sensor: {entity_id} = {value}W")
                    
                    elif unit in ['kwh', 'wh']:
                        if 'today' in entity_id or 'daily' in entity_id:
                            electricity_data['today_usage'] = value
                        elif 'month' in entity_id:
                            electricity_data['month_usage'] = value
                        elif 'import' in entity_id or 'total' in entity_id:
                            electricity_data['total_import'] = value
                        elif 'export' in entity_id:
                            electricity_data['total_export'] = value

                # Voltage sensors
                if unit == 'v' or 'voltage' in entity_id:
                    try:
                        value = float(state)
                        if 'l1' in entity_id or 'phase_1' in entity_id:
                            electricity_data['phase_l1_voltage'] = value
                        elif 'l2' in entity_id or 'phase_2' in entity_id:
                            electricity_data['phase_l2_voltage'] = value
                        elif 'l3' in entity_id or 'phase_3' in entity_id:
                            electricity_data['phase_l3_voltage'] = value
                        elif electricity_data['voltage'] is None:
                            electricity_data['voltage'] = value
                    except (ValueError, TypeError):
                        pass

                # Current sensors
                if unit == 'a' or 'current' in entity_id:
                    try:
                        value = float(state)
                        if 'l1' in entity_id or 'phase_1' in entity_id:
                            electricity_data['phase_l1_current'] = value
                        elif 'l2' in entity_id or 'phase_2' in entity_id:
                            electricity_data['phase_l2_current'] = value
                        elif 'l3' in entity_id or 'phase_3' in entity_id:
                            electricity_data['phase_l3_current'] = value
                        elif electricity_data['current_amps'] is None:
                            electricity_data['current_amps'] = value
                    except (ValueError, TypeError):
                        pass

            # Only sync if we have some data
            if electricity_data['current_power'] is not None:
                logger.info(f"Syncing electricity data: {electricity_data['current_power']}W")
                
                sync_response = requests.post(
                    f"{self.api_endpoint}/functions/v1/electricity-sync",
                    json={
                        'cabin_id': self.cabin_id,
                        'ha_username': self.ha_username,
                        'ha_password': self.ha_password,
                        'electricity_data': electricity_data
                    },
                    headers={'Content-Type': 'application/json'}
                )

                if sync_response.status_code == 200:
                    logger.info("Electricity data synced successfully")
                else:
                    logger.error(f"Electricity sync failed: {sync_response.text}")
            else:
                logger.info("No electricity sensors found")

        except Exception as e:
            logger.error(f"Electricity sync error: {str(e)}")

    def backup_config(self):
        """Backup HomeAssistant configuration"""
        if not self.authenticated:
            logger.warning("Not authenticated, skipping backup")
            return

        logger.info("Creating configuration backup...")

        try:
            supervisor_token = os.environ.get('SUPERVISOR_TOKEN')

            # Get configuration from supervisor
            response = requests.get(
                'http://supervisor/core/api/config',
                headers={'Authorization': f'Bearer {supervisor_token}'}
            )

            if response.status_code == 200:
                config_data = response.json()

                # Send backup to portal
                backup_response = requests.post(
                    f"{self.api_endpoint}/functions/v1/raspberry-backup",
                    json={
                        'cabin_id': self.cabin_id,
                        'ha_username': self.ha_username,
                        'ha_password': self.ha_password,
                        'backup_data': {
                            'config': config_data,
                            'version': os.environ.get('SUPERVISOR_VERSION', 'unknown'),
                            'timestamp': datetime.now().isoformat()
                        }
                    },
                    headers={'Content-Type': 'application/json'}
                )

                if backup_response.status_code == 200:
                    logger.info("Backup uploaded successfully")
                else:
                    logger.error(f"Backup upload failed: {backup_response.text}")

        except Exception as e:
            logger.error(f"Backup error: {str(e)}")

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

        # Initial sync
        self.sync_cameras()
        self.sync_electricity()

        # Initial backup
        if self.backup_enabled:
            self.backup_config()

        # Main loop
        last_backup = time.time()
        last_camera_sync = time.time()
        last_electricity_sync = time.time()

        while True:
            try:
                time.sleep(30)  # Check every 30 seconds
                current_time = time.time()

                # Sync electricity every 30 seconds
                if current_time - last_electricity_sync >= 30:
                    self.sync_electricity()
                    last_electricity_sync = current_time

                # Sync cameras every 5 minutes
                if current_time - last_camera_sync >= 300:
                    self.sync_cameras()
                    last_camera_sync = current_time

                # Backup every 24 hours
                if self.backup_enabled and current_time - last_backup >= 86400:
                    self.backup_config()
                    last_backup = current_time

            except KeyboardInterrupt:
                logger.info("Shutting down...")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {str(e)}")
                time.sleep(60)

if __name__ == '__main__':
    integration = MinHustomteIntegration()
    integration.run()

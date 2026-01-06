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

    def sync_electricity(self):
        """Sync electricity sensor data to portal using device_class detection"""
        if not self.authenticated:
            logger.warning("Not authenticated, skipping electricity sync")
            return

        logger.info("Syncing electricity data...")

        try:
            supervisor_token = os.environ.get('SUPERVISOR_TOKEN')

            # Get all states from Home Assistant
            response = requests.get(
                'http://supervisor/core/api/states',
                headers={'Authorization': f'Bearer {supervisor_token}'}
            )

            if response.status_code != 200:
                logger.error(f"Failed to get states: {response.status_code}")
                return

            states = response.json()
            electricity_data = {
                'current_power': None,
                'voltage': None,
                'current_amps': None,
                'today_usage': None,
                'month_usage': None,
                'total_import': None,
                'total_export': None,
                'power_factor': None,
                'phase_l1_power': None,
                'phase_l1_voltage': None,
                'phase_l1_current': None,
                'phase_l2_power': None,
                'phase_l2_voltage': None,
                'phase_l2_current': None,
                'phase_l3_power': None,
                'phase_l3_voltage': None,
                'phase_l3_current': None
            }

            # Collect sensors by device_class
            power_sensors = []
            energy_sensors = []
            voltage_sensors = []
            current_sensors = []

            for state in states:
                entity_id = state.get('entity_id', '')
                if not entity_id.startswith('sensor.'):
                    continue

                attrs = state.get('attributes', {})
                device_class = attrs.get('device_class', '')
                unit = attrs.get('unit_of_measurement', '')
                state_value = state.get('state', '')

                # Skip unavailable/unknown states
                if state_value in ['unavailable', 'unknown', 'none', '']:
                    continue

                try:
                    value = float(state_value)
                except (ValueError, TypeError):
                    continue

                # Categorize by device_class
                if device_class == 'power' or unit in ['W', 'kW']:
                    power_sensors.append({
                        'entity_id': entity_id,
                        'value': value if unit != 'kW' else value * 1000,
                        'attrs': attrs
                    })
                elif device_class == 'energy' or unit in ['kWh', 'Wh']:
                    energy_sensors.append({
                        'entity_id': entity_id,
                        'value': value if unit != 'Wh' else value / 1000,
                        'attrs': attrs
                    })
                elif device_class == 'voltage' or unit == 'V':
                    voltage_sensors.append({
                        'entity_id': entity_id,
                        'value': value,
                        'attrs': attrs
                    })
                elif device_class == 'current' or unit == 'A':
                    current_sensors.append({
                        'entity_id': entity_id,
                        'value': value,
                        'attrs': attrs
                    })
                elif device_class == 'power_factor':
                    electricity_data['power_factor'] = value

            logger.info(f"Found sensors - Power: {len(power_sensors)}, Energy: {len(energy_sensors)}, Voltage: {len(voltage_sensors)}, Current: {len(current_sensors)}")

            # Find main power sensor (prioritize total/main sensors)
            if power_sensors:
                # Sort by priority: prefer sensors with 'total', 'power', 'active' in name
                def power_priority(s):
                    eid = s['entity_id'].lower()
                    if 'total' in eid and 'power' in eid:
                        return 0
                    if 'active_power' in eid:
                        return 1
                    if 'power' in eid and ('import' in eid or 'consumption' in eid):
                        return 2
                    if 'power' in eid:
                        return 3
                    return 4

                power_sensors.sort(key=power_priority)
                electricity_data['current_power'] = power_sensors[0]['value']
                logger.info(f"Using power sensor: {power_sensors[0]['entity_id']} = {power_sensors[0]['value']}W")

                # Look for phase-specific power sensors
                for sensor in power_sensors:
                    eid = sensor['entity_id'].lower()
                    if 'l1' in eid or 'phase_1' in eid or '_1_power' in eid:
                        electricity_data['phase_l1_power'] = sensor['value']
                    elif 'l2' in eid or 'phase_2' in eid or '_2_power' in eid:
                        electricity_data['phase_l2_power'] = sensor['value']
                    elif 'l3' in eid or 'phase_3' in eid or '_3_power' in eid:
                        electricity_data['phase_l3_power'] = sensor['value']

            # Find energy sensors (import/export)
            if energy_sensors:
                for sensor in energy_sensors:
                    eid = sensor['entity_id'].lower()
                    # Total import/consumption
                    if ('total' in eid or 'energy' in eid) and ('import' in eid or 'consumption' in eid):
                        electricity_data['total_import'] = sensor['value']
                        logger.info(f"Using import sensor: {sensor['entity_id']} = {sensor['value']}kWh")
                    # Total export
                    elif ('total' in eid or 'energy' in eid) and 'export' in eid:
                        electricity_data['total_export'] = sensor['value']
                        logger.info(f"Using export sensor: {sensor['entity_id']} = {sensor['value']}kWh")
                    # Today's usage
                    elif 'today' in eid or 'daily' in eid:
                        electricity_data['today_usage'] = sensor['value']
                    # Monthly usage
                    elif 'month' in eid:
                        electricity_data['month_usage'] = sensor['value']

                # If no specific import found, use first energy sensor as total
                if electricity_data['total_import'] is None and energy_sensors:
                    # Prefer sensors without 'export' in name
                    for sensor in energy_sensors:
                        if 'export' not in sensor['entity_id'].lower():
                            electricity_data['total_import'] = sensor['value']
                            logger.info(f"Using fallback import sensor: {sensor['entity_id']} = {sensor['value']}kWh")
                            break

            # Find voltage sensors
            if voltage_sensors:
                # Prefer main/total voltage
                for sensor in voltage_sensors:
                    eid = sensor['entity_id'].lower()
                    if electricity_data['voltage'] is None and ('total' in eid or 'main' in eid or 'voltage' in eid):
                        electricity_data['voltage'] = sensor['value']
                    if 'l1' in eid or 'phase_1' in eid:
                        electricity_data['phase_l1_voltage'] = sensor['value']
                    elif 'l2' in eid or 'phase_2' in eid:
                        electricity_data['phase_l2_voltage'] = sensor['value']
                    elif 'l3' in eid or 'phase_3' in eid:
                        electricity_data['phase_l3_voltage'] = sensor['value']

                # Use first voltage as main if not set
                if electricity_data['voltage'] is None:
                    electricity_data['voltage'] = voltage_sensors[0]['value']

            # Find current sensors
            if current_sensors:
                for sensor in current_sensors:
                    eid = sensor['entity_id'].lower()
                    if electricity_data['current_amps'] is None and ('total' in eid or 'main' in eid or 'current' in eid):
                        electricity_data['current_amps'] = sensor['value']
                    if 'l1' in eid or 'phase_1' in eid:
                        electricity_data['phase_l1_current'] = sensor['value']
                    elif 'l2' in eid or 'phase_2' in eid:
                        electricity_data['phase_l2_current'] = sensor['value']
                    elif 'l3' in eid or 'phase_3' in eid:
                        electricity_data['phase_l3_current'] = sensor['value']

                # Use first current as main if not set
                if electricity_data['current_amps'] is None:
                    electricity_data['current_amps'] = current_sensors[0]['value']

            # Only send if we have at least power or energy data
            if electricity_data['current_power'] is not None or electricity_data['total_import'] is not None:
                # Clean up None values for JSON
                clean_data = {k: v for k, v in electricity_data.items() if v is not None}
                
                sync_response = requests.post(
                    f"{self.api_endpoint}/functions/v1/electricity-sync",
                    json={
                        'cabin_id': self.cabin_id,
                        'electricity_data': clean_data
                    },
                    headers={'Content-Type': 'application/json'}
                )

                if sync_response.status_code == 200:
                    logger.info(f"Electricity data synced: power={electricity_data['current_power']}W, import={electricity_data['total_import']}kWh")
                else:
                    logger.error(f"Electricity sync failed: {sync_response.text}")
            else:
                logger.warning("No electricity sensors found (need device_class=power or device_class=energy)")

        except Exception as e:
            logger.error(f"Electricity sync error: {str(e)}")

    def sync_cameras(self):
        """Sync camera entities to portal"""
        if not self.authenticated:
            logger.warning("Not authenticated, skipping camera sync")
            return

        logger.info("Syncing cameras...")

        try:
            supervisor_token = os.environ.get('SUPERVISOR_TOKEN')

            # Get all states from Home Assistant
            response = requests.get(
                'http://supervisor/core/api/states',
                headers={'Authorization': f'Bearer {supervisor_token}'}
            )

            if response.status_code != 200:
                logger.error(f"Failed to get states: {response.status_code}")
                return

            states = response.json()
            cameras = []

            for state in states:
                entity_id = state.get('entity_id', '')
                if entity_id.startswith('camera.'):
                    attrs = state.get('attributes', {})
                    cameras.append({
                        'entity_id': entity_id,
                        'name': attrs.get('friendly_name', entity_id),
                        'status': state.get('state', 'unknown'),
                        'manufacturer': attrs.get('manufacturer'),
                        'model': attrs.get('model'),
                        'supports_stream': attrs.get('frontend_stream_type') is not None
                    })

            if cameras:
                sync_response = requests.post(
                    f"{self.api_endpoint}/functions/v1/camera-sync",
                    json={
                        'cabin_id': self.cabin_id,
                        'cameras': cameras
                    },
                    headers={'Content-Type': 'application/json'}
                )

                if sync_response.status_code == 200:
                    logger.info(f"Synced {len(cameras)} cameras")
                else:
                    logger.error(f"Camera sync failed: {sync_response.text}")
            else:
                logger.info("No cameras found")

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

        # Initial sync
        if self.backup_enabled:
            self.backup_config()
        
        # Sync cameras and electricity on startup
        self.sync_cameras()
        self.sync_electricity()

        # Main loop
        last_electricity_sync = time.time()
        last_camera_sync = time.time()
        last_backup = time.time()

        while True:
            try:
                time.sleep(60)  # Check every minute
                now = time.time()

                # Electricity sync every 5 minutes
                if now - last_electricity_sync >= 300:
                    self.sync_electricity()
                    last_electricity_sync = now

                # Camera sync every hour
                if now - last_camera_sync >= 3600:
                    self.sync_cameras()
                    last_camera_sync = now

                # Backup every 24 hours
                if self.backup_enabled and now - last_backup >= 86400:
                    self.backup_config()
                    last_backup = now

            except KeyboardInterrupt:
                logger.info("Shutting down...")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {str(e)}")
                time.sleep(60)

if __name__ == '__main__':
    integration = MinHustomteIntegration()
    integration.run()

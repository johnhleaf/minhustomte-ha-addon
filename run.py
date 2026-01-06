#!/usr/bin/env python3
"""
MinHustomte Portal Integration for Home Assistant
This script integrates Home Assistant with the MinHustomte portal.
"""

import os
import sys
import json
import time
import logging
import requests
import hashlib
from datetime import datetime
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class MinHustomteIntegration:
    def __init__(self):
        self.config = {}
        self.authenticated = False
        self.cabin_id = None
        self.ha_username = None
        self.ha_password = None
        self.api_endpoint = None
        self.credentials_file = '/data/minhustomte_credentials.json'
        self.load_config()
    
    def load_config(self):
        """Load configuration from Home Assistant options."""
        try:
            with open('/data/options.json', 'r') as f:
                self.config = json.load(f)
            logger.info("Configuration loaded successfully")
        except FileNotFoundError:
            logger.warning("No options.json found, using defaults")
            self.config = {}
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing options.json: {e}")
            self.config = {}
    
    def load_credentials(self):
        """Load saved credentials from file."""
        try:
            if os.path.exists(self.credentials_file):
                with open(self.credentials_file, 'r') as f:
                    creds = json.load(f)
                    self.cabin_id = creds.get('cabin_id')
                    self.ha_username = creds.get('ha_username')
                    self.ha_password = creds.get('ha_password')
                    self.api_endpoint = creds.get('api_endpoint')
                    self.authenticated = True
                    logger.info(f"Loaded credentials for cabin: {self.cabin_id}")
                    return True
        except Exception as e:
            logger.error(f"Error loading credentials: {e}")
        return False
    
    def save_credentials(self):
        """Save credentials to file."""
        try:
            creds = {
                'cabin_id': self.cabin_id,
                'ha_username': self.ha_username,
                'ha_password': self.ha_password,
                'api_endpoint': self.api_endpoint,
                'saved_at': datetime.now().isoformat()
            }
            with open(self.credentials_file, 'w') as f:
                json.dump(creds, f)
            logger.info("Credentials saved successfully")
        except Exception as e:
            logger.error(f"Error saving credentials: {e}")
    
    def authenticate(self):
        """Authenticate with MinHustomte portal using auth code."""
        auth_code = self.config.get('auth_code', '')
        portal_url = self.config.get('portal_url', 'https://qqmxykhzatbdsabsarrd.supabase.co')
        
        if not auth_code:
            logger.error("No auth_code provided in configuration")
            return False
        
        try:
            logger.info(f"Authenticating with portal: {portal_url}")
            response = requests.post(
                f"{portal_url}/functions/v1/raspberry-auth",
                json={'auth_code': auth_code},
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                self.cabin_id = data.get('cabin_id')
                self.ha_username = data.get('ha_username')
                self.ha_password = data.get('ha_password')
                self.api_endpoint = portal_url
                self.authenticated = True
                self.save_credentials()
                logger.info(f"Authentication successful! Cabin ID: {self.cabin_id}")
                return True
            else:
                logger.error(f"Authentication failed: {response.status_code} - {response.text}")
                return False
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error during authentication: {e}")
            return False
    
    def get_ha_api_url(self):
        """Get Home Assistant API URL."""
        return "http://supervisor/core/api"
    
    def get_ha_token(self):
        """Get Home Assistant supervisor token."""
        return os.environ.get('SUPERVISOR_TOKEN', '')
    
    def get_ha_headers(self):
        """Get headers for Home Assistant API requests."""
        return {
            'Authorization': f'Bearer {self.get_ha_token()}',
            'Content-Type': 'application/json'
        }
    
    def create_ha_user(self):
        """Create Home Assistant user for portal access."""
        if not self.ha_username or not self.ha_password:
            logger.warning("No HA credentials to create user")
            return False
        
        try:
            # This would create a user in Home Assistant
            # For now, just log the intention
            logger.info(f"Would create HA user: {self.ha_username}")
            return True
        except Exception as e:
            logger.error(f"Error creating HA user: {e}")
            return False
    
    def install_theme(self):
        """Install MinHustomte theme in Home Assistant."""
        try:
            themes_dir = Path('/config/themes')
            themes_dir.mkdir(exist_ok=True)
            
            theme_content = """
minhustomte:
  # Primary colors
  primary-color: "#2E7D32"
  accent-color: "#4CAF50"
  
  # Background
  primary-background-color: "#1a1a2e"
  secondary-background-color: "#16213e"
  
  # Cards
  card-background-color: "#1a1a2e"
  ha-card-background: "#1a1a2e"
  ha-card-border-radius: "12px"
  
  # Text
  primary-text-color: "#ffffff"
  secondary-text-color: "#a0a0a0"
  
  # Header
  app-header-background-color: "#0f3460"
  app-header-text-color: "#ffffff"
  
  # Sidebar
  sidebar-background-color: "#16213e"
  sidebar-text-color: "#ffffff"
"""
            
            theme_file = themes_dir / 'minhustomte.yaml'
            with open(theme_file, 'w') as f:
                f.write(theme_content)
            
            logger.info("Theme installed successfully")
            
            # Reload themes
            try:
                requests.post(
                    f"{self.get_ha_api_url()}/services/frontend/reload_themes",
                    headers=self.get_ha_headers(),
                    timeout=10
                )
            except:
                pass
            
            return True
            
        except Exception as e:
            logger.error(f"Error installing theme: {e}")
            return False
    
    def backup_config(self):
        """Backup Home Assistant configuration to portal."""
        if not self.authenticated:
            logger.warning("Not authenticated, skipping backup")
            return False
        
        try:
            # Collect configuration data
            config_data = {
                'timestamp': datetime.now().isoformat(),
                'version': '1.0'
            }
            
            # Get automations
            try:
                automations_file = Path('/config/automations.yaml')
                if automations_file.exists():
                    with open(automations_file, 'r') as f:
                        config_data['automations'] = f.read()
            except Exception as e:
                logger.warning(f"Could not read automations: {e}")
            
            # Get scripts
            try:
                scripts_file = Path('/config/scripts.yaml')
                if scripts_file.exists():
                    with open(scripts_file, 'r') as f:
                        config_data['scripts'] = f.read()
            except Exception as e:
                logger.warning(f"Could not read scripts: {e}")
            
            # Get scenes
            try:
                scenes_file = Path('/config/scenes.yaml')
                if scenes_file.exists():
                    with open(scenes_file, 'r') as f:
                        config_data['scenes'] = f.read()
            except Exception as e:
                logger.warning(f"Could not read scenes: {e}")
            
            # Send backup to portal
            response = requests.post(
                f"{self.api_endpoint}/functions/v1/raspberry-backup",
                json={
                    'cabin_id': self.cabin_id,
                    'ha_username': self.ha_username,
                    'ha_password': self.ha_password,
                    'backup_data': config_data
                },
                headers={'Content-Type': 'application/json'},
                timeout=60
            )
            
            if response.status_code == 200:
                logger.info("Backup completed successfully")
                return True
            else:
                logger.error(f"Backup failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error during backup: {e}")
            return False
    
    def get_electricity_sensors(self):
        """Get all electricity-related sensors from Home Assistant."""
        try:
            response = requests.get(
                f"{self.get_ha_api_url()}/states",
                headers=self.get_ha_headers(),
                timeout=30
            )
            
            if response.status_code != 200:
                logger.error(f"Failed to get states: {response.status_code}")
                return {}
            
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
                'phase_l1_voltage': None,
                'phase_l1_current': None,
                'phase_l1_power': None,
                'phase_l2_voltage': None,
                'phase_l2_current': None,
                'phase_l2_power': None,
                'phase_l3_voltage': None,
                'phase_l3_current': None,
                'phase_l3_power': None
            }
            
            for state in states:
                entity_id = state.get('entity_id', '')
                attributes = state.get('attributes', {})
                device_class = attributes.get('device_class', '')
                state_value = state.get('state', '')
                
                # Skip unavailable states
                if state_value in ['unavailable', 'unknown', '']:
                    continue
                
                try:
                    value = float(state_value)
                except (ValueError, TypeError):
                    continue
                
                entity_lower = entity_id.lower()
                
                # Current power consumption
                if device_class == 'power' or 'power' in entity_lower:
                    if 'total' not in entity_lower and electricity_data['current_power'] is None:
                        electricity_data['current_power'] = value
                        logger.debug(f"Found current power: {entity_id} = {value}")
                
                # Energy consumption
                if device_class == 'energy' or 'energy' in entity_lower or 'kwh' in entity_lower:
                    if 'today' in entity_lower or 'daily' in entity_lower:
                        electricity_data['today_usage'] = value
                    elif 'month' in entity_lower:
                        electricity_data['month_usage'] = value
                    elif 'import' in entity_lower or 'consumption' in entity_lower:
                        electricity_data['total_import'] = value
                    elif 'export' in entity_lower:
                        electricity_data['total_export'] = value
                
                # Voltage
                if device_class == 'voltage' or 'voltage' in entity_lower:
                    if 'l1' in entity_lower or 'phase_1' in entity_lower:
                        electricity_data['phase_l1_voltage'] = value
                    elif 'l2' in entity_lower or 'phase_2' in entity_lower:
                        electricity_data['phase_l2_voltage'] = value
                    elif 'l3' in entity_lower or 'phase_3' in entity_lower:
                        electricity_data['phase_l3_voltage'] = value
                    elif electricity_data['voltage'] is None:
                        electricity_data['voltage'] = value
                
                # Current (amps)
                if device_class == 'current' or 'current' in entity_lower or 'ampere' in entity_lower:
                    if 'l1' in entity_lower or 'phase_1' in entity_lower:
                        electricity_data['phase_l1_current'] = value
                    elif 'l2' in entity_lower or 'phase_2' in entity_lower:
                        electricity_data['phase_l2_current'] = value
                    elif 'l3' in entity_lower or 'phase_3' in entity_lower:
                        electricity_data['phase_l3_current'] = value
                    elif electricity_data['current_amps'] is None:
                        electricity_data['current_amps'] = value
                
                # Phase power
                if 'power' in entity_lower:
                    if 'l1' in entity_lower or 'phase_1' in entity_lower:
                        electricity_data['phase_l1_power'] = value
                    elif 'l2' in entity_lower or 'phase_2' in entity_lower:
                        electricity_data['phase_l2_power'] = value
                    elif 'l3' in entity_lower or 'phase_3' in entity_lower:
                        electricity_data['phase_l3_power'] = value
                
                # Power factor
                if 'power_factor' in entity_lower or device_class == 'power_factor':
                    electricity_data['power_factor'] = value
            
            return electricity_data
            
        except Exception as e:
            logger.error(f"Error getting electricity sensors: {e}")
            return {}
    
    def sync_electricity(self):
        """Sync electricity data to portal."""
        if not self.authenticated:
            logger.warning("Not authenticated, skipping electricity sync")
            return False
        
        try:
            electricity_data = self.get_electricity_sensors()
            
            if not electricity_data.get('current_power'):
                logger.warning("No electricity data found to sync")
                return False
            
            # Clean the data - remove None values
            clean_data = {k: v for k, v in electricity_data.items() if v is not None}
            
            logger.info(f"Syncing electricity data: {clean_data}")
            
            response = requests.post(
                f"{self.api_endpoint}/functions/v1/electricity-sync",
                json={
                    'cabin_id': self.cabin_id,
                    'ha_username': self.ha_username,
                    'ha_password': self.ha_password,
                    'electricity': clean_data
                },
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            
            if response.status_code == 200:
                logger.info("Electricity data synced successfully")
                return True
            else:
                logger.error(f"Electricity sync failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error syncing electricity: {e}")
            return False
    
    def sync_cameras(self):
        """Sync camera entities to portal."""
        if not self.authenticated:
            logger.warning("Not authenticated, skipping camera sync")
            return False
        
        try:
            response = requests.get(
                f"{self.get_ha_api_url()}/states",
                headers=self.get_ha_headers(),
                timeout=30
            )
            
            if response.status_code != 200:
                logger.error(f"Failed to get states for cameras: {response.status_code}")
                return False
            
            states = response.json()
            cameras = []
            
            for state in states:
                entity_id = state.get('entity_id', '')
                if entity_id.startswith('camera.'):
                    attributes = state.get('attributes', {})
                    cameras.append({
                        'entity_id': entity_id,
                        'name': attributes.get('friendly_name', entity_id),
                        'status': state.get('state', 'unknown'),
                        'manufacturer': attributes.get('manufacturer'),
                        'model': attributes.get('model'),
                        'supports_stream': attributes.get('supported_features', 0) & 2 > 0
                    })
            
            if not cameras:
                logger.info("No cameras found to sync")
                return True
            
            logger.info(f"Found {len(cameras)} cameras to sync")
            
            response = requests.post(
                f"{self.api_endpoint}/functions/v1/camera-sync",
                json={
                    'cabin_id': self.cabin_id,
                    'ha_username': self.ha_username,
                    'ha_password': self.ha_password,
                    'cameras': cameras
                },
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            
            if response.status_code == 200:
                logger.info("Cameras synced successfully")
                return True
            else:
                logger.error(f"Camera sync failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error syncing cameras: {e}")
            return False
    
    def run(self):
        """Main run loop."""
        logger.info("Starting MinHustomte Integration")
        
        # Try to load existing credentials
        if not self.load_credentials():
            # Authenticate with portal
            if not self.authenticate():
                logger.error("Failed to authenticate with portal")
                return
        
        # Create HA user
        self.create_ha_user()
        
        # Install theme
        self.install_theme()
        
        # Initial sync
        self.sync_electricity()
        self.sync_cameras()
        self.backup_config()
        
        # Get sync intervals from config
        electricity_interval = self.config.get('electricity_sync_interval', 60)  # seconds
        camera_interval = self.config.get('camera_sync_interval', 300)  # seconds
        backup_interval = self.config.get('backup_interval', 3600)  # seconds
        
        last_electricity_sync = time.time()
        last_camera_sync = time.time()
        last_backup = time.time()
        
        logger.info(f"Sync intervals - Electricity: {electricity_interval}s, Cameras: {camera_interval}s, Backup: {backup_interval}s")
        
        # Main loop
        while True:
            try:
                current_time = time.time()
                
                # Sync electricity
                if current_time - last_electricity_sync >= electricity_interval:
                    self.sync_electricity()
                    last_electricity_sync = current_time
                
                # Sync cameras
                if current_time - last_camera_sync >= camera_interval:
                    self.sync_cameras()
                    last_camera_sync = current_time
                
                # Backup
                if current_time - last_backup >= backup_interval:
                    self.backup_config()
                    last_backup = current_time
                
                # Sleep for a bit
                time.sleep(10)
                
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(30)


if __name__ == '__main__':
    integration = MinHustomteIntegration()
    integration.run()

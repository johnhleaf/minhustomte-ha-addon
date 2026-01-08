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
import threading
import base64
from datetime import datetime
from pathlib import Path

# WebSocket support
try:
    import websocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    logging.warning("websocket-client not installed, camera streaming disabled")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CameraStreamer:
    """Handles WebSocket streaming of camera frames to portal."""
    
    def __init__(self, integration, entity_id):
        self.integration = integration
        self.entity_id = entity_id
        self.ws = None
        self.running = False
        self.thread = None
        self.frame_interval = 0.1  # 10 FPS
    
    def start(self):
        """Start streaming camera frames."""
        if not WEBSOCKET_AVAILABLE:
            logger.error("WebSocket not available, cannot stream camera")
            return False
        
        self.running = True
        self.thread = threading.Thread(target=self._stream_loop, daemon=True)
        self.thread.start()
        logger.info(f"Started camera streamer for {self.entity_id}")
        return True
    
    def stop(self):
        """Stop streaming."""
        self.running = False
        if self.ws:
            try:
                self.ws.close()
            except:
                pass
        self.ws = None
        logger.info(f"Stopped camera streamer for {self.entity_id}")
    
    def _connect(self):
        """Connect to portal WebSocket."""
        try:
            ws_url = (
                f"wss://qqmxykhzatbdsabsarrd.functions.supabase.co/functions/v1/camera-stream"
                f"?role=provider"
                f"&cabin_id={self.integration.cabin_id}"
                f"&entity_id={self.entity_id}"
                f"&ha_username={self.integration.ha_username}"
                f"&ha_password={self.integration.ha_password}"
            )
            
            logger.info(f"Connecting to stream relay: {ws_url[:80]}...")
            
            self.ws = websocket.create_connection(
                ws_url,
                timeout=30,
                header=["User-Agent: MinHustomte-HA-Addon/1.0"]
            )
            
            logger.info(f"Connected to stream relay for {self.entity_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to stream relay: {e}")
            return False
    
    def _get_camera_frame(self):
        """Get current camera frame from Home Assistant."""
        try:
            response = requests.get(
                f"{self.integration.get_ha_api_url()}/camera_proxy/{self.entity_id}",
                headers=self.integration.get_ha_headers(),
                timeout=10
            )
            
            if response.status_code == 200:
                return response.content
            else:
                logger.warning(f"Failed to get camera frame: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting camera frame: {e}")
            return None
    
    def _stream_loop(self):
        """Main streaming loop."""
        reconnect_delay = 5
        
        while self.running:
            try:
                if not self.ws or not self.ws.connected:
                    if not self._connect():
                        time.sleep(reconnect_delay)
                        continue
                
                try:
                    msg = self.ws.recv()
                    data = json.loads(msg)
                    
                    if data.get('type') == 'start_stream':
                        logger.info(f"Received start_stream command for {self.entity_id}")
                        self._send_frames()
                    
                except websocket.WebSocketTimeoutException:
                    continue
                except Exception as e:
                    logger.error(f"Error receiving message: {e}")
                    self.ws = None
                    time.sleep(reconnect_delay)
                    
            except Exception as e:
                logger.error(f"Stream loop error: {e}")
                time.sleep(reconnect_delay)
        
        logger.info(f"Stream loop ended for {self.entity_id}")
    
    def _send_frames(self):
        """Send camera frames to connected viewers."""
        frames_sent = 0
        
        while self.running and self.ws and self.ws.connected:
            try:
                frame_data = self._get_camera_frame()
                
                if frame_data:
                    self.ws.send_binary(frame_data)
                    frames_sent += 1
                    
                    if frames_sent % 100 == 0:
                        logger.debug(f"Sent {frames_sent} frames for {self.entity_id}")
                
                time.sleep(self.frame_interval)
                
            except websocket.WebSocketConnectionClosedException:
                logger.info(f"WebSocket closed, stopping frame sending")
                break
            except Exception as e:
                logger.error(f"Error sending frame: {e}")
                break
        
        logger.info(f"Frame sending ended, total frames: {frames_sent}")


class MinHustomteIntegration:
    def __init__(self):
        self.config = {}
        self.authenticated = False
        self.cabin_id = None
        self.ha_username = None
        self.ha_password = None
        self.api_endpoint = None
        self.credentials_file = '/data/minhustomte_credentials.json'
        self.camera_streamers = {}
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
            config_data = {
                'timestamp': datetime.now().isoformat(),
                'version': '1.0'
            }
            
            try:
                automations_file = Path('/config/automations.yaml')
                if automations_file.exists():
                    with open(automations_file, 'r') as f:
                        config_data['automations'] = f.read()
            except Exception as e:
                logger.warning(f"Could not read automations: {e}")
            
            try:
                scripts_file = Path('/config/scripts.yaml')
                if scripts_file.exists():
                    with open(scripts_file, 'r') as f:
                        config_data['scripts'] = f.read()
            except Exception as e:
                logger.warning(f"Could not read scripts: {e}")
            
            try:
                scenes_file = Path('/config/scenes.yaml')
                if scenes_file.exists():
                    with open(scenes_file, 'r') as f:
                        config_data['scenes'] = f.read()
            except Exception as e:
                logger.warning(f"Could not read scenes: {e}")
            
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
                
                if state_value in ['unavailable', 'unknown', '']:
                    continue
                
                try:
                    value = float(state_value)
                except (ValueError, TypeError):
                    continue
                
                entity_lower = entity_id.lower()
                
                if device_class == 'power' or 'power' in entity_lower:
                    if 'total' not in entity_lower and electricity_data['current_power'] is None:
                        electricity_data['current_power'] = value
                        logger.debug(f"Found current power: {entity_id} = {value}")
                
                if device_class == 'energy' or 'energy' in entity_lower or 'kwh' in entity_lower:
                    if 'today' in entity_lower or 'daily' in entity_lower:
                        electricity_data['today_usage'] = value
                    elif 'month' in entity_lower:
                        electricity_data['month_usage'] = value
                    elif 'import' in entity_lower or 'consumption' in entity_lower:
                        electricity_data['total_import'] = value
                    elif 'export' in entity_lower:
                        electricity_data['total_export'] = value
                
                if device_class == 'voltage' or 'voltage' in entity_lower:
                    if 'l1' in entity_lower or 'phase_1' in entity_lower:
                        electricity_data['phase_l1_voltage'] = value
                    elif 'l2' in entity_lower or 'phase_2' in entity_lower:
                        electricity_data['phase_l2_voltage'] = value
                    elif 'l3' in entity_lower or 'phase_3' in entity_lower:
                        electricity_data['phase_l3_voltage'] = value
                    elif electricity_data['voltage'] is None:
                        electricity_data['voltage'] = value
                
                if device_class == 'current' or 'current' in entity_lower or 'ampere' in entity_lower:
                    if 'l1' in entity_lower or 'phase_1' in entity_lower:
                        electricity_data['phase_l1_current'] = value
                    elif 'l2' in entity_lower or 'phase_2' in entity_lower:
                        electricity_data['phase_l2_current'] = value
                    elif 'l3' in entity_lower or 'phase_3' in entity_lower:
                        electricity_data['phase_l3_current'] = value
                    elif electricity_data['current_amps'] is None:
                        electricity_data['current_amps'] = value
                
                if 'power' in entity_lower:
                    if 'l1' in entity_lower or 'phase_1' in entity_lower:
                        electricity_data['phase_l1_power'] = value
                    elif 'l2' in entity_lower or 'phase_2' in entity_lower:
                        electricity_data['phase_l2_power'] = value
                    elif 'l3' in entity_lower or 'phase_3' in entity_lower:
                        electricity_data['phase_l3_power'] = value
                
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
    
    def get_cameras(self):
        """Get all camera entities from Home Assistant."""
        try:
            response = requests.get(
                f"{self.get_ha_api_url()}/states",
                headers=self.get_ha_headers(),
                timeout=30
            )
            
            if response.status_code != 200:
                logger.error(f"Failed to get states for cameras: {response.status_code}")
                return []
            
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
            
            return cameras
            
        except Exception as e:
            logger.error(f"Error getting cameras: {e}")
            return []
    
    def sync_cameras(self):
        """Sync camera entities to portal."""
        if not self.authenticated:
            logger.warning("Not authenticated, skipping camera sync")
            return False
        
        try:
            cameras = self.get_cameras()
            
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
    
    def start_camera_streamers(self):
        """Start streaming for all available cameras."""
        if not WEBSOCKET_AVAILABLE:
            logger.warning("WebSocket not available, camera streaming disabled")
            return
        
        cameras = self.get_cameras()
        
        for camera in cameras:
            entity_id = camera['entity_id']
            
            if entity_id not in self.camera_streamers:
                streamer = CameraStreamer(self, entity_id)
                if streamer.start():
                    self.camera_streamers[entity_id] = streamer
                    logger.info(f"Started streamer for {entity_id}")
    
    def stop_camera_streamers(self):
        """Stop all camera streamers."""
        for entity_id, streamer in self.camera_streamers.items():
            streamer.stop()
        self.camera_streamers.clear()
    
    def run(self):
        """Main run loop."""
        logger.info("Starting MinHustomte Integration")
        
        # Try to load existing credentials
        if not self.load_credentials():
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
        
        # Start camera streamers
        self.start_camera_streamers()
        
        # Get sync intervals from config
        electricity_interval = self.config.get('electricity_sync_interval', 60)
        camera_interval = self.config.get('camera_sync_interval', 300)
        backup_interval = self.config.get('backup_interval', 3600)
        
        last_electricity_sync = time.time()
        last_camera_sync = time.time()
        last_backup = time.time()
        
        logger.info(f"Sync intervals - Electricity: {electricity_interval}s, Cameras: {camera_interval}s, Backup: {backup_interval}s")
        
        # Main loop
        while True:
            try:
                current_time = time.time()
                
                if current_time - last_electricity_sync >= electricity_interval:
                    self.sync_electricity()
                    last_electricity_sync = current_time
                
                if current_time - last_camera_sync >= camera_interval:
                    self.sync_cameras()
                    # Restart streamers if needed
                    self.start_camera_streamers()
                    last_camera_sync = current_time
                
                if current_time - last_backup >= backup_interval:
                    self.backup_config()
                    last_backup = current_time
                
                time.sleep(10)
                
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                self.stop_camera_streamers()
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(30)


if __name__ == '__main__':
    integration = MinHustomteIntegration()
    integration.run()

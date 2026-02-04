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
from urllib.parse import quote_plus
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


class TunnelClient:
    """Handles HA tunnel requests from portal via Supabase Realtime."""
    
    def __init__(self, integration):
        self.integration = integration
        self.running = False
        self.thread = None
        self.poll_interval = 2  # Poll every 2 seconds
    
    def start(self):
        """Start listening for tunnel requests."""
        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        logger.info("Started HA tunnel client")
        return True
    
    def stop(self):
        """Stop listening."""
        self.running = False
        logger.info("Stopped HA tunnel client")
    
    def _poll_loop(self):
        """Poll for pending tunnel requests."""
        while self.running:
            try:
                self._process_pending_requests()
                time.sleep(self.poll_interval)
            except Exception as e:
                logger.error(f"Error in tunnel poll loop: {e}")
                time.sleep(5)
    
    def _process_pending_requests(self):
        """Check for and process pending tunnel requests."""
        if not self.integration.authenticated:
            return
        
        try:
            # Fetch pending requests for this cabin
            response = requests.get(
                f"{self.integration.api_endpoint}/rest/v1/ha_tunnel_requests",
                params={
                    'cabin_id': f'eq.{self.integration.cabin_id}',
                    'status': 'eq.pending',
                    'select': '*'
                },
                headers={
                    'apikey': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFxbXh5a2h6YXRiZHNhYnNhcnJkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQ1MzAyNTQsImV4cCI6MjA4MDEwNjI1NH0.tXboR_2k7Pwh3cFAngWKNL9f2f-YdZM6sVVD4lFYQKo',
                    'Authorization': f'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFxbXh5a2h6YXRiZHNhYnNhcnJkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQ1MzAyNTQsImV4cCI6MjA4MDEwNjI1NH0.tXboR_2k7Pwh3cFAngWKNL9f2f-YdZM6sVVD4lFYQKo',
                    'Content-Type': 'application/json'
                },
                timeout=10
            )
            
            if response.status_code != 200:
                return
            
            requests_list = response.json()
            
            for req in requests_list:
                self._handle_request(req)
                
        except Exception as e:
            logger.debug(f"Error fetching tunnel requests: {e}")
    
    def _handle_request(self, req):
        """Handle a single tunnel request."""
        request_id = req.get('id')
        request_data = req.get('request', {})
        action = request_data.get('action')
        
        logger.info(f"Processing tunnel request: {action} (ID: {request_id})")
        
        try:
            result = None
            error = None
            
            if action == 'ping':
                result = {'status': 'pong', 'timestamp': datetime.now().isoformat()}
            
            elif action == 'list_entities':
                result = self._list_entities(request_data.get('filter'))
            
            elif action == 'get_state':
                entity_id = request_data.get('entity_id')
                result = self._get_state(entity_id)
            
            elif action == 'get_states':
                result = self._get_all_states()
            
            elif action == 'call_service':
                domain = request_data.get('domain')
                service = request_data.get('service')
                service_data = request_data.get('service_data', {})
                result = self._call_service(domain, service, service_data)
            
            else:
                error = f"Unknown action: {action}"
            
            # Update request with response
            self._update_request(request_id, result, error)
            
        except Exception as e:
            logger.error(f"Error handling tunnel request: {e}")
            self._update_request(request_id, None, str(e))
    
    def _update_request(self, request_id, result, error):
        """Update the request with response."""
        try:
            update_data = {
                'status': 'completed' if error is None else 'error',
                'updated_at': datetime.now().isoformat()
            }
            
            if result is not None:
                update_data['response'] = result
            if error is not None:
                update_data['error'] = error
            
            response = requests.patch(
                f"{self.integration.api_endpoint}/rest/v1/ha_tunnel_requests",
                params={'id': f'eq.{request_id}'},
                json=update_data,
                headers={
                    'apikey': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFxbXh5a2h6YXRiZHNhYnNhcnJkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQ1MzAyNTQsImV4cCI6MjA4MDEwNjI1NH0.tXboR_2k7Pwh3cFAngWKNL9f2f-YdZM6sVVD4lFYQKo',
                    'Authorization': f'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFxbXh5a2h6YXRiZHNhYnNhcnJkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQ1MzAyNTQsImV4cCI6MjA4MDEwNjI1NH0.tXboR_2k7Pwh3cFAngWKNL9f2f-YdZM6sVVD4lFYQKo',
                    'Content-Type': 'application/json',
                    'Prefer': 'return=minimal'
                },
                timeout=10
            )
            
            if response.status_code in [200, 204]:
                logger.info(f"Updated tunnel request {request_id}: {'completed' if error is None else 'error'}")
            else:
                logger.error(f"Failed to update request: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Error updating tunnel request: {e}")
    
    def _list_entities(self, filter_opts=None):
        """List all HA entities with optional filtering."""
        try:
            ha_url = self.integration.get_ha_api_url()
            headers = self.integration.get_ha_headers()
            
            response = requests.get(
                f"{ha_url}/states",
                headers=headers,
                timeout=30
            )
            
            if response.status_code != 200:
                return {'error': f"HA API error: {response.status_code}"}
            
            states = response.json()
            entities = []
            
            for state in states:
                entity = {
                    'entity_id': state.get('entity_id'),
                    'state': state.get('state'),
                    'friendly_name': state.get('attributes', {}).get('friendly_name'),
                    'device_class': state.get('attributes', {}).get('device_class'),
                    'unit_of_measurement': state.get('attributes', {}).get('unit_of_measurement'),
                    'domain': state.get('entity_id', '').split('.')[0]
                }
                
                # Apply filters if provided
                if filter_opts:
                    if filter_opts.get('domain') and entity['domain'] != filter_opts['domain']:
                        continue
                    if filter_opts.get('device_class') and entity['device_class'] != filter_opts['device_class']:
                        continue
                
                entities.append(entity)
            
            return {'entities': entities, 'count': len(entities)}
            
        except Exception as e:
            return {'error': str(e)}
    
    def _get_state(self, entity_id):
        """Get state of a specific entity."""
        try:
            ha_url = self.integration.get_ha_api_url()
            headers = self.integration.get_ha_headers()
            
            response = requests.get(
                f"{ha_url}/states/{entity_id}",
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                return {'error': f"Entity not found: {entity_id}"}
                
        except Exception as e:
            return {'error': str(e)}
    
    def _get_all_states(self):
        """Get all states from HA."""
        try:
            ha_url = self.integration.get_ha_api_url()
            headers = self.integration.get_ha_headers()
            
            response = requests.get(
                f"{ha_url}/states",
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                return {'states': response.json()}
            else:
                return {'error': f"HA API error: {response.status_code}"}
                
        except Exception as e:
            return {'error': str(e)}
    
    def _call_service(self, domain, service, service_data):
        """Call a HA service."""
        try:
            ha_url = self.integration.get_ha_api_url()
            headers = self.integration.get_ha_headers()
            
            response = requests.post(
                f"{ha_url}/services/{domain}/{service}",
                json=service_data,
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                return {'success': True, 'result': response.json()}
            else:
                return {'error': f"Service call failed: {response.status_code}"}
                
        except Exception as e:
            return {'error': str(e)}


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
                f"&cabin_id={quote_plus(str(self.integration.cabin_id or ''))}"
                f"&entity_id={quote_plus(str(self.entity_id or ''))}"
                f"&ha_username={quote_plus(str(self.integration.ha_username or ''))}"
                f"&ha_password={quote_plus(str(self.integration.ha_password or ''))}"
            )

            logger.info(f"Connecting to stream relay: {ws_url[:80]}...")

            self.ws = websocket.create_connection(
                ws_url,
                timeout=30,
                header=["User-Agent: MinHustomte-HA-Addon/1.0"],
            )

            # Ensure recv() doesn't block forever (lets us keep the loop responsive)
            try:
                self.ws.settimeout(30)
            except Exception:
                pass

            logger.info(f"Connected to stream relay for {self.entity_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to stream relay: {e}")
            return False
    
    def _get_camera_frame(self):
        """Get current camera frame from Home Assistant."""
        try:
            # Use camera.get_image service or direct API
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
                # Connect if not connected
                if not self.ws or not self.ws.connected:
                    if not self._connect():
                        time.sleep(reconnect_delay)
                        continue
                
                # Wait for start_stream command
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
        last_ping = 0.0

        while self.running and self.ws and self.ws.connected:
            try:
                now = time.time()
                # Keepalive ping every 15s to reduce idle disconnects
                if now - last_ping > 15:
                    try:
                        self.ws.ping()
                    except Exception:
                        pass
                    last_ping = now

                # Get frame from camera
                frame_data = self._get_camera_frame()

                if frame_data:
                    # Send as binary
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
        self.tunnel_client = None
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
                friendly_name = attributes.get('friendly_name', '')
                unit = attributes.get('unit_of_measurement', '')
                state_value = state.get('state', '')
                
                if state_value in ['unavailable', 'unknown', '']:
                    continue
                
                try:
                    value = float(state_value)
                except (ValueError, TypeError):
                    continue
                
                # Check both entity_id and friendly_name for matching
                entity_lower = entity_id.lower()
                name_lower = friendly_name.lower()
                search_text = f"{entity_lower} {name_lower}"
                
                # Power sensors (W or kW)
                is_power = device_class == 'power' or unit in ['W', 'kW'] or 'power' in entity_lower or 'effekt' in name_lower
                if is_power:
                    if 'total' not in search_text and electricity_data['current_power'] is None:
                        electricity_data['current_power'] = value if unit != 'kW' else value * 1000
                        logger.debug(f"Found current power: {entity_id} ({friendly_name}) = {value}")
                
                # Energy sensors (kWh)
                is_energy = device_class == 'energy' or unit == 'kWh' or 'energy' in search_text or 'energi' in search_text
                if is_energy:
                    if 'today' in search_text or 'daily' in search_text or 'idag' in search_text or 'dygn' in search_text:
                        electricity_data['today_usage'] = value
                    elif 'month' in search_text or 'månad' in search_text:
                        electricity_data['month_usage'] = value
                    elif 'import' in search_text or 'consumption' in search_text or 'förbrukning' in search_text:
                        electricity_data['total_import'] = value
                    elif 'export' in search_text:
                        electricity_data['total_export'] = value
                
                # Voltage sensors (V)
                is_voltage = device_class == 'voltage' or unit == 'V' or 'voltage' in search_text or 'spänning' in search_text
                if is_voltage:
                    if 'l1' in search_text or 'phase_1' in search_text or 'fas_1' in search_text or 'fas 1' in search_text:
                        electricity_data['phase_l1_voltage'] = value
                    elif 'l2' in search_text or 'phase_2' in search_text or 'fas_2' in search_text or 'fas 2' in search_text:
                        electricity_data['phase_l2_voltage'] = value
                    elif 'l3' in search_text or 'phase_3' in search_text or 'fas_3' in search_text or 'fas 3' in search_text:
                        electricity_data['phase_l3_voltage'] = value
                    elif electricity_data['voltage'] is None:
                        electricity_data['voltage'] = value
                
                # Current sensors (A)
                is_current = device_class == 'current' or unit == 'A' or 'current' in search_text or 'ampere' in search_text or 'ström' in search_text
                if is_current:
                    if 'l1' in search_text or 'phase_1' in search_text or 'fas_1' in search_text or 'fas 1' in search_text:
                        electricity_data['phase_l1_current'] = value
                    elif 'l2' in search_text or 'phase_2' in search_text or 'fas_2' in search_text or 'fas 2' in search_text:
                        electricity_data['phase_l2_current'] = value
                    elif 'l3' in search_text or 'phase_3' in search_text or 'fas_3' in search_text or 'fas 3' in search_text:
                        electricity_data['phase_l3_current'] = value
                    elif electricity_data['current_amps'] is None:
                        electricity_data['current_amps'] = value
                
                # Phase power sensors (W) - check friendly_name for "Effekt Fas X"
                if is_power:
                    if 'l1' in search_text or 'phase_1' in search_text or 'fas_1' in search_text or 'fas 1' in search_text:
                        electricity_data['phase_l1_power'] = value if unit != 'kW' else value * 1000
                        logger.debug(f"Found L1 power: {friendly_name} = {value}")
                    elif 'l2' in search_text or 'phase_2' in search_text or 'fas_2' in search_text or 'fas 2' in search_text:
                        electricity_data['phase_l2_power'] = value if unit != 'kW' else value * 1000
                        logger.debug(f"Found L2 power: {friendly_name} = {value}")
                    elif 'l3' in search_text or 'phase_3' in search_text or 'fas_3' in search_text or 'fas 3' in search_text:
                        electricity_data['phase_l3_power'] = value if unit != 'kW' else value * 1000
                        logger.debug(f"Found L3 power: {friendly_name} = {value}")
                
                if 'power_factor' in search_text or device_class == 'power_factor':
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
    
    def start_tunnel_client(self):
        """Start the HA tunnel client for portal requests."""
        if self.tunnel_client:
            self.tunnel_client.stop()
        
        self.tunnel_client = TunnelClient(self)
        self.tunnel_client.start()
        logger.info("HA tunnel client started")
    
    def stop_tunnel_client(self):
        """Stop the HA tunnel client."""
        if self.tunnel_client:
            self.tunnel_client.stop()
            self.tunnel_client = None
    
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
        
        # Start tunnel client for portal requests
        self.start_tunnel_client()
        
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
                self.stop_tunnel_client()
                self.stop_camera_streamers()
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(30)


if __name__ == '__main__':
    integration = MinHustomteIntegration()
    integration.run()

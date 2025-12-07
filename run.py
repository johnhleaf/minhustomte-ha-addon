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
        
        # Initial backup
        if self.backup_enabled:
            self.backup_config()
        
        # Main loop - backup every 24 hours
        while True:
            try:
                time.sleep(86400)  # 24 hours
                
                if self.backup_enabled:
                    self.backup_config()
                    
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {str(e)}")
                time.sleep(60)

if __name__ == '__main__':
    integration = MinHustomteIntegration()
    integration.run()

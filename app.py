"""
Audiobook Eclipse Music Addon
Streams audiobooks via c411 (Torznab) + AllDebrid
"""

import os
import logging
from flask import Flask
from flask_cors import CORS
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from helpers import TorznabClient, AllDebridClient, AudibleClient
from routes import register_all_routes

# Configure logging level from environment
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
log_level_map = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL
}

logging.basicConfig(
    level=log_level_map.get(LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Configuration from environment variables
C411_API_KEY = os.environ.get('C411_API_KEY')
ALLDEBRID_API_KEY = os.environ.get('ALLDEBRID_API_KEY')
ECLIPSE_API_KEY = os.environ.get('ECLIPSE_API_KEY', '')  # Optional token validation

# Validate required configuration
if not C411_API_KEY:
    logger.error("C411_API_KEY environment variable is required")
    raise ValueError("C411_API_KEY not configured")

if not ALLDEBRID_API_KEY:
    logger.error("ALLDEBRID_API_KEY environment variable is required")
    raise ValueError("ALLDEBRID_API_KEY not configured")

logger.info("Configuration loaded successfully")
logger.info(f"Log level: {LOG_LEVEL}")
logger.info(f"Token validation: {'enabled' if ECLIPSE_API_KEY else 'disabled'}")

# Create HTTP session with connection pooling and retries
session = requests.Session()

# Configure retries for transient errors
retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[500, 502, 503, 504],
    allowed_methods=["GET", "POST"]
)

adapter = HTTPAdapter(
    pool_connections=10,
    pool_maxsize=20,
    max_retries=retry_strategy
)

session.mount("http://", adapter)
session.mount("https://", adapter)

# Create separate session for streaming (no retries, larger pool for speed)
streaming_session = requests.Session()
streaming_adapter = HTTPAdapter(
    pool_connections=50,
    pool_maxsize=100
)
streaming_session.mount("http://", streaming_adapter)
streaming_session.mount("https://", streaming_adapter)

logger.info("HTTP session configured with connection pooling")

# Initialize API clients
torznab_client = TorznabClient(C411_API_KEY, session)
alldebrid_client = AllDebridClient(ALLDEBRID_API_KEY, session)
audible_client = AudibleClient(session)

logger.info("API clients initialized (c411 + AllDebrid + Audible)")

# Register all routes
register_all_routes(app, ECLIPSE_API_KEY, torznab_client, alldebrid_client, audible_client, streaming_session)

logger.info("All routes registered")

if __name__ == '__main__':
    # Development server
    app.run(
        host='0.0.0.0',
        port=5001,
        debug=(LOG_LEVEL == 'DEBUG')
    )

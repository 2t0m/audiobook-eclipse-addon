"""
Unlock route for audiobook Eclipse addon
Debrids AllDebrid links on-demand
"""

from flask import jsonify, redirect
from helpers import validate_token
import logging
import base64
import json

logger = logging.getLogger(__name__)

# In-memory cache for unlocked links (5 minutes TTL)
_unlock_cache = {}
UNLOCK_CACHE_TTL = 300  # 5 minutes


def register_routes(app, api_key, alldebrid_client):
    """Register unlock route"""
    
    @app.route('/<token>/unlock/<encoded_data>')
    def unlock(token, encoded_data):
        """
        Unlock an AllDebrid link on-demand
        Returns 302 redirect to the unlocked direct download URL
        """
        if not validate_token(token, api_key):
            return jsonify({'error': 'Unauthorized'}), 401
        
        try:
            # Decode base64url-safe data
            decoded_json = base64.urlsafe_b64decode(encoded_data + '==').decode('utf-8')
            link_data = json.loads(decoded_json)
            
            file_name = link_data.get('fileName', 'unknown')
            alldebrid_link = link_data.get('allDebridLink')
            
            if not alldebrid_link:
                logger.error("No AllDebrid link in encoded data")
                return jsonify({'error': 'Invalid unlock data'}), 400
            
            logger.info(f"Unlock request for: {file_name}")
            
            # Cache disabled for debugging
            # if alldebrid_link in _unlock_cache:
            #     cached_link = _unlock_cache[alldebrid_link]
            #     logger.info(f"Cache hit for unlock: {file_name}")
            #     return redirect(cached_link, code=302)
            
            # Unlock the link via AllDebrid
            unlocked_link = alldebrid_client.unlock_link(alldebrid_link)
            
            if not unlocked_link:
                logger.error(f"Failed to unlock link for: {file_name}")
                return jsonify({'error': 'Failed to unlock file'}), 500
            
            # Cache disabled for debugging
            # _unlock_cache[alldebrid_link] = unlocked_link
            
            logger.info(f"Unlock success: {file_name}")
            
            # Redirect to the unlocked direct download URL
            return redirect(unlocked_link, code=302)
        
        except Exception as e:
            logger.error(f"Error in unlock route: {e}")
            return jsonify({'error': 'Unlock error'}), 500

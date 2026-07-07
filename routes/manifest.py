"""
Manifest route for Eclipse Music addon
"""

from flask import jsonify
from helpers import validate_token


def register_routes(app, api_key):
    """Register manifest route"""
    
    @app.route('/<token>/manifest.json')
    def manifest(token):
        """Addon manifest - describes capabilities to Eclipse Music"""
        if not validate_token(token, api_key):
            return jsonify({'error': 'Unauthorized'}), 401
        
        return jsonify({
            'id': 'com.audiobook.eclipse',
            'name': 'Audiobooks',
            'version': '1.0.3',
            'description': 'Stream audiobooks via c411 + AllDebrid. M4B, MP3, and M4A formats supported. Now with playlist support!',
            'icon': 'https://cdn-icons-png.flaticon.com/512/2702/2702154.png',
            'resources': ['search', 'stream', 'album', 'track'],
            'types': ['track', 'album'],
            'contentType': 'audiobook'
        })

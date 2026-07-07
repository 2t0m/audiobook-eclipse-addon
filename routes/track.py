"""
Track route for Eclipse Music addon
Returns metadata for a single track (used by playlists/library)
"""

from flask import jsonify, request
from helpers import validate_token, get_track_file, get_album_metadata
import logging

logger = logging.getLogger(__name__)


def register_routes(app, api_key):
    """Register track route"""
    
    @app.route('/<token>/track/<track_id>')
    def track(token, track_id):
        """
        Get metadata for a single track
        Used when Eclipse needs to refresh track info (playlists, library)
        """
        if not validate_token(token, api_key):
            return jsonify({'error': 'Unauthorized'}), 401
        
        # Remove file extension if present
        track_id_clean = track_id.rsplit('.', 1)[0] if '.' in track_id else track_id
        
        logger.info(f"Track metadata request for: {track_id_clean}")
        
        # Get track file info from cache
        track_file = get_track_file(track_id_clean)
        
        if not track_file:
            logger.error(f"Track not found in cache: {track_id_clean}")
            return jsonify({
                'error': 'Track not found',
                'message': 'Track not in cache. Please open the album again.'
            }), 404
        
        # Get album metadata
        album_id = track_file.get('album_id')
        album_metadata = get_album_metadata(album_id) if album_id else None
        
        # Detect format from filename
        filename = track_file.get('filename', '')
        file_format = 'mp3'  # default
        if filename.lower().endswith('.m4b'):
            file_format = 'm4b'
        elif filename.lower().endswith('.m4a'):
            file_format = 'm4a'
        elif filename.lower().endswith('.mp3'):
            file_format = 'mp3'
        
        # Build fresh streamURL
        base_url = f"https://{request.host}"
        
        # Return track metadata with fresh streamURL
        track_data = {
            'id': track_id_clean,
            'title': filename,
            'artist': album_metadata.get('artist', 'Unknown') if album_metadata else 'Unknown',
            'album': album_metadata.get('title', 'Unknown Album') if album_metadata else 'Unknown Album',
            'albumId': album_id,
            'duration': None,  # Cannot estimate without file size
            'artworkURL': album_metadata.get('artwork_url') if album_metadata else None,
            'streamURL': f"{base_url}/{token}/stream/{track_id_clean}.{file_format}",
            'format': file_format
        }
        
        logger.info(f"Returning metadata for track: {track_id_clean}")
        
        return jsonify(track_data)

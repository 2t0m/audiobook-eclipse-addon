"""
Album route for Eclipse Music addon
Returns all tracks for a multi-file audiobook
"""

from flask import jsonify, request
from helpers import validate_token, get_track_magnet, get_album_metadata, cache_track_file
import logging
import hashlib

logger = logging.getLogger(__name__)


def register_routes(app, api_key, alldebrid_client):
    """Register album route"""
    
    @app.route('/<token>/album/<album_id>')
    def album(token, album_id):
        """
        Get all tracks for an audiobook album
        Returns list of tracks with metadata only (no streamURL)
        Eclipse Music will call /stream/{track_id} separately when playing
        """
        if not validate_token(token, api_key):
            return jsonify({'error': 'Unauthorized'}), 401
        
        logger.info(f"Album request for: {album_id}")
        
        # Get magnet URL from cache (album_id is the torrent hash)
        magnet_url = get_track_magnet(album_id)
        
        if not magnet_url:
            logger.error(f"No magnet URL found for album: {album_id}")
            return jsonify({
                'error': 'Album not found',
                'message': 'Please search for the audiobook again.'
            }), 404
        
        # Upload magnet to AllDebrid
        magnet_id = alldebrid_client.upload_magnet(magnet_url)
        
        if not magnet_id:
            logger.error("Failed to upload magnet to AllDebrid")
            return jsonify({
                'error': 'Failed to upload',
                'message': 'Could not upload audiobook to AllDebrid.'
            }), 500
        
        # Check status immediately - NO WAITING
        logger.info(f"Checking magnet {magnet_id} status (no wait)...")
        status = alldebrid_client.get_magnet_status(magnet_id)
        
        if not status:
            logger.error(f"Could not get status for magnet {magnet_id}")
            return jsonify({
                'error': 'Status unavailable',
                'message': 'Could not check download status.'
            }), 500
        
        # Only proceed if magnet is ready (statusCode 4)
        if status['statusCode'] != 4:
            logger.info(f"Magnet {magnet_id} not ready yet: {status['status']} (code {status['statusCode']})")
            return jsonify({
                'error': 'Not ready',
                'message': f"This audiobook is not cached yet. Status: {status['status']}. Try again in a few minutes or choose another result."
            }), 503
        
        logger.info(f"Magnet {magnet_id} is ready!")
        
        # Get file list
        files = alldebrid_client.get_magnet_files(magnet_id)
        
        if not files:
            logger.error(f"No files found for magnet {magnet_id}")
            return jsonify({'error': 'No audio files found'}), 404
        
        # Filter and sort audio files
        audio_extensions = ['.mp3', '.m4a', '.m4b', '.aac', '.ogg', '.flac']
        audio_files = [
            f for f in files 
            if any(f['name'].lower().endswith(ext) for ext in audio_extensions)
        ]
        
        if not audio_files:
            logger.error(f"No audio files found in magnet {magnet_id}")
            return jsonify({'error': 'No audio files found in torrent'}), 404
        
        # Sort files by name (natural ordering for "001", "002", etc.)
        audio_files.sort(key=lambda f: f['name'])
        
        # Get album metadata from cache
        album_metadata = get_album_metadata(album_id)
        
        if not album_metadata:
            # Fallback if metadata not found
            album_metadata = {
                'title': f"Audiobook {album_id[:8]}",
                'artist': "Unknown",
                'track_count': len(audio_files)
            }
        
        # Convert to Eclipse tracks format (audiobooks need streamURL unlike music)
        tracks = []
        base_url = f"https://{request.host}"
        
        for idx, file in enumerate(audio_files, 1):
            # Create unique track ID from file link
            track_id = hashlib.md5(file['link'].encode()).hexdigest()
            
            # Cache track file info with AllDebrid link (will be unlocked on-demand via /stream)
            cache_track_file(
                track_id=track_id,
                album_id=album_id,
                file_link=file['link'],
                filename=file['name'].split('/')[-1]
            )
            
            # Estimate duration (rough: 1MB ≈ 60 seconds for 128kbps)
            estimated_duration = int((file['size'] / 1024 / 1024) * 60) if file['size'] > 0 else 0
            
            # Detect file format
            file_format = 'mp3'  # default
            name_lower = file['name'].lower()
            if name_lower.endswith('.m4b'):
                file_format = 'm4b'
            elif name_lower.endswith('.m4a'):
                file_format = 'm4a'
            elif name_lower.endswith('.mp3'):
                file_format = 'mp3'
            
            # Create track with audiobook format - WITH streamURL and format
            track = {
                'id': track_id,
                'title': file['name'].split('/')[-1],  # Just the filename
                'artist': album_metadata['artist'],
                'album': album_metadata['title'],  # Add album name for playlists
                'albumId': album_id,  # Add album reference
                'duration': estimated_duration if estimated_duration > 0 else None,
                'artworkURL': album_metadata.get('artwork_url'),  # Use album artwork
                'streamURL': f"{base_url}/{token}/stream/{track_id}.{file_format}",
                'format': file_format
            }
            
            tracks.append(track)
        
        logger.info(f"Returning {len(tracks)} tracks for album: {album_id}")
        
        # Return full album format (audiobook style with description)
        return jsonify({
            'id': album_id,
            'title': album_metadata['title'],
            'artist': album_metadata['artist'],
            'artworkURL': album_metadata.get('artwork_url'),
            'year': None,
            'description': album_metadata.get('description', ''),
            'trackCount': len(tracks),
            'tracks': tracks
        })

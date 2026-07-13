"""
Stream route for Eclipse Music addon
"""

from flask import jsonify, request, redirect, Response
from helpers import validate_token, get_cached_magnet, cache_magnet_status, get_track_magnet, get_track_file, cache_track_file
import helpers
import logging
import hashlib
import requests

logger = logging.getLogger(__name__)


def register_routes(app, api_key, alldebrid_client, streaming_session):
    """Register stream route"""
    
    @app.route('/<token>/stream/<path:track_id>', methods=['GET', 'HEAD', 'OPTIONS'])
    def stream(token, track_id):
        """
        Stream audiobook via AllDebrid with proper proxy support
        Handles HEAD, OPTIONS, and Range requests for iOS/Android/Windows compatibility
        Supports extensions like .mp3, .m4b, etc.
        """
        if not validate_token(token, api_key):
            return jsonify({'error': 'Unauthorized'}), 401
        
        # Handle OPTIONS preflight for CORS
        if request.method == 'OPTIONS':
            return Response(status=200, headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
                'Access-Control-Allow-Headers': '*'
            })
        
        # Remove file extension if present (e.g., "abc123.mp3" -> "abc123")
        track_id_clean = track_id.rsplit('.', 1)[0] if '.' in track_id else track_id
        
        # Log detailed request information
        user_agent = request.headers.get('User-Agent', 'Unknown')
        range_header = request.headers.get('Range', 'None')
        referer = request.headers.get('Referer', 'None')
        
        logger.info(f"Stream: {track_id_clean} | {request.method} | {user_agent[:40]}")
        if range_header != 'None':
            logger.debug(f"Range: {range_header}")

        
        # First, check if we have a cached file link for this track
        track_file = get_track_file(track_id_clean)
        
        if track_file:
            logger.debug(f"Cache hit: {track_file.get('filename', 'unknown')}")
            
            # Unlock the AllDebrid link on-demand to get fresh direct link
            unlocked_link = alldebrid_client.unlock_link(track_file['file_link'])
            
            if unlocked_link:
                logger.info(f"✓ Track unlocked: {unlocked_link[:80]}...")
                
                # Detect file format from filename
                filename = track_file.get('filename', '').lower()
                file_format = 'mp3'  # default
                content_type = 'audio/mpeg'
                
                if filename.endswith('.m4b'):
                    file_format = 'm4b'
                    content_type = 'audio/x-m4b'
                elif filename.endswith('.m4a'):
                    file_format = 'm4a'
                    content_type = 'audio/mp4'
                elif filename.endswith('.mp3'):
                    file_format = 'mp3'
                    content_type = 'audio/mpeg'
                
                # Return direct AllDebrid URL (CORS works!)
                logger.info(f"→ Returning direct AllDebrid URL: {filename}")
                logger.debug(f"[Stream] File format detected: {file_format}, Content-Type: {content_type}")
                
                return jsonify({
                    'url': unlocked_link,
                    'format': file_format,
                    'filename': filename
                })
            else:
                logger.error(f"✗ Failed to unlock track: {track_id_clean}")
                return jsonify({'error': 'Failed to unlock stream'}), 500
        
        # Fallback: Try the old method with magnet URL
        logger.warning(f"⚠ No cached file link for track: {track_id_clean}")
        logger.info(f"→ Trying fallback: check if we have album_id in expired cache...")
        
        # Check if we have album info in the track cache (even if expired)
        if track_id_clean in helpers._track_file_cache:
            cached_track = helpers._track_file_cache[track_id_clean]
            album_id = cached_track.get('album_id')
            old_filename = cached_track.get('filename', 'unknown')
            
            logger.info(f"→ Found album_id {album_id} for track (file: {old_filename})")
            
            # Try to get magnet URL for this album
            magnet_url = get_track_magnet(album_id)
            
            if magnet_url:
                logger.info(f"→ Found magnet URL for album {album_id}, re-uploading...")
                
                # Re-upload magnet to AllDebrid
                magnet_id = alldebrid_client.upload_magnet(magnet_url)
                
                if magnet_id:
                    # Check if magnet is ready
                    status = alldebrid_client.get_magnet_status(magnet_id)
                    
                    if status and status['statusCode'] == 4:
                        logger.info(f"→ Magnet {magnet_id} is ready, finding track file...")
                        
                        # Get all files
                        files = alldebrid_client.get_magnet_files(magnet_id)
                        
                        # Find the exact file by matching filename
                        matching_file = None
                        for f in files:
                            file_name = f['name'].split('/')[-1]
                            if file_name == old_filename:
                                matching_file = f
                                break
                        
                        if matching_file:
                            logger.info(f"→ Found matching file: {matching_file['name']}")
                            
                            # Update cache with fresh link
                            cache_track_file(
                                track_id=track_id_clean,
                                album_id=album_id,
                                file_link=matching_file['link'],
                                filename=old_filename
                            )
                            
                            logger.info(f"→ Cache refreshed, redirecting to stream again...")
                            # Redirect to self to use the fresh cache
                            return redirect(request.url)
                        else:
                            logger.warning(f"⚠ Could not find file {old_filename} in magnet")
                    else:
                        logger.warning(f"⚠ Magnet not ready: {status['status'] if status else 'unknown'}")
                else:
                    logger.error(f"✗ Failed to re-upload magnet")
            else:
                logger.warning(f"⚠ No magnet URL found for album {album_id}")
        
        logger.info(f"→ Trying legacy fallback magnet method...")
        
        # Legacy fallback: Get magnet URL from cache (stored during search)
        magnet_url = get_track_magnet(track_id_clean)
        
        # Fallback: check query parameter (for manual testing)
        if not magnet_url:
            magnet_url = request.args.get('magnet')
        
        if not magnet_url:
            logger.error(f"No magnet URL found for track: {track_id_clean}")
            return jsonify({
                'error': 'Track not found',
                'message': 'Please search for the audiobook again.'
            }), 404
        
        # Generate hash for caching
        magnet_hash = hashlib.md5(magnet_url.encode()).hexdigest()
        
        # Check cache first and return direct URL
        cached = get_cached_magnet(magnet_hash)
        if cached and cached.get('url'):
            logger.info(f"Cache hit for magnet: {magnet_hash}")
            logger.info(f"→ Returning cached direct URL: {cached.get('filename', 'unknown')}")
            return jsonify(cached)
        
        # Step 1: Upload magnet to AllDebrid
        magnet_id = alldebrid_client.upload_magnet(magnet_url)
        
        if not magnet_id:
            logger.error("Failed to upload magnet to AllDebrid")
            return jsonify({'error': 'Failed to upload magnet'}), 500
        
        # Step 2: Check status immediately - NO WAITING
        logger.info(f"Checking magnet {magnet_id} status (no wait)...")
        status = alldebrid_client.get_magnet_status(magnet_id)
        
        if not status or status['statusCode'] != 4:
            logger.error(f"Magnet {magnet_id} not ready: {status['status'] if status else 'unknown'}")
            return jsonify({
                'error': 'Not ready',
                'message': 'This audiobook is not cached yet. Please try again later.'
            }), 503
        
        logger.info(f"Magnet {magnet_id} is ready!")
        
        # Step 3: Get file list
        files = alldebrid_client.get_magnet_files(magnet_id)
        
        if not files:
            logger.error(f"No files found for magnet {magnet_id}")
            alldebrid_client.delete_magnet(magnet_id)
            return jsonify({'error': 'No audio files found'}), 404
        
        # Step 4: Select best audio file
        best_file = alldebrid_client.select_best_audio_file(files)
        
        if not best_file:
            logger.error(f"No audio file found in magnet {magnet_id}")
            alldebrid_client.delete_magnet(magnet_id)
            return jsonify({'error': 'No audio files found in torrent'}), 404
        
        # Step 5: Return direct URL (no proxy needed)
        stream_url = best_file['link']
        filename = best_file['name']
        file_format = 'mp3'  # default
        
        if filename.lower().endswith('.m4b'):
            file_format = 'm4b'
        elif filename.lower().endswith('.m4a'):
            file_format = 'm4a'
        elif filename.lower().endswith('.mp3'):
            file_format = 'mp3'
        
        # Cache the file link for next time
        cache_track_file(
            track_id=track_id_clean,
            album_id=track_id_clean,  # Use track_id as album_id for legacy entries
            file_link=stream_url,
            filename=filename
        )
        
        response = {
            'url': stream_url,
            'format': file_format,
            'quality': 'audiobook',
            'filename': filename
        }
        
        # Cache the result
        cache_magnet_status(magnet_hash, response)
        
        logger.info(f"→ Returning direct URL for: {filename}")
        
        # Note: We could delete the magnet after streaming starts to free up AllDebrid quota
        # alldebrid_client.delete_magnet(magnet_id)
        
        return jsonify(response)

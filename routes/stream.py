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


def proxy_audio_stream(url, filename, content_type, streaming_session):
    """
    Proxy audio content from AllDebrid with Range request support
    Used for streaming (not downloading)
    """
    try:
        # Get Range header from client request
        range_header = request.headers.get('Range')
        headers = {}
        
        if range_header:
            headers['Range'] = range_header
            logger.debug(f"[Proxy] Forwarding Range request: {range_header}")
        
        # Make request to AllDebrid
        response = streaming_session.get(url, headers=headers, stream=True, timeout=30)
        
        # Build response headers
        response_headers = {
            'Content-Type': content_type,
            'Accept-Ranges': 'bytes',
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'no-cache',
        }
        
        # Forward relevant headers from AllDebrid
        if 'Content-Length' in response.headers:
            response_headers['Content-Length'] = response.headers['Content-Length']
        
        if 'Content-Range' in response.headers:
            response_headers['Content-Range'] = response.headers['Content-Range']
        
        # Set status code (206 for partial content, 200 for full)
        status_code = response.status_code
        
        logger.info(f"[Proxy] Streaming {filename} | Status: {status_code} | Range: {range_header or 'Full'}")
        
        # Stream the response
        def generate():
            try:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
            except Exception as e:
                logger.error(f"[Proxy] Error streaming: {e}")
                raise
        
        return Response(generate(), status=status_code, headers=response_headers)
        
    except Exception as e:
        logger.error(f"[Proxy] Failed to proxy stream: {e}")
        return jsonify({'error': 'Stream proxy failed'}), 500


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
                
                logger.debug(f"[Stream] File format detected: {file_format}, Content-Type: {content_type}")
                
                # Detect if this is a streaming request
                # Method 1: Check for Range header (standard for streaming)
                has_range = request.headers.get('Range') is not None
                
                # Method 2: Check User-Agent for known audio players
                user_agent = request.headers.get('User-Agent', '').lower()
                is_audio_player = any(player in user_agent for player in [
                    'eclipse-android',      # Android Eclipse Music
                    'vlc',
                    'media',
                    'audio',
                    'player'
                ])
                
                # Method 3: Exclude known download clients
                is_downloader = 'okhttp' in user_agent and not is_audio_player
                
                is_streaming = (has_range or is_audio_player) and not is_downloader
                
                if is_streaming:
                    # STREAMING MODE: Proxy the audio content
                    logger.info(f"→ [STREAMING] Proxying audio content: {filename}")
                    return proxy_audio_stream(unlocked_link, filename, content_type, streaming_session)
                else:
                    # DOWNLOAD MODE: Return direct URL
                    logger.info(f"→ [DOWNLOAD] Returning direct AllDebrid URL: {filename}")
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
        
        # Detect if this is a streaming request
        # Method 1: Check for Range header (standard for streaming)
        has_range = request.headers.get('Range') is not None
        
        # Method 2: Check User-Agent for known audio players
        user_agent = request.headers.get('User-Agent', '').lower()
        is_audio_player = any(player in user_agent for player in [
            'eclipse-android',      # Android Eclipse Music
            'vlc',
            'media',
            'audio',
            'player'
        ])
        
        # Method 3: Exclude known download clients
        is_downloader = 'okhttp' in user_agent and not is_audio_player
        
        is_streaming = (has_range or is_audio_player) and not is_downloader
        
        if is_streaming:
            # STREAMING MODE: Proxy the audio content
            logger.info(f"→ [STREAMING] Proxying audio content: {filename}")
            # Determine content type from format
            content_types = {
                'mp3': 'audio/mpeg',
                'm4b': 'audio/x-m4b',
                'm4a': 'audio/mp4'
            }
            content_type = content_types.get(file_format, 'audio/mpeg')
            return proxy_audio_stream(stream_url, filename, content_type, streaming_session)
        else:
            # DOWNLOAD MODE: Return direct URL
            logger.info(f"→ [DOWNLOAD] Returning direct URL for: {filename}")
            # Note: We could delete the magnet after streaming starts to free up AllDebrid quota
            # alldebrid_client.delete_magnet(magnet_id)
            return jsonify(response)

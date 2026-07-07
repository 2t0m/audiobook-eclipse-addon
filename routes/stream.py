"""
Stream route for Eclipse Music addon
"""

from flask import jsonify, request, redirect, Response
from helpers import validate_token, get_cached_magnet, cache_magnet_status, get_track_magnet, get_track_file
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

                
                # Get Content-Length from AllDebrid for range support
                content_length = None
                try:
                    head_response = streaming_session.head(unlocked_link, timeout=2)
                    if head_response.status_code == 200:
                        content_length = head_response.headers.get('Content-Length')
                        if content_length:
                            logger.debug(f"Content-Length: {content_length} bytes")
                except Exception as e:
                    logger.debug(f"Could not get Content-Length: {e}")
                
                # Detect file format from filename
                filename = track_file.get('filename', '').lower()
                content_type = 'audio/mpeg'  # default
                if filename.endswith('.m4b'):
                    content_type = 'audio/m4b'
                elif filename.endswith('.m4a'):
                    content_type = 'audio/mp4'
                elif filename.endswith('.mp3'):
                    content_type = 'audio/mpeg'
                
                # Handle HEAD request (iOS checks file existence/size)
                if request.method == 'HEAD':
                    logger.debug(f"HEAD request - returning headers only")
                    headers = {
                        'Content-Type': content_type,
                        'Accept-Ranges': 'bytes',
                        'Cache-Control': 'public, max-age=3600',
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Expose-Headers': 'Content-Length, Content-Range'
                    }
                    if content_length:
                        headers['Content-Length'] = content_length
                    return Response(status=200, headers=headers)
                
                # Parse Range header for iOS/Android seeking
                range_header = request.headers.get('Range')
                start_byte = 0
                end_byte = None
                is_range_request = False
                
                if range_header and content_length:
                    is_range_request = True
                    try:
                        range_str = range_header.replace('bytes=', '')
                        if '-' in range_str:
                            parts = range_str.split('-')
                            start_byte = int(parts[0]) if parts[0] else 0
                            end_byte = int(parts[1]) if parts[1] else int(content_length) - 1
                        else:
                            start_byte = int(range_str)
                            end_byte = int(content_length) - 1
                        
                        end_byte = min(end_byte, int(content_length) - 1)
                        logger.debug(f"Range request: bytes {start_byte}-{end_byte}/{content_length}")
                    except Exception as e:
                        logger.debug(f"Failed to parse Range: {e}")
                        is_range_request = False
                        start_byte = 0
                        end_byte = None
                
                # Build response headers
                headers = {
                    'Content-Type': content_type,
                    'Accept-Ranges': 'bytes',
                    'Cache-Control': 'public, max-age=3600',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Expose-Headers': 'Content-Length, Content-Range'
                }
                
                # Handle ICY metadata request (Eclipse-Android with Icy-Metadata: 1)
                if request.headers.get('Icy-Metadata') == '1':
                    headers['icy-metaint'] = '0'  # Tell Eclipse: no ICY metadata in this stream
                    logger.info("→ ICY metadata requested, responding with icy-metaint: 0")
                
                # Determine status code and prepare parameters
                status_code = 200
                
                if is_range_request:
                    status_code = 206
                    range_length = end_byte - start_byte + 1
                    headers['Content-Length'] = str(range_length)
                    headers['Content-Range'] = f'bytes {start_byte}-{end_byte}/{content_length}'
                    logger.info(f"Streaming track {track_id_clean} (range: {start_byte}-{end_byte})")
                else:
                    if content_length:
                        headers['Content-Length'] = content_length
                    logger.info(f"Streaming track {track_id_clean} (full): {filename}")
                
                # Generator - must capture variables for closure
                def generate():
                    """Stream chunks from AllDebrid"""
                    stream_response = None
                    try:
                        # Build headers
                        req_headers = {}
                        if is_range_request:
                            req_headers = {'Range': f'bytes={start_byte}-{end_byte}'}
                        
                        # Make the HTTP request INSIDE the generator
                        stream_response = streaming_session.get(
                            unlocked_link, 
                            headers=req_headers if req_headers else None, 
                            stream=True, 
                            timeout=30
                        )
                        
                        # Check status
                        if stream_response.status_code not in [200, 206]:
                            logger.error(f"✗ AllDebrid returned status {stream_response.status_code}")
                            return
                        
                        # Stream chunks (1MB for faster transfer)
                        chunk_count = 0
                        total_bytes = 0
                        for chunk in stream_response.iter_content(chunk_size=1048576):  # 1MB chunks for speed
                            if chunk:
                                chunk_count += 1
                                total_bytes += len(chunk)
                                yield chunk
                        
                        logger.info(f"✓ Streamed {total_bytes / 1024 / 1024:.1f} MB ({chunk_count} chunks)")
                        
                    except GeneratorExit:
                        logger.warning(f"⚠ Generator exit for track {track_id_clean} (client disconnected?)")
                    except Exception as e:
                        logger.error(f"✗ Streaming error for track {track_id_clean}: {e}", exc_info=True)
                    finally:
                        if stream_response:
                            try:
                                stream_response.close()
                                logger.debug(f"→ Stream response closed for track {track_id_clean}")
                            except:
                                pass
                
                logger.debug(f"Returning stream (status {status_code})")
                return Response(generate(), status=status_code, headers=headers)
            else:
                logger.error(f"✗ Failed to unlock track: {track_id_clean}")
                return jsonify({'error': 'Failed to unlock stream'}), 500
        
        # Fallback: Try the old method with magnet URL
        logger.warning(f"⚠ No cached file link for track: {track_id_clean}")
        logger.info(f"→ Trying fallback magnet method...")
        
        # Get magnet URL from cache (stored during search)
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
        
        # Check cache first
        cached = get_cached_magnet(magnet_hash)
        if cached and cached.get('url'):
            logger.info(f"Cache hit for magnet: {magnet_hash}")
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
        
        # Step 5: Return stream URL
        stream_url = best_file['link']
        file_format = 'mp3'  # default
        
        if best_file['name'].lower().endswith('.m4b'):
            file_format = 'm4b'
        elif best_file['name'].lower().endswith('.m4a'):
            file_format = 'm4a'
        
        response = {
            'url': stream_url,
            'format': file_format,
            'quality': 'audiobook',
            'filename': best_file['name']
        }
        
        # Cache the result
        cache_magnet_status(magnet_hash, response)
        
        logger.info(f"Returning stream URL for: {best_file['name']}")
        
        # Note: We could delete the magnet after streaming starts to free up AllDebrid quota
        # alldebrid_client.delete_magnet(magnet_id)
        
        return jsonify(response)

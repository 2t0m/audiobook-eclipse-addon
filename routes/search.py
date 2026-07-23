"""
Search route for Eclipse Music addon
"""

from flask import jsonify, request
from helpers import validate_token, cache_track_magnet, cache_album_metadata
import logging
import concurrent.futures

logger = logging.getLogger(__name__)


def register_routes(app, api_key, torznab_client, tr4ker_client, alldebrid_client, audible_client):
    """Register search route"""
    
    @app.route('/<token>/search')
    def search(token):
        """
        Search for audiobooks via c411 and TR4KER Torznab (parallel), enriched with Audible metadata
        Query parameter: q (search query)
        """
        if not validate_token(token, api_key):
            return jsonify({'error': 'Unauthorized'}), 401
        
        query = request.args.get('q', '').strip()
        
        if not query:
            return jsonify({'tracks': [], 'albums': []})
        
        logger.info(f"Search request: {query}")
        
        # Search both c411 and TR4KER in parallel
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            # Submit both searches
            c411_future = executor.submit(torznab_client.search_audiobooks, query, 50)
            tr4ker_future = executor.submit(tr4ker_client.search_audiobooks, query, 50)
            
            # Collect results
            try:
                c411_results = c411_future.result(timeout=15)
                logger.info(f"c411 returned {len(c411_results)} results")
                for result in c411_results:
                    result['source'] = 'c411'
                results.extend(c411_results)
            except Exception as e:
                logger.error(f"c411 search failed: {e}")
            
            try:
                tr4ker_results = tr4ker_future.result(timeout=15)
                logger.info(f"TR4KER returned {len(tr4ker_results)} results")
                results.extend(tr4ker_results)
            except Exception as e:
                logger.error(f"TR4KER search failed: {e}")
        
        # Deduplicate by infohash (from magnet URL)
        seen_hashes = set()
        unique_results = []
        for result in results:
            magnet = result.get('magnet', '')
            # Extract infohash from magnet URL
            if 'btih:' in magnet:
                infohash = magnet.split('btih:')[1].split('&')[0].lower()
                if infohash not in seen_hashes:
                    seen_hashes.add(infohash)
                    unique_results.append(result)
        
        logger.info(f"After deduplication: {len(unique_results)} unique results")
        
        # Sort by seeders (descending)
        unique_results.sort(key=lambda x: x.get('seeders', 0), reverse=True)
        
        # Convert to Eclipse format - return all albums (availability checked on album open)
        albums = []
        
        for result in unique_results[:20]:  # Limit to 20 results
            album_id = result['guid']  # Use torrent hash as album ID
            magnet_url = result['magnet']
            source = result.get('source', 'unknown')
            
            # Store album_id -> magnet mapping for album endpoint
            cache_track_magnet(album_id, magnet_url)
            
            # Enrich with Audible metadata
            audible_metadata = audible_client.search_audiobook(result['title'], result['author'])
            
            # Use Audible data if available, fallback to torrent data
            if audible_metadata:
                title = audible_metadata.get('title') or result['title']
                author = audible_metadata.get('author') or result['author']
                artwork_url = audible_metadata.get('artwork_url')
                description = audible_metadata.get('description', '')
                narrator = audible_metadata.get('narrator', '')
                
                # Add format info to description
                if description:
                    description = f"{description}\n\n{result['format'].upper()} • {result['size'] / 1024 / 1024:.0f} MB • {source.upper()}"
                    if narrator:
                        description = f"Narrated by {narrator}\n\n{description}"
                else:
                    description = f"{result['format'].upper()} • {result['size'] / 1024 / 1024:.0f} MB • {source.upper()}"
            else:
                # Fallback to torrent data
                title = result['title']
                author = result['author']
                artwork_url = None
                description = f"{result['format'].upper()} • {result['size'] / 1024 / 1024:.0f} MB • {source.upper()}"
            
            # Cache album metadata
            cache_album_metadata(
                album_id,
                title=title,
                artist=author,
                track_count=0,  # Will be determined when album is opened
                description=description,
                artwork_url=artwork_url
            )
            
            # Create album entry
            album = {
                'id': album_id,
                'title': title,
                'artist': author,
                'artworkURL': artwork_url,
                'description': description
            }
            
            albums.append(album)
        
        logger.info(f"Returning {len(albums)} albums from combined search (c411 + TR4KER)")
        
        return jsonify({
            'tracks': [],  # No individual tracks in search, only albums
            'albums': albums
        })

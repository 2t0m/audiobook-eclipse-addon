"""
Search route for Eclipse Music addon
"""

from flask import jsonify, request
from helpers import validate_token, cache_track_magnet, cache_album_metadata
import logging

logger = logging.getLogger(__name__)


def register_routes(app, api_key, torznab_client, alldebrid_client, audible_client):
    """Register search route"""
    
    @app.route('/<token>/search')
    def search(token):
        """
        Search for audiobooks via c411 Torznab, enriched with Audible metadata
        Query parameter: q (search query)
        """
        if not validate_token(token, api_key):
            return jsonify({'error': 'Unauthorized'}), 401
        
        query = request.args.get('q', '').strip()
        
        if not query:
            return jsonify({'tracks': [], 'albums': []})
        
        logger.info(f"Search request: {query}")
        
        # Search c411 via Torznab
        results = torznab_client.search_audiobooks(query, limit=50)
        
        # Convert to Eclipse format - return all albums (availability checked on album open)
        albums = []
        
        for result in results[:20]:  # Limit to 20 results
            album_id = result['guid']  # Use torrent hash as album ID
            magnet_url = result['magnet']
            
            # Store album_id -> magnet mapping for album endpoint
            cache_track_magnet(album_id, magnet_url)
            
            # Enrich with Audible metadata
            audible_metadata = audible_client.search_audiobook(result['title'], result['author'])
            
            # Use Audible data if available, fallback to c411 data
            if audible_metadata:
                title = audible_metadata.get('title') or result['title']
                author = audible_metadata.get('author') or result['author']
                artwork_url = audible_metadata.get('artwork_url')
                description = audible_metadata.get('description', '')
                narrator = audible_metadata.get('narrator', '')
                
                # Add format info to description
                if description:
                    description = f"{description}\n\n{result['format'].upper()} • {result['size'] / 1024 / 1024:.0f} MB"
                    if narrator:
                        description = f"Narrated by {narrator}\n\n{description}"
                else:
                    description = f"{result['format'].upper()} • {result['size'] / 1024 / 1024:.0f} MB"
            else:
                # Fallback to c411 data
                title = result['title']
                author = result['author']
                artwork_url = None
                description = f"{result['format'].upper()} • {result['size'] / 1024 / 1024:.0f} MB"
            
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
        
        logger.info(f"Returning {len(albums)} albums from search")
        
        return jsonify({
            'tracks': [],  # No individual tracks in search, only albums
            'albums': albums
        })

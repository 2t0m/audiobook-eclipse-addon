"""
Helper functions for audiobook Eclipse addon
"""

import os
import time
import logging
import xmltodict
import requests
import unicodedata
import json
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Persistent cache file path
CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')
TRACK_CACHE_FILE = os.path.join(CACHE_DIR, 'track_file_cache.json')

# Ensure cache directory exists
os.makedirs(CACHE_DIR, exist_ok=True)

# In-memory caches with TTL
_search_cache = {}
_magnet_cache = {}
_track_magnet_cache = {}  # Maps track_id -> magnet_url
_album_metadata_cache = {}  # Maps album_id -> {title, artist, track_count}
_track_file_cache = {}  # Maps track_id -> {album_id, file_link, filename, timestamp}
CACHE_TTL = 3600  # 1 hour
STREAM_CACHE_TTL = 14400  # 4 hours for AllDebrid links
PERSISTENT_CACHE_TTL = 30 * 24 * 3600  # 30 days for persistent track cache


def _load_persistent_cache():
    """Load track file cache from disk"""
    global _track_file_cache
    try:
        if os.path.exists(TRACK_CACHE_FILE):
            with open(TRACK_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Convert timestamp strings back to datetime objects
                for track_id, info in data.items():
                    if 'timestamp' in info and isinstance(info['timestamp'], str):
                        info['timestamp'] = datetime.fromisoformat(info['timestamp'])
                _track_file_cache = data
                print(f"✓ Loaded {len(_track_file_cache)} tracks from persistent cache", flush=True)
                logger.info(f"Loaded {len(_track_file_cache)} tracks from persistent cache")
        else:
            print(f"✓ No persistent cache file found at {TRACK_CACHE_FILE}", flush=True)
            logger.info(f"No persistent cache file found")
    except Exception as e:
        print(f"✗ Failed to load persistent cache: {e}", flush=True)
        logger.error(f"Failed to load persistent cache: {e}")
        _track_file_cache = {}


def _save_persistent_cache():
    """Save track file cache to disk"""
    try:
        # Convert datetime objects to ISO format strings for JSON serialization
        data = {}
        for track_id, info in _track_file_cache.items():
            data[track_id] = {
                'album_id': info['album_id'],
                'file_link': info['file_link'],
                'filename': info['filename'],
                'timestamp': info['timestamp'].isoformat() if isinstance(info['timestamp'], datetime) else info['timestamp']
            }
        
        with open(TRACK_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        logger.debug(f"Saved {len(data)} tracks to persistent cache")
    except Exception as e:
        logger.error(f"Failed to save persistent cache: {e}")


# Load cache on module import
_load_persistent_cache()


def validate_token(token: str, api_key: str) -> bool:
    """Validate Eclipse API token"""
    if not api_key:
        return True  # No validation if API key not set
    
    is_valid = token == api_key
    if not is_valid:
        logger.warning(f"Invalid token attempt: {token}")
    return is_valid


def get_cached_search(query: str) -> Optional[List[Dict]]:
    """Get cached search results (DISABLED FOR DEBUG)"""
    # Cache disabled for debugging
    return None


def cache_search_results(query: str, results: List[Dict]):
    """Cache search results with timestamp"""
    _search_cache[query] = (results, datetime.now())


def get_cached_magnet(magnet_hash: str) -> Optional[Dict]:
    """Get cached magnet status (DISABLED FOR DEBUG)"""
    # Cache disabled for debugging
    return None


def cache_magnet_status(magnet_hash: str, data: Dict):
    """Cache magnet status"""
    _magnet_cache[magnet_hash] = (data, datetime.now())


def cache_track_magnet(track_id: str, magnet_url: str):
    """Store track_id -> magnet_url mapping"""
    _track_magnet_cache[track_id] = (magnet_url, datetime.now())


def get_track_magnet(track_id: str) -> Optional[str]:
    """Get magnet URL from cache (DISABLED FOR DEBUG)"""
    # Cache disabled for debugging
    if track_id in _track_magnet_cache:
        magnet_url, timestamp = _track_magnet_cache[track_id]
        return magnet_url
    return None


def cache_album_metadata(album_id: str, title: str, artist: str, track_count: int, description: str = '', artwork_url: str = None):
    """Store album metadata"""
    _album_metadata_cache[album_id] = {
        'title': title,
        'artist': artist,
        'track_count': track_count,
        'description': description,
        'artwork_url': artwork_url,
        'timestamp': datetime.now()
    }


def get_album_metadata(album_id: str) -> Optional[Dict]:
    """Get album metadata from cache (DISABLED FOR DEBUG)"""
    # Cache disabled for debugging
    if album_id in _album_metadata_cache:
        return _album_metadata_cache[album_id]
    return None


def cache_track_file(track_id: str, album_id: str, file_link: str, filename: str):
    """Store track_id -> (album_id, file_link, filename) mapping persistently"""
    _track_file_cache[track_id] = {
        'album_id': album_id,
        'file_link': file_link,
        'filename': filename,
        'timestamp': datetime.now()
    }
    # Save to disk immediately
    _save_persistent_cache()


def get_track_file(track_id: str) -> Optional[Dict]:
    """Get track file info from cache with expiration check"""
    if track_id in _track_file_cache:
        cached_data = _track_file_cache[track_id]
        timestamp = cached_data.get('timestamp')
        
        # Check if cache entry is still valid (30 days)
        if timestamp and isinstance(timestamp, datetime):
            age = (datetime.now() - timestamp).total_seconds()
            if age < PERSISTENT_CACHE_TTL:
                return cached_data
            else:
                # Remove expired entry
                logger.debug(f"Cache entry expired for track: {track_id}")
                del _track_file_cache[track_id]
                _save_persistent_cache()
        else:
            # Entry without timestamp is still valid (for backward compatibility)
            return cached_data
    
    return None


class AudibleClient:
    """Client for enriching audiobook metadata using Audible Public Catalog API"""
    
    def __init__(self, session: requests.Session):
        self.session = session
        # Use Audible public catalog API (French version for better coverage)
        self.base_url = "https://api.audible.fr/1.0/catalog/products"
    
    @staticmethod
    def normalize_for_search(text: str) -> str:
        """Normalize text for better search matching (remove accents, replace œ)"""
        if not text:
            return text
        # Replace œ with oe before removing accents
        text = text.replace('œ', 'oe').replace('Œ', 'OE')
        # Remove accents: é→e, à→a, etc.
        nfd = unicodedata.normalize('NFD', text)
        without_accents = ''.join(char for char in nfd if unicodedata.category(char) != 'Mn')
        return without_accents
    
    def search_audiobook(self, title: str, author: str = None) -> Optional[Dict]:
        """
        Search for an audiobook metadata using Audible Catalog API
        Returns: {title, author, narrator, artwork_url, description, release_date, runtime_length_min}
        """
        try:
            # Normalize title and author for better matching
            normalized_title = self.normalize_for_search(title)
            normalized_author = self.normalize_for_search(author) if author else None
            
            # Build search query
            if normalized_author:
                search_query = f'{normalized_title} {normalized_author}'
            else:
                search_query = normalized_title
            
            params = {
                'response_groups': 'contributors,product_desc,product_attrs,series,media',
                'image_sizes': '500',
                'num_results': 10,
                'products_sort_by': 'Relevance',
                'keywords': search_query
            }
            
            logger.info(f"Searching Audible API for: {title} (normalized: {search_query})")
            
            response = self.session.get(
                self.base_url,
                params=params,
                timeout=8
            )
            
            if response.status_code != 200:
                logger.warning(f"Audible API returned status {response.status_code}")
                return None
            
            data = response.json()
            products = data.get('products', [])
            
            if not products:
                logger.info(f"✗ No Audible results for: {search_query}")
                return None
            
            # Take first result
            book = products[0]
            
            # Extract title
            book_title = book.get('title', title)
            
            # Extract authors
            authors = book.get('authors', [])
            author_names = [a.get('name', '') for a in authors if a.get('name')]
            author_str = ', '.join(author_names) if author_names else (author or '')
            
            # Extract narrators
            narrators = book.get('narrators', [])
            narrator_names = [n.get('name', '') for n in narrators if n.get('name')]
            narrator_str = ', '.join(narrator_names) if narrator_names else ''
            
            # Extract artwork from social_media_images
            artwork_url = None
            social_media_images = book.get('social_media_images', {})
            
            if social_media_images:
                # Try to extract image ID from any social media URL (they all contain the same ID)
                for key in ['facebook', 'twitter', 'ig_bg']:
                    if key in social_media_images:
                        url = social_media_images[key]
                        # Extract image ID: https://m.media-amazon.com/images/I/51uUZA2pixL._...
                        if '/images/I/' in url:
                            image_id = url.split('/images/I/')[1].split('.')[0]
                            artwork_url = f"https://m.media-amazon.com/images/I/{image_id}._SL500_.jpg"
                            logger.info(f"✓ Artwork extracted: {image_id}")
                            break
            
            if not artwork_url:
                logger.warning(f"✗ No artwork for: {book_title}")
            
            # Extract description
            description = book.get('publisher_summary', '')
            
            # Extract release date
            release_date = book.get('release_date', '')
            if release_date:
                # Format: "YYYY-MM-DD" -> extract year
                release_date = release_date.split('-')[0] if '-' in release_date else release_date
            
            # Extract runtime
            runtime_min = book.get('runtime_length_min', 0)
            
            result = {
                'title': book_title,
                'author': author_str,
                'narrator': narrator_str,
                'artwork_url': artwork_url,
                'description': description,
                'release_date': release_date,
                'runtime_length_min': runtime_min
            }
            
            logger.info(f"✓ Audible match: {book_title} by {author_str}")
            return result
            
        except Exception as e:
            logger.warning(f"Audible API search error: {e}")
            return None


class TorznabClient:
    """Client for c411 Torznab API"""
    
    def __init__(self, api_key: str, session: requests.Session):
        self.base_url = "https://c411.org/api"
        self.api_key = api_key
        self.session = session
    
    def search_audiobooks(self, query: str, limit: int = 50) -> List[Dict]:
        """
        Search for audiobooks on c411
        Returns list of results with: title, magnet, size, seeders, author
        """
        # Check cache first
        cached = get_cached_search(query)
        if cached is not None:
            return cached
        
        params = {
            't': 'search',
            'apikey': self.api_key,
            'q': query,
            'cat': '3030',  # Audiobook category
            'limit': limit,
            'extended': '1'
        }
        
        try:
            logger.info(f"Searching c411 for: {query}")
            response = self.session.get(self.base_url, params=params, timeout=10)
            response.raise_for_status()
            
            # Log raw response for debugging
            logger.debug(f"Raw c411 response: {response.text[:2000]}...")
            
            # Parse XML response
            data = xmltodict.parse(response.content)
            
            if 'rss' not in data or 'channel' not in data['rss']:
                logger.warning("Invalid Torznab response format")
                return []
            
            channel = data['rss']['channel']
            items = channel.get('item', [])
            
            # Handle single item (not a list)
            if isinstance(items, dict):
                items = [items]
            
            results = []
            for item in items:
                # Log raw item for debugging
                logger.debug(f"Raw item: {item.get('title', 'N/A')}")
                logger.debug(f"Item attributes: {item.get('torznab:attr', [])}")
                
                parsed = self._parse_torznab_item(item)
                if parsed:
                    results.append(parsed)
            
            logger.info(f"Found {len(results)} audiobooks for query: {query}")
            
            # Cache results
            cache_search_results(query, results)
            
            return results
        
        except requests.RequestException as e:
            logger.error(f"c411 search error: {e}")
            return []
        except Exception as e:
            logger.error(f"Torznab parsing error: {e}")
            return []
    
    def _parse_torznab_item(self, item: Dict) -> Optional[Dict]:
        """Parse a single Torznab XML item"""
        try:
            title = item.get('title', 'Unknown')
            guid = item.get('guid', {})
            
            if isinstance(guid, dict):
                guid = guid.get('#text', '')
            
            # Extract torznab attributes
            attrs = item.get('torznab:attr', [])
            if isinstance(attrs, dict):
                attrs = [attrs]
            
            attr_dict = {attr.get('@name'): attr.get('@value') for attr in attrs if '@name' in attr}
            
            # Get magnet URL or construct from infohash
            magnet_url = attr_dict.get('magneturl')
            if not magnet_url:
                infohash = attr_dict.get('infohash')
                if infohash:
                    magnet_url = f"magnet:?xt=urn:btih:{infohash}"
            
            if not magnet_url:
                logger.debug(f"No magnet URL for: {title}")
                return None
            
            # Extract metadata
            size = int(attr_dict.get('size', 0))
            seeders = int(attr_dict.get('seeders', 0))
            
            # Detect format from title
            format_type = 'mp3'  # default
            title_lower = title.lower()
            if '.m4b' in title_lower or 'm4b' in title_lower:
                format_type = 'm4b'
            elif '.m4a' in title_lower:
                format_type = 'm4a'
            
            # Extract author from title
            # Pattern c411: Titre.Mots.Prénom.Nom.YYYY.LANG.[Format]-TAG
            author = "Unknown Author"
            clean_title = title
            
            # Try c411 pattern first (most common)
            import re
            match = re.search(r'^(.+?)\.(\d{4})\.(FR|EN|fr|en|Es|es)', title, re.IGNORECASE)
            if match:
                # Everything before year
                before_year = match.group(1)
                parts = before_year.split('.')
                
                # Author is typically the last 2 words before year (Prénom Nom)
                # But can be 1 or 3 words (just Nom, or Prénom Deuxième Nom)
                if len(parts) >= 3:
                    # Try to extract author (last 2 words)
                    author_parts = parts[-2:]
                    author = ' '.join(author_parts).title()
                    
                    # Title is everything before author
                    title_parts = parts[:-2]
                    clean_title = ' '.join(title_parts).title()
                elif len(parts) >= 2:
                    # Fallback: last word is author
                    author = parts[-1].title()
                    clean_title = ' '.join(parts[:-1]).title()
            
            # Fallback to other patterns if c411 pattern didn't match
            elif ' - ' in title:
                parts = title.split(' - ', 1)
                author = parts[0].strip()
                clean_title = parts[1].strip()
            elif ' by ' in title.lower():
                parts = title.lower().split(' by ')
                if len(parts) == 2:
                    clean_title = parts[0].strip().title()
                    author = parts[1].strip().title()
            
            # Clean title (remove common tags and format info)
            for tag in ['[Audiobook]', '[Unabridged]', '[Abridged]', '[MP3]', '[M4B]', 
                       '(Audiobook)', '(Unabridged)', '(Abridged)', '-NOTAG', '-notag']:
                clean_title = clean_title.replace(tag, '').strip()
            
            # Remove format patterns like [MP3.128kbps], [Mp3.64Kbps], etc.
            clean_title = re.sub(r'\[.*?(mp3|m4b|m4a).*?\]', '', clean_title, flags=re.IGNORECASE).strip()
            
            title = clean_title
            
            # Estimate duration (rough: 1MB ≈ 60 seconds for 128kbps)
            estimated_duration = int((size / 1024 / 1024) * 60) if size > 0 else 0
            
            return {
                'guid': guid,
                'title': title,
                'author': author,
                'magnet': magnet_url,
                'size': size,
                'seeders': seeders,
                'format': format_type,
                'duration': estimated_duration
            }
        
        except Exception as e:
            logger.error(f"Error parsing item: {e}")
            return None


class AllDebridClient:
    """Client for AllDebridClient API"""
    
    def __init__(self, api_key: str, session: requests.Session):
        self.base_url = "https://api.alldebrid.com/v4"
        self.api_key = api_key
        self.session = session
        self.headers = {'Authorization': f'Bearer {api_key}'}
    
    def upload_magnet(self, magnet_url: str) -> Optional[int]:
        """
        Upload magnet to AllDebrid
        Returns magnet_id if successful
        """
        try:
            logger.info(f"Uploading magnet to AllDebrid: {magnet_url[:50]}...")
            
            response = self.session.post(
                f"{self.base_url}/magnet/upload",
                headers=self.headers,
                data={'magnets[]': magnet_url},
                timeout=30
            )
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('status') != 'success':
                error = data.get('error', {})
                logger.error(f"AllDebrid upload error: {error}")
                return None
            
            magnets = data.get('data', {}).get('magnets', [])
            if not magnets:
                logger.error("No magnets returned from AllDebrid")
                return None
            
            magnet_info = magnets[0]
            
            if 'error' in magnet_info:
                logger.error(f"Magnet error: {magnet_info['error']}")
                return None
            
            magnet_id = magnet_info.get('id')
            logger.info(f"Magnet uploaded successfully: ID {magnet_id}")
            return magnet_id
        
        except Exception as e:
            logger.error(f"Error uploading magnet: {e}")
            return None
    
    def get_magnet_status(self, magnet_id: int) -> Optional[Dict]:
        """
        Get magnet download status
        Returns dict with: statusCode, status, filename, size, ready
        """
        try:
            # Use v4.1 API endpoint
            response = self.session.post(
                "https://api.alldebrid.com/v4.1/magnet/status",
                headers=self.headers,
                data={'id': magnet_id},
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('status') != 'success':
                return None
            
            # v4.1 API returns magnets as a single object, not a list
            magnet_info = data.get('data', {}).get('magnets', {})
            
            if not magnet_info:
                return None
            
            return {
                'statusCode': magnet_info.get('statusCode', 0),
                'status': magnet_info.get('status', 'Unknown'),
                'filename': magnet_info.get('filename', ''),
                'size': magnet_info.get('size', 0),
                'ready': magnet_info.get('statusCode') == 4
            }
        
        except Exception as e:
            logger.error(f"Error getting magnet status: {e}")
            return None
    
    def wait_for_magnet(self, magnet_id: int, max_wait: int = 300) -> bool:
        """
        Poll magnet status until ready or timeout
        Returns True if ready, False if timeout or error
        """
        start_time = time.time()
        delay = 5  # Start with 5 seconds
        
        while time.time() - start_time < max_wait:
            status = self.get_magnet_status(magnet_id)
            
            if not status:
                logger.error("Failed to get magnet status")
                return False
            
            logger.info(f"Magnet {magnet_id} status: {status['status']} (code: {status['statusCode']})")
            
            # Status codes: 4 = Ready, 5-15 = Error states
            if status['statusCode'] == 4:
                logger.info(f"Magnet {magnet_id} ready!")
                return True
            elif status['statusCode'] >= 5:
                logger.error(f"Magnet {magnet_id} failed: {status['status']}")
                return False
            
            # Wait before next poll (exponential backoff, max 20s)
            time.sleep(delay)
            delay = min(delay * 1.5, 20)
        
        logger.error(f"Magnet {magnet_id} timeout after {max_wait}s")
        return False
    
    def get_magnet_files(self, magnet_id: int) -> List[Dict]:
        """
        Get file list from ready magnet
        Returns list of files with: name, size, link
        """
        try:
            # Use query param for API key like in reference code
            response = self.session.post(
                f"{self.base_url}/magnet/files?apikey={self.api_key.replace('Bearer ', '')}",
                headers=self.headers,
                data={'id[]': magnet_id},
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('status') != 'success':
                return []
            
            # v4.1 might return magnets as object or list, handle both
            magnets_data = data.get('data', {}).get('magnets', {})
            
            # If it's a dict (single magnet), use it directly
            if isinstance(magnets_data, dict):
                magnet_info = magnets_data
            # If it's a list, take first element
            elif isinstance(magnets_data, list) and magnets_data:
                magnet_info = magnets_data[0]
            else:
                return []
            
            files_tree = magnet_info.get('files', [])
            
            # Flatten file tree
            files = self._flatten_file_tree(files_tree)
            
            logger.info(f"Found {len(files)} files in magnet {magnet_id}")
            return files
        
        except Exception as e:
            logger.error(f"Error getting magnet files: {e}")
            return []
    
    def _flatten_file_tree(self, tree: List, prefix: str = '') -> List[Dict]:
        """Recursively flatten AllDebrid file tree"""
        files = []
        
        for node in tree:
            name = node.get('n', '')
            
            # Check if it's a folder (has 'e' key for entries)
            if 'e' in node:
                # Recursive call for subfolder
                subfolder_files = self._flatten_file_tree(node['e'], prefix + name + '/')
                files.extend(subfolder_files)
            else:
                # It's a file
                files.append({
                    'name': prefix + name,
                    'size': node.get('s', 0),
                    'link': node.get('l', '')
                })
        
        return files
    
    def select_best_audio_file(self, files: List[Dict]) -> Optional[Dict]:
        """
        Select the best audio file from a list
        For audiobooks:
        - Priority 1: Single M4B file (audiobook format with chapters)
        - Priority 2: Single large M4A file (likely combined audiobook)
        - Priority 3: For multi-file MP3 audiobooks, return first/largest file
        """
        if not files:
            return None
        
        # Filter audio files only
        audio_extensions = ['.m4b', '.mp3', '.m4a', '.aac', '.ogg', '.flac']
        audio_files = [
            f for f in files 
            if any(f['name'].lower().endswith(ext) for ext in audio_extensions)
        ]
        
        if not audio_files:
            logger.warning("No audio files found in magnet")
            return None
        
        logger.info(f"Found {len(audio_files)} audio files in torrent")
        
        # Strategy 1: Look for M4B files (audiobook format with chapters)
        m4b_files = [f for f in audio_files if f['name'].lower().endswith('.m4b')]
        if m4b_files:
            best_m4b = max(m4b_files, key=lambda f: f['size'])
            logger.info(f"Selected M4B file: {best_m4b['name']} ({best_m4b['size'] / 1024 / 1024:.1f} MB)")
            return best_m4b
        
        # Strategy 2: Look for large M4A files (likely combined audiobook)
        m4a_files = [f for f in audio_files if f['name'].lower().endswith('.m4a')]
        if m4a_files:
            # If there's a large M4A (>50MB), it's likely a combined audiobook
            large_m4a = [f for f in m4a_files if f['size'] > 50 * 1024 * 1024]
            if large_m4a:
                best_m4a = max(large_m4a, key=lambda f: f['size'])
                logger.info(f"Selected large M4A file: {best_m4a['name']} ({best_m4a['size'] / 1024 / 1024:.1f} MB)")
                return best_m4a
        
        # Strategy 3: Multi-file MP3 audiobook - return the first numbered file or largest
        mp3_files = [f for f in audio_files if f['name'].lower().endswith('.mp3')]
        if mp3_files:
            logger.warning(f"Multi-file MP3 audiobook detected ({len(mp3_files)} files). Selecting first/largest file.")
            
            # Try to find a file with "01" or "001" in the name (first chapter)
            first_files = [f for f in mp3_files if '01' in f['name'] or '001' in f['name']]
            if first_files:
                best_mp3 = first_files[0]
            else:
                # Fallback: select largest MP3
                best_mp3 = max(mp3_files, key=lambda f: f['size'])
            
            logger.info(f"Selected MP3 file: {best_mp3['name']} ({best_mp3['size'] / 1024 / 1024:.1f} MB)")
            logger.warning(f"⚠️ Note: This audiobook has {len(mp3_files)} MP3 files. Consider uploading M4B format for better experience.")
            return best_mp3
        
        # Fallback: return largest audio file
        best_file = max(audio_files, key=lambda f: f['size'])
        logger.info(f"Selected audio file (fallback): {best_file['name']} ({best_file['size'] / 1024 / 1024:.1f} MB)")
        
        return best_file
    
    def delete_magnet(self, magnet_id: int) -> bool:
        """Delete a magnet from AllDebrid"""
        try:
            response = self.session.post(
                f"{self.base_url}/magnet/delete",
                headers=self.headers,
                data={'id': magnet_id},
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            return data.get('status') == 'success'
        
        except Exception as e:
            logger.error(f"Error deleting magnet: {e}")
            return False
    
    def unlock_link(self, file_link: str) -> Optional[str]:
        """
        Unlock/debrid an AllDebrid link to get direct download URL
        Returns the unlocked direct download link
        """
        try:
            logger.info(f"Unlocking AllDebrid link: {file_link[:50]}...")
            
            response = self.session.post(
                f"{self.base_url}/link/unlock",
                headers=self.headers,
                data={'link': file_link},
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('status') != 'success':
                logger.error(f"AllDebrid unlock error: {data.get('error', {})}")
                return None
            
            unlocked_link = data.get('data', {}).get('link')
            
            if unlocked_link:
                logger.info(f"Link unlocked successfully: {unlocked_link[:50]}...")
                return unlocked_link
            else:
                logger.error("No unlocked link returned from AllDebrid")
                return None
        
        except Exception as e:
            logger.error(f"Error unlocking link: {e}")
            return None

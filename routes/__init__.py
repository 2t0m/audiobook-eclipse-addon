"""
Routes package for audiobook Eclipse addon
"""

from .manifest import register_routes as register_manifest_routes
from .search import register_routes as register_search_routes
from .stream import register_routes as register_stream_routes
from .album import register_routes as register_album_routes
from .unlock import register_routes as register_unlock_routes
from .track import register_routes as register_track_routes


def register_all_routes(app, api_key, torznab_client, alldebrid_client, audible_client, streaming_session):
    """Register all route modules"""
    register_manifest_routes(app, api_key)
    register_search_routes(app, api_key, torznab_client, alldebrid_client, audible_client)
    register_stream_routes(app, api_key, alldebrid_client, streaming_session)
    register_album_routes(app, api_key, alldebrid_client)
    # register_unlock_routes(app, api_key, alldebrid_client)  # DISABLED: use /stream proxy only
    register_track_routes(app, api_key)

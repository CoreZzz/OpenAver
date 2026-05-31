"""MetatubeConnectionState — runtime-only connection & availability state (TASK-63a-4).

Singleton (`metatube_state`) tracks:
- Whether OpenAver is connected to a metatube HTTP server.
- Per-provider availability (key = 'metatube:{ProviderName}', matches config `id` field).
- Thread-safe: all mutations and reads are guarded by a reentrant lock (RLock).

Design notes:
- NOT persistent: nothing is written to config.json / DB / disk.
- `_availability` keys are kept on disconnect (bulk set to False) so the UI
  can render grey capsules for known-but-unavailable providers (63b).
- Use RLock (not Lock) so that future callers within the same thread (e.g. a
  method that calls another method internally) won't deadlock.  RLock is a
  strict superset of Lock and matches the house pattern in
  core/similar/ranker_cache.py and web/routers/notifications.py.

Usage:
    from core.metatube.state import metatube_state

    metatube_state.connect(base_url, token, ['FANZA', 'HEYZO'])
    metatube_state.availability_map()   # -> {'metatube:FANZA': True, ...}
    metatube_state.disconnect()
"""
import threading

from core.logger import get_logger

logger = get_logger(__name__)


class MetatubeConnectionState:
    """Thread-safe, runtime-only metatube connection & provider availability state."""

    def __init__(self) -> None:
        # Use RLock: reentrant, so same-thread nested acquisitions won't deadlock.
        self._lock: threading.RLock = threading.RLock()
        self.connected: bool = False
        self.base_url: str | None = None
        self.token: str | None = None
        self._availability: dict[str, bool] = {}   # key = 'metatube:{ProviderName}'
        self._providers: list[str] = []            # raw ProviderName list

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def connect(self, base_url: str, token: str, provider_names: list[str]) -> None:
        """Mark as connected and bulk-set all named providers to available.

        Repeated connect rebuilds availability from scratch: _availability is
        cleared first, then bulk-set to True for all providers in
        `provider_names`.  Stale keys from a previous connection to a different
        server are removed, preventing phantom-available providers from being
        routed to a server that no longer serves them.

        Args:
            base_url: Base URL of the metatube HTTP server (e.g. 'http://host:8080').
            token:    API token (empty string means no auth required).
            provider_names: Raw provider names WITHOUT 'metatube:' prefix
                            (e.g. ['FANZA', 'HEYZO']).
        """
        with self._lock:
            self._availability = {}  # clear stale keys before rebuilding
            self.connected = True
            self.base_url = base_url
            self.token = token
            self._providers = list(provider_names)
            for name in provider_names:
                self._availability[f'metatube:{name}'] = True
        logger.debug(
            'MetatubeConnectionState.connect: base_url=%r providers=%r',
            base_url, provider_names,
        )

    def disconnect(self) -> None:
        """Mark as disconnected; bulk-set all known providers to unavailable.

        Keys are preserved (not cleared) so the UI can still render grey
        capsules for previously known providers (63b feature).
        """
        with self._lock:
            self.connected = False
            self.base_url = None
            self.token = None
            self._providers = []
            for key in self._availability:
                self._availability[key] = False
        logger.debug('MetatubeConnectionState.disconnect')

    def mark_failed(self, source_id: str) -> None:
        """Set a single provider source to unavailable.

        Works on unknown source_ids (creates entry with False value).

        Args:
            source_id: Full source id including prefix, e.g. 'metatube:FANZA'.
        """
        with self._lock:
            self._availability[source_id] = False

    def mark_available(self, source_id: str) -> None:
        """Set a single provider source to available.

        Works on unknown source_ids (creates entry with True value).

        Args:
            source_id: Full source id including prefix, e.g. 'metatube:FANZA'.
        """
        with self._lock:
            self._availability[source_id] = True

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def availability_map(self) -> dict[str, bool]:
        """Return a shallow copy of the current availability dict.

        Returns a copy so that external callers (e.g. get_enabled_source_ids,
        fan-out routing) cannot accidentally mutate the internal state during
        concurrent probe operations.
        """
        with self._lock:
            return dict(self._availability)

    def is_available(self, source_id: str) -> bool:
        """Return True iff the source is currently marked available.

        Unknown source_ids return False (safe default).
        """
        with self._lock:
            return self._availability.get(source_id, False)

    # ------------------------------------------------------------------
    # Read-only properties
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """True if currently connected to a metatube server."""
        with self._lock:
            return self.connected

    @property
    def provider_count(self) -> int:
        """Number of providers registered at the last connect()."""
        with self._lock:
            return len(self._providers)


# ---------------------------------------------------------------------------
# Module-level singleton (placed after class definition per house convention)
# ---------------------------------------------------------------------------
metatube_state = MetatubeConnectionState()

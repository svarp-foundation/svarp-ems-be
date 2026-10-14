"""
HTTP client for communicating with the portal-user service.
All auth, user lookup, and permission checks go through here.
"""

import time
from threading import Lock
from typing import Optional, Dict, Any
from uuid import UUID
import httpx
from app.core.config import settings


class TTLCache:
    """Thread-safe in-memory cache with TTL eviction."""

    def __init__(self, ttl_seconds: float = 20.0):
        self.ttl = ttl_seconds
        self._cache: Dict[str, tuple] = {}
        self._lock = Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._cache:
                val, expires_at = self._cache[key]
                if time.time() < expires_at:
                    return val
                del self._cache[key]
            return None

    def set(self, key: str, val: Any) -> None:
        with self._lock:
            now = time.time()
            if len(self._cache) > 500:
                self._cache = {k: v for k, v in self._cache.items() if v[1] > now}
            self._cache[key] = (val, now + self.ttl)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


class PortalUserClient:
    """Wraps portal-user /external/* API calls with connection pooling and caching."""

    def __init__(self):
        self.base_url = settings.PORTAL_USER_BASE_URL.rstrip("/")
        self.api_key = settings.PORTAL_USER_API_KEY
        self.api_secret = settings.PORTAL_USER_API_SECRET
        self.timeout = 10.0
        self._client = httpx.Client(
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
        )
        self._cache = TTLCache(ttl_seconds=20.0)

    def _headers_key_only(self) -> Dict[str, str]:
        """Headers for endpoints requiring only API key."""
        return {"X-API-KEY": self.api_key}

    def _headers_full(self) -> Dict[str, str]:
        """Headers for endpoints requiring API key + secret."""
        return {
            "X-API-KEY": self.api_key,
            "X-API-SECRET": self.api_secret,
        }

    def login(self, email: str, password: str) -> Dict[str, Any]:
        """
        Authenticate via portal-user.
        Returns: {"access_token": "...", "token_type": "bearer"}
        Raises: httpx.HTTPStatusError on auth failure
        """
        response = self._client.post(
            f"{self.base_url}/external/login",
            data={"username": email, "password": password},
            headers=self._headers_key_only(),
        )
        response.raise_for_status()
        return response.json()

    def validate_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Validate a JWT with portal-user.
        Caches valid results for 20s to prevent spamming the auth service on parallel calls.
        Returns: {"is_valid": bool, "user_id": str, "session_id": str, "expires_at": float}
        """
        cache_key = f"token_{token}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            response = self._client.get(
                f"{self.base_url}/external/validate-token",
                headers={
                    **self._headers_key_only(),
                    "Authorization": f"Bearer {token}",
                },
            )
            if response.status_code != 200:
                return None
            data = response.json()
            if data and data.get("is_valid"):
                self._cache.set(cache_key, data)
            return data
        except Exception:
            return None

    def invalidate_token(self, token: str) -> None:
        """Evict token from local cache."""
        self._cache.invalidate(f"token_{token}")

    def get_user(
        self,
        user_id: Optional[UUID] = None,
        email: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch user info from portal-user.
        Caches results for 20s.
        Returns: {"user_id", "email", "full_name", "is_active", "roles", ...}
        """
        cache_key = f"user_{str(user_id) if user_id else ''}_{email or ''}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        params = {}
        if user_id:
            params["user_id"] = str(user_id)
        if email:
            params["email"] = email

        try:
            response = self._client.get(
                f"{self.base_url}/external/get-user",
                params=params,
                headers=self._headers_full(),
            )
            if response.status_code != 200:
                return None
            data = response.json()
            if data:
                self._cache.set(cache_key, data)
            return data
        except Exception:
            return None

    def get_permissions(
        self,
        user_id: Optional[UUID] = None,
        email: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch user permissions from portal-user.
        Returns: {"user_id": str, "permissions": [...]}
        """
        params = {}
        if user_id:
            params["user_id"] = str(user_id)
        if email:
            params["email"] = email

        try:
            response = self._client.get(
                f"{self.base_url}/external/get-permissions",
                params=params,
                headers=self._headers_full(),
            )
            if response.status_code != 200:
                return None
            return response.json()
        except Exception:
            return None

    def list_users(
        self, skip: int = 0, limit: int = 100, search: Optional[str] = None
    ) -> list:
        """Fetch users list from portal-user."""
        params = {"skip": skip, "limit": limit}
        if search:
            params["search"] = search

        try:
            response = self._client.get(
                f"{self.base_url}/external/list-users",
                params=params,
                headers=self._headers_key_only(),
            )
            if response.status_code != 200:
                return []
            return response.json()
        except Exception:
            return []


# Singleton instance
portal_user_client = PortalUserClient()

# http_help.py
from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from nuke_lock_utils import local_system_id

TASK_STATUS_CHOICES = [
    ("unassigned", "Unassigned"),
    ("assigned", "Assigned"),
    ("not_started", "Not started"),
    ("in_progress", "In progress"),
    ("waiting_for_approval", "Waiting for approval"),
    ("approved", "Approved"),
    ("done", "Done"),
    ("rejected", "Rejected"),
]


class DjangoAPIError(requests.RequestException):
    def __init__(
        self,
        *,
        operation: str,
        method: str,
        url: str,
        timeout: float,
        attempt: int,
        total_attempts: int,
        detail: str,
        retryable: bool = False,
        status_code: int | None = None,
        original_error: Exception | None = None,
    ):
        self.operation = operation
        self.method = method.upper()
        self.url = url
        self.timeout = timeout
        self.attempt = attempt
        self.total_attempts = total_attempts
        self.detail = detail
        self.retryable = retryable
        self.status_code = status_code
        self.original_error = original_error
        super().__init__(self.__str__())

    @property
    def endpoint(self) -> str:
        parsed = urlparse(self.url)
        return parsed.path or self.url

    @property
    def timeout_text(self) -> str:
        try:
            timeout_value = float(self.timeout)
        except (TypeError, ValueError):
            return str(self.timeout)
        if timeout_value.is_integer():
            return str(int(timeout_value))
        return f"{timeout_value:g}"

    def __str__(self) -> str:
        parts = [
            self.operation,
            f"{self.method} {self.endpoint}",
            self.detail,
            f"attempt {self.attempt}/{self.total_attempts}",
        ]
        return " | ".join(part for part in parts if part)

class DjangoAPI:
    # Class-level default URL (can be updated via settings)
    _default_base_url = "http://192.168.10.207:8000/api/"
    
    # Class-level username for activity tracking (shared across all instances)
    _current_username = None

    # Cached users list for username lookup
    _cached_users = None
    _system_id = None
    _max_retry_backoff = 3.0
    
    def __init__(
        self,
        base_url: str = None,
        *,
        timeout: float = 6,
        retry_count: int = 0,
        retry_backoff: float = 0.0,
        skip_activity_logging: bool = False,
    ):
        # Use provided URL, or class default
        self.base_url = base_url or DjangoAPI._default_base_url
        self._s = requests.Session()
        self._timeout = float(timeout)
        self._retry_count = max(0, int(retry_count))
        self._retry_backoff = max(0.0, float(retry_backoff))
        self._skip_activity_logging = bool(skip_activity_logging)
    
    @classmethod
    def get_system_id(cls) -> str:
        if not cls._system_id:
            cls._system_id = local_system_id()
        return cls._system_id
    
    @classmethod
    def set_default_base_url(cls, url: str):
        """Set the default base URL for all new instances."""
        cls._default_base_url = url
    
    @classmethod
    def set_current_username(cls, username: str):
        """
        Set the current username for activity tracking.
        This should be called once when the app starts or user logs in.
        All API requests will include this username in the X-ShotBox-User header.
        """
        cls._current_username = username
        if username:
            print(f"[ShotBox] Activity tracking enabled for user: {username}")
    
    @classmethod
    def set_current_user_by_id(cls, user_id: int):
        """
        Set the current username by looking up the user ID.
        Fetches users from API if not cached.
        
        Args:
            user_id: The Django user ID from settings
        """
        if user_id is None:
            cls._current_username = None
            return
        
        # Try to find username from cached users
        if cls._cached_users:
            for user in cls._cached_users:
                if user.get("id") == user_id:
                    cls.set_current_username(user.get("username"))
                    return
        
        # Not in cache, try to fetch
        try:
            api = cls()
            users = api.get_users()
            cls._cached_users = users
            
            for user in users:
                if user.get("id") == user_id:
                    cls.set_current_username(user.get("username"))
                    return
            
            print(f"[ShotBox] Warning: User ID {user_id} not found")
        except Exception as e:
            print(f"[ShotBox] Could not look up user ID {user_id}: {e}")
    
    @classmethod
    def get_current_username(cls) -> str:
        """Get the currently set username."""
        return cls._current_username

    def _get_headers(self) -> dict:
        """Get headers for API requests, including username if set."""
        headers = {}
        if DjangoAPI._current_username:
            headers["X-ShotBox-User"] = DjangoAPI._current_username
        if self._skip_activity_logging:
            headers["X-ShotBox-Skip-Activity"] = "1"
        return headers

    def _should_retry_method(self, method: str) -> bool:
        return method.upper() in {"GET", "PATCH", "PUT", "DELETE"}

    def _sleep_before_retry(self, attempt: int) -> None:
        if self._retry_backoff <= 0:
            return
        delay = min(self._retry_backoff * max(1, attempt), self._max_retry_backoff)
        time.sleep(delay)

    def _build_error(
        self,
        *,
        operation: str,
        method: str,
        url: str,
        timeout: float,
        attempt: int,
        total_attempts: int,
        detail: str,
        retryable: bool,
        status_code: int | None = None,
        original_error: Exception | None = None,
    ) -> DjangoAPIError:
        return DjangoAPIError(
            operation=operation,
            method=method,
            url=url,
            timeout=timeout,
            attempt=attempt,
            total_attempts=total_attempts,
            detail=detail,
            retryable=retryable,
            status_code=status_code,
            original_error=original_error,
        )
    
    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """
        Make a request with the username header.
        Wrapper around session methods to ensure headers are always included.
        """
        operation = kwargs.pop("operation", method.upper())
        raise_for_status = kwargs.pop("raise_for_status", True)
        # Merge our headers with any provided headers
        headers = self._get_headers()
        if 'headers' in kwargs:
            headers.update(kwargs['headers'])
        if 'json' in kwargs and "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"
        kwargs['headers'] = headers
        
        # Set default timeout
        timeout_value = kwargs.get('timeout', self._timeout)
        kwargs['timeout'] = timeout_value
        total_attempts = self._retry_count + 1
        retryable_method = self._should_retry_method(method)

        for attempt in range(1, total_attempts + 1):
            try:
                response = self._s.request(method, url, **kwargs)
            except requests.Timeout as exc:
                if retryable_method and attempt < total_attempts:
                    self._sleep_before_retry(attempt)
                    continue
                timeout_text = self._build_error(
                    operation=operation,
                    method=method,
                    url=url,
                    timeout=timeout_value,
                    attempt=attempt,
                    total_attempts=total_attempts,
                    detail="",
                    retryable=retryable_method,
                ).timeout_text
                raise self._build_error(
                    operation=operation,
                    method=method,
                    url=url,
                    timeout=timeout_value,
                    attempt=attempt,
                    total_attempts=total_attempts,
                    detail=f"timed out after {timeout_text}s",
                    retryable=True,
                    original_error=exc,
                ) from exc
            except requests.ConnectionError as exc:
                if retryable_method and attempt < total_attempts:
                    self._sleep_before_retry(attempt)
                    continue
                raise self._build_error(
                    operation=operation,
                    method=method,
                    url=url,
                    timeout=timeout_value,
                    attempt=attempt,
                    total_attempts=total_attempts,
                    detail=f"connection failed: {exc}",
                    retryable=True,
                    original_error=exc,
                ) from exc

            if response.status_code in {502, 503, 504} and retryable_method and attempt < total_attempts:
                self._sleep_before_retry(attempt)
                continue

            if raise_for_status and response.status_code >= 400:
                detail = f"HTTP {response.status_code}"
                try:
                    payload = response.json()
                    if isinstance(payload, dict) and payload.get("detail"):
                        detail = f"{detail}: {payload['detail']}"
                except ValueError:
                    body_text = response.text.strip()
                    if body_text:
                        detail = f"{detail}: {body_text[:200]}"

                raise self._build_error(
                    operation=operation,
                    method=method,
                    url=url,
                    timeout=timeout_value,
                    attempt=attempt,
                    total_attempts=total_attempts,
                    detail=detail,
                    retryable=response.status_code in {502, 503, 504},
                    status_code=response.status_code,
                )

            return response

        raise self._build_error(
            operation=operation,
            method=method,
            url=url,
            timeout=timeout_value,
            attempt=total_attempts,
            total_attempts=total_attempts,
            detail="request failed",
            retryable=retryable_method,
        )

    def getAPI(self):
        r = self._request('GET', self.base_url, operation="get_api_root")
        return r.json()

    def get_users(self):
        users_url = f"{self.base_url}users"
        r = self._request('GET', users_url, operation="get_users")

        users = r.json()
        
        # Cache for later lookups
        DjangoAPI._cached_users = users

        # Only keep users in group 1
        filtered = []
        for user in users:
            groups = user.get("groups", [])
            if 1 in groups:
                filtered.append(user)

        return filtered


    def username_from_id(self, id):
        name = None
        try:
            users = self.get_users()
            for user in users:
                if id == user.get("id"):
                    name = user.get("first_name")
                    break
        except Exception:
            return "None"
        return name if name else "None"

    # NEW: generic partial update for a task
    def update_task(self, task_id: int, **fields):
        url = f"{self.base_url}tasks/{task_id}"
        r = self._request('PATCH', url, json=fields, operation="update_task")
        return r.json()
    
    def create_task(self, *, shot_id: int, title: str = "New Task", notes: str = ""):
        url = f"{self.base_url}tasks"
        payload = {"title": title, "shot": shot_id, "notes": notes}
        r = self._request('POST', url, json=payload, operation="create_task")
        return r.json()
    
    def delete_task(self, task_id: int):
        url = f"{self.base_url}tasks/{task_id}"
        self._request('DELETE', url, operation="delete_task")
        return True
    
    def update_shot(self, shot_id: int, **fields):
        url = f"{self.base_url}shots/{shot_id}"
        r = self._request('PATCH', url, json=fields, operation="update_shot")
        return r.json()

    def get_shot(self, shot_id: int):
        url = f"{self.base_url}shots/{shot_id}"
        r = self._request('GET', url, operation="get_shot")
        return r.json()
    
    def get_job(self, job_id: int):
        url = f"{self.base_url}jobs/{job_id}"
        r = self._request('GET', url, operation="get_job")
        return r.json()

    def update_timeline(self, timeline_id: int, **fields):
        url = f"{self.base_url}timelines/{timeline_id}"
        r = self._request('PATCH', url, json=fields, operation="update_timeline")
        return r.json()

    def get_timeline(self, timeline_id: int):
        url = f"{self.base_url}timelines/{timeline_id}"
        r = self._request('GET', url, operation="get_timeline")
        return r.json()

    def update_shot_lock(self, shot_id: int, release: bool = False, force: bool = False):
        url = f"{self.base_url}shots/{shot_id}"
        payload = {"nuke_in_use": "None" if release else "heartbeat"}
        if force and not release:
            payload["nuke_force_take"] = True

        headers = {"X-ShotBox-System": DjangoAPI.get_system_id()}
        try:
            r = self._request(
                'PATCH',
                url,
                json=payload,
                headers=headers,
                operation="update_shot_lock",
                raise_for_status=False,
            )
        except requests.RequestException as exc:
            return {
                "ok": False,
                "status_code": None,
                "shot": None,
                "conflict": False,
                "lock_status": None,
                "detail": str(exc),
                "nuke_in_use": None,
            }

        try:
            body = r.json()
        except ValueError:
            body = {}

        if 200 <= r.status_code < 300:
            return {
                "ok": True,
                "status_code": r.status_code,
                "shot": body if isinstance(body, dict) else None,
                "conflict": False,
                "lock_status": None,
                "detail": None,
                "nuke_in_use": body.get("nuke_in_use") if isinstance(body, dict) else None,
            }

        return {
            "ok": False,
            "status_code": r.status_code,
            "shot": None,
            "conflict": r.status_code == 409,
            "lock_status": body.get("lock_status") if isinstance(body, dict) else None,
            "detail": body.get("detail") if isinstance(body, dict) else None,
            "nuke_in_use": body.get("nuke_in_use") if isinstance(body, dict) else None,
        }

    # --- Import API methods ---
    
    def get_jobs(self):
        """Get all jobs from the API."""
        url = f"{self.base_url}jobs"
        r = self._request('GET', url, operation="get_jobs")
        return r.json()
    
    def create_job(self, title: str):
        """Create a new job. Returns the created job dict."""
        url = f"{self.base_url}jobs"
        payload = {"title": title}
        r = self._request('POST', url, json=payload, operation="create_job")
        return r.json()
    
    def get_job_by_title(self, title: str):
        """Find a job by title. Returns job dict or None."""
        jobs = self.get_jobs()
        for job in jobs:
            if job.get("title") == title:
                return job
        return None
    
    def get_or_create_job(self, title: str):
        """Get existing job by title or create new one. Returns (job_dict, created_bool)."""
        existing = self.get_job_by_title(title)
        if existing:
            return existing, False

        total_attempts = self._retry_count + 1
        for attempt in range(1, total_attempts + 1):
            try:
                new_job = self.create_job(title)
                return new_job, True
            except DjangoAPIError as exc:
                if not exc.retryable:
                    raise RuntimeError(f"get_or_create_job | {exc}") from exc
                existing = self.get_job_by_title(title)
                if existing:
                    return existing, False
                if attempt >= total_attempts:
                    raise RuntimeError(f"get_or_create_job | {exc}") from exc
                self._sleep_before_retry(attempt)
        raise RuntimeError(f"get_or_create_job | could not create or fetch '{title}'")
    
    def create_timeline(self, job_id: int, title: str):
        """Create a new timeline under a job. Returns the created timeline dict."""
        url = f"{self.base_url}timelines"
        payload = {"job": job_id, "title": title}
        r = self._request('POST', url, json=payload, operation="create_timeline")
        return r.json()
    
    def get_timelines_for_job(self, job_id: int):
        """Get all timelines for a job."""
        # Fetch full job data which includes timelines
        url = f"{self.base_url}jobs/{job_id}"
        r = self._request('GET', url, operation="get_timelines_for_job")
        job_data = r.json()
        return job_data.get("timelines", [])
    
    def get_or_create_timeline(self, job_id: int, title: str):
        """Get existing timeline or create new one. Returns (timeline_dict, created_bool)."""
        timelines = self.get_timelines_for_job(job_id)
        for tl in timelines:
            if tl.get("title") == title:
                return tl, False

        total_attempts = self._retry_count + 1
        for attempt in range(1, total_attempts + 1):
            try:
                new_tl = self.create_timeline(job_id, title)
                return new_tl, True
            except DjangoAPIError as exc:
                if not exc.retryable:
                    raise RuntimeError(f"get_or_create_timeline | {exc}") from exc
                timelines = self.get_timelines_for_job(job_id)
                for timeline in timelines:
                    if timeline.get("title") == title:
                        return timeline, False
                if attempt >= total_attempts:
                    raise RuntimeError(f"get_or_create_timeline | {exc}") from exc
                self._sleep_before_retry(attempt)
        raise RuntimeError(f"get_or_create_timeline | could not create or fetch '{title}'")
    
    def create_shot(self, timeline_id: int, title: str, base_path: str = ""):
        """Create a new shot under a timeline. Returns the created shot dict."""
        url = f"{self.base_url}shots"
        payload = {"timeline": timeline_id, "title": title, "base_path": base_path}
        r = self._request('POST', url, json=payload, operation="create_shot")
        return r.json()
    
    def get_shots_for_timeline(self, timeline_id: int):
        """Get all shots for a timeline."""
        url = f"{self.base_url}timelines/{timeline_id}"
        r = self._request('GET', url, operation="get_shots_for_timeline")
        tl_data = r.json()
        return tl_data.get("shots", [])
    
    def get_or_create_shot(self, timeline_id: int, title: str, base_path: str = ""):
        """Get existing shot or create new one. Returns (shot_dict, created_bool)."""
        shots = self.get_shots_for_timeline(timeline_id)
        for shot in shots:
            if shot.get("title") == title:
                # Update base_path if it changed
                if base_path and shot.get("base_path") != base_path:
                    self.update_shot(shot["id"], base_path=base_path)
                    shot["base_path"] = base_path
                return shot, False

        total_attempts = self._retry_count + 1
        for attempt in range(1, total_attempts + 1):
            try:
                new_shot = self.create_shot(timeline_id, title, base_path)
                return new_shot, True
            except DjangoAPIError as exc:
                if not exc.retryable:
                    raise RuntimeError(f"get_or_create_shot | {exc}") from exc
                shots = self.get_shots_for_timeline(timeline_id)
                for shot in shots:
                    if shot.get("title") == title:
                        return shot, False
                if attempt >= total_attempts:
                    raise RuntimeError(f"get_or_create_shot | {exc}") from exc
                self._sleep_before_retry(attempt)
        raise RuntimeError(f"get_or_create_shot | could not create or fetch '{title}'")
    
    def upload_shot_thumbnail(self, shot_id: int, image_path: str):
        """Upload a thumbnail image for a shot. Returns updated shot dict."""
        url = f"{self.base_url}shots/{shot_id}/thumbnail"

        with open(image_path, "rb") as f:
            files = {"thumbnail": (Path(image_path).name, f, self._guess_mime_type(image_path))}
            r = self._request(
                'POST',
                url,
                files=files,
                timeout=max(self._timeout, 30),
                operation="upload_shot_thumbnail",
            )

        return r.json()
    
    def _guess_mime_type(self, path: str) -> str:
        """Guess MIME type from file extension."""
        ext = Path(path).suffix.lower()
        mime_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        return mime_types.get(ext, "application/octet-stream")
    
    # --- Activity API methods ---
    
    def get_recent_activity(self, limit: int = 50, **filters):
        """
        Get recent activity logs.
        
        Args:
            limit: Maximum number of results (default 50, max 200)
            **filters: Optional filters - user, action_type, shot, job, hours, since
        
        Returns:
            List of activity log entries
        """
        url = f"{self.base_url}activity/recent"
        params = {"limit": limit}
        params.update(filters)
        
        r = self._request('GET', url, params=params, operation="get_recent_activity")
        return r.json()
    
    def get_shot_activity(self, shot_id: int, limit: int = 20):
        """
        Get activity logs for a specific shot.
        
        Args:
            shot_id: The shot ID
            limit: Maximum number of results (default 20)
        
        Returns:
            List of activity log entries for the shot
        """
        url = f"{self.base_url}shots/{shot_id}/activity"
        params = {"limit": limit}
        
        r = self._request('GET', url, params=params, operation="get_shot_activity")
        return r.json()


# =============================================================================
# CONVENIENCE FUNCTION FOR APP STARTUP
# =============================================================================

def setup_activity_tracking_from_settings():
    """
    Set up activity tracking using the django_username from settings.
    Call this once at app startup.
    
    Usage in main.py:
        import http_help
        http_help.setup_activity_tracking_from_settings()
    """
    try:
        from settings import get_settings_manager
        settings = get_settings_manager()
        user_id = settings.get("django_username")
        
        if user_id:
            DjangoAPI.set_current_user_by_id(user_id)
        else:
            print("[ShotBox] No django_username set in settings, activity will show as 'System'")
    except Exception as e:
        print(f"[ShotBox] Could not set up activity tracking: {e}")

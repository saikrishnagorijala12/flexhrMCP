from pathlib import Path
import msal
import httpx
from src.config import Config

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Chat.Read covers 1:1 and group chats without admin consent.
# ChannelMessage.Read.All requires admin consent — excluded by default.
SCOPES = [
    "User.Read",
    "Mail.Read",
    "Calendars.Read",
    "Notes.Read",
    "Files.Read",
]


class GraphClient:
    def __init__(self, config: Config):
        self._config = config
        self._cache = msal.SerializableTokenCache()
        self._load_cache()
        self._app = msal.PublicClientApplication(
            client_id=config.ms_client_id,
            authority=f"https://login.microsoftonline.com/{config.ms_tenant_id}",
            token_cache=self._cache,
        )
        self._http = httpx.Client(timeout=60, follow_redirects=True)

    def _cache_path(self) -> Path:
        return Path(self._config.ms_token_cache)

    def _load_cache(self):
        p = self._cache_path()
        if p.exists():
            self._cache.deserialize(p.read_text())

    def _save_cache(self):
        if self._cache.has_state_changed:
            self._cache_path().write_text(self._cache.serialize())

    def _token(self) -> str:
        accounts = self._app.get_accounts()
        result = None
        if accounts:
            result = self._app.acquire_token_silent(SCOPES, account=accounts[0])
        if not result:
            flow = self._app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                raise RuntimeError(f"Device flow initiation failed: {flow}")
            print(f"\n{flow['message']}\n")
            result = self._app.acquire_token_by_device_flow(flow)
            if "access_token" not in result:
                raise RuntimeError(f"Auth failed: {result.get('error_description', result)}")
            self._save_cache()
            granted = result.get("scope", "")
            if granted:
                print(f"[auth] Signed in. Scopes: {granted}")
        return result["access_token"]

    def _raise_graph_error(self, resp: httpx.Response) -> None:
        try:
            body = resp.json()
            err = body.get("error", {})
            code = err.get("code", "")
            msg = err.get("message", "")
            inner = (err.get("innerError") or {}).get("message", "")
            detail = f"{code}: {msg}" + (f" ({inner})" if inner else "")
        except Exception:
            detail = resp.text or "(empty response body)"

        hint = ""
        if resp.status_code == 401:
            hint = (
                "\n\nHint: 401 usually means a missing or wrongly-consented permission."
                "\n  1. Delete .token_cache.json and re-run to see which scopes are granted."
                "\n  2. Make sure MS_TENANT_ID in .env is your actual tenant ID (not 'common')."
                "\n  3. Verify all permissions are consented in Azure portal → API permissions."
            )
        elif resp.status_code == 403:
            hint = "\n\nHint: 403 means the permission exists but needs admin consent."

        raise httpx.HTTPStatusError(
            f"Graph API {resp.status_code} — {detail}{hint}",
            request=resp.request,
            response=resp,
        )

    def get(self, path: str, **params) -> dict:
        resp = self._http.get(
            f"{GRAPH_BASE}{path}",
            headers={"Authorization": f"Bearer {self._token()}"},
            params=params,
        )
        if not resp.is_success:
            self._raise_graph_error(resp)
        return resp.json()

    def get_bytes(self, url: str) -> bytes:
        resp = self._http.get(
            url,
            headers={"Authorization": f"Bearer {self._token()}"},
        )
        if not resp.is_success:
            self._raise_graph_error(resp)
        return resp.content

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

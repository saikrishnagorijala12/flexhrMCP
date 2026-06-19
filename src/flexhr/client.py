import re
from datetime import date
import httpx
from src.config import Config


class FlexHRClient:
    """Frappe HRMS client — authenticates via username/password session.

    Uses two sessions:
    - _http      : regular user credentials (metadata reads)
    - _http_api  : API user credentials (timesheet create/submit)
                   Falls back to the same credentials if FLEXHR_API_USERNAME is not set.
    """

    def __init__(self, config: Config):
        self._url = config.flexhr_url
        self._employee = config.flexhr_employee
        self._company = config.flexhr_company

        self._username = config.flexhr_username
        self._password = config.flexhr_password
        self._api_username = config.flexhr_api_username
        self._api_password = config.flexhr_api_password
        self._work_status = config.flexhr_work_status

        self._http = httpx.Client(timeout=30)
        self._logged_in = False

        # Separate session for privileged submit operations
        self._same_user = (self._api_username == self._username)
        self._http_api = self._http if self._same_user else httpx.Client(timeout=30)
        self._api_logged_in = False

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _login(self):
        resp = self._http.post(
            f"{self._url}/api/method/login",
            json={"usr": self._username, "pwd": self._password},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("message") not in ("Logged In", "No App"):
            raise RuntimeError(f"Frappe login failed: {data}")
        self._logged_in = True
        if self._same_user:
            self._api_logged_in = True

    def _login_api(self):
        if self._same_user:
            self._login()
            return
        resp = self._http_api.post(
            f"{self._url}/api/method/login",
            json={"usr": self._api_username, "pwd": self._api_password},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("message") not in ("Logged In", "No App"):
            raise RuntimeError(f"Frappe API user login failed: {data}")
        self._api_logged_in = True

    def _ensure_logged_in(self):
        if not self._logged_in:
            self._login()

    def _ensure_api_logged_in(self):
        if not self._api_logged_in:
            self._login_api()

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _get(self, path: str, **params) -> dict:
        self._ensure_logged_in()
        resp = self._http.get(f"{self._url}{path}", params=params)
        if resp.status_code == 403:
            self._logged_in = False
            self._login()
            resp = self._http.get(f"{self._url}{path}", params=params)
        if not resp.is_success:
            self._raise_frappe_error(resp)
        return resp.json()

    def _csrf_token(self) -> str:
        # Frappe sets a csrftoken cookie after login — required for all POST requests
        return self._http_api.cookies.get("csrftoken", "")

    def _post(self, path: str, payload: dict) -> dict:
        self._ensure_api_logged_in()
        resp = self._http_api.post(
            f"{self._url}{path}",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Frappe-CSRF-Token": self._csrf_token(),
            },
        )
        if resp.status_code == 403:
            # Re-login to get a fresh CSRF token and retry once
            self._api_logged_in = False
            self._login_api()
            resp = self._http_api.post(
                f"{self._url}{path}",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Frappe-CSRF-Token": self._csrf_token(),
                },
            )
        if not resp.is_success:
            self._raise_frappe_error(resp)
        return resp.json()

    def _raise_frappe_error(self, resp) -> None:
        try:
            body = resp.json()
            msg = (
                body.get("exception")
                or body.get("_server_messages")
                or body.get("message")
                or str(body)
            )
        except Exception:
            msg = resp.text or "(empty body)"
        raise RuntimeError(f"Frappe {resp.status_code}: {msg}")

    # ── Metadata ──────────────────────────────────────────────────────────────

    def get_activity_types(self) -> list[str]:
        try:
            data = self._get(
                "/api/resource/Activity Type",
                fields='["name"]',
                limit_page_length="100",
            )
            return [r["name"] for r in data.get("data", [])]
        except Exception:
            return [
                "Development", "Meeting", "Review", "Documentation",
                "Testing", "Support", "Research", "Planning", "Admin",
            ]

    def get_projects(self) -> list[dict]:
        """Return list of {name, project_name} dicts for all open projects."""
        try:
            data = self._get(
                "/api/resource/Project",
                fields='["name","project_name"]',
                filters='[["status","=","Open"]]',
                limit_page_length="50",
            )
            return data.get("data", [])
        except Exception:
            return []

    def get_timesheet_detail_fields(self) -> list[dict]:
        """Return all fields on the Timesheet Detail child table."""
        self._ensure_logged_in()
        resp = self._http.get(
            f"{self._url}/api/method/frappe.desk.form.load.getdoctype",
            params={"doctype": "Timesheet Detail"},
        )
        resp.raise_for_status()
        docs = resp.json().get("docs", [])
        if not docs:
            return []
        return [
            {"fieldname": f.get("fieldname"), "label": f.get("label"),
             "fieldtype": f.get("fieldtype"), "reqd": f.get("reqd")}
            for f in docs[0].get("fields", [])
            if f.get("fieldname")
        ]

    # ── Submission ────────────────────────────────────────────────────────────

    def _build_log_rows(self, target_date: date, entries: list[dict]) -> tuple[list[dict], list[str]]:
        date_str = target_date.isoformat()
        rows, notes = [], []
        for e in entries:
            rows.append({
                "activity_type":      e["activity_type"],
                "from_time":          date_str,
                "hours":              float(e["hours"]),
                "project":            e.get("project", ""),
                "custom_work_status": self._work_status,
            })
            if e.get("description"):
                notes.append(f"- [{e['activity_type']}] {e['description']}")
        return rows, notes

    def _find_timesheet_for_date(self, target_date: date) -> str | None:
        """Return the name of an existing timesheet whose week covers target_date."""
        date_str = target_date.isoformat()
        try:
            data = self._get(
                "/api/resource/Timesheet",
                filters=f'[["employee","=","{self._employee}"],'
                        f'["start_date","<=","{date_str}"],'
                        f'["end_date",">=","{date_str}"]]',
                fields='["name"]',
                limit_page_length="1",
            )
            results = data.get("data", [])
            return results[0]["name"] if results else None
        except Exception:
            return None

    def _put(self, path: str, payload: dict) -> dict:
        self._ensure_api_logged_in()
        resp = self._http_api.put(
            f"{self._url}{path}",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Frappe-CSRF-Token": self._csrf_token(),
            },
        )
        if resp.status_code == 403:
            self._api_logged_in = False
            self._login_api()
            resp = self._http_api.put(
                f"{self._url}{path}",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Frappe-CSRF-Token": self._csrf_token(),
                },
            )
        if not resp.is_success:
            self._raise_frappe_error(resp)
        return resp.json()

    def _update_existing(self, name: str, target_date: date,
                         new_rows: list[dict], note_lines: list[str]) -> dict:
        existing = self._get(f"/api/resource/Timesheet/{name}").get("data", {})
        merged_logs = existing.get("time_logs", []) + new_rows
        existing_note = existing.get("note", "") or ""
        if note_lines:
            sep = f"<br><br>--- {target_date.isoformat()} ---<br>"
            merged_note = existing_note + sep + "<br>".join(note_lines)
        else:
            merged_note = existing_note
        return self._put(
            f"/api/resource/Timesheet/{name}",
            {"time_logs": merged_logs, "note": merged_note},
        )

    def submit_timesheet(self, target_date: date, entries: list[dict]) -> tuple[dict, bool]:
        """Create or update the weekly Frappe Timesheet for target_date.

        Returns (response, was_updated) where was_updated=True means rows were
        appended to an existing timesheet.
        """
        new_rows, note_lines = self._build_log_rows(target_date, entries)

        # Check for an existing timesheet covering this date
        existing_name = self._find_timesheet_for_date(target_date)
        if existing_name:
            return self._update_existing(existing_name, target_date, new_rows, note_lines), True

        # Try to create a new one
        payload = {
            "employee":  self._employee,
            "company":   self._company,
            "note":      "<br>".join(note_lines) if note_lines else "",
            "time_logs": new_rows,
        }
        try:
            return self._post("/api/resource/Timesheet", payload), False
        except RuntimeError as exc:
            # Frappe's duplicate check fires — parse the timesheet name from the error HTML
            match = re.search(r'href="/app/timesheet/([^"]+)"', str(exc))
            if match:
                name = match.group(1)
                return self._update_existing(name, target_date, new_rows, note_lines), True
            raise

    # ── Context manager ───────────────────────────────────────────────────────

    def close(self):
        for session, logged_in in [(self._http, self._logged_in),
                                   (self._http_api, self._api_logged_in)]:
            if logged_in and session is not self._http:
                try:
                    session.get(f"{self._url}/api/method/logout")
                except Exception:
                    pass
        if self._logged_in:
            try:
                self._http.get(f"{self._url}/api/method/logout")
            except Exception:
                pass
        self._http.close()
        if not self._same_user:
            self._http_api.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

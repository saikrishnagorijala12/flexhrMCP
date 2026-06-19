# FlexHR Timesheet Agent

Automatically generates timesheet entries from your Microsoft 365 activity (Outlook email, calendar, Teams chats, OneNote) using Qwen3, then lets you review and submit them to Frappe HRMS (FlexHR).

## How it works

1. Reads your M365 activity via Microsoft Graph API (device-flow auth)
2. Sends the activity to Claude via OpenRouter to produce structured timesheet entries
3. Shows a Rich terminal preview — edit, delete, or approve entries
4. Submits approved entries to Frappe HRMS via REST API

## Prerequisites

- Python 3.12+
- An [Azure AD app registration](#azure-ad-setup) with delegated Graph permissions
- An [OpenRouter](https://openrouter.ai/keys) API key
- Access to a Frappe HRMS / FlexHR instance with API credentials

## Setup

```bash
# 1. Clone and create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your credentials (see sections below)

# 4. Run
python agent.py
```

## Configuration

Copy `.env.example` to `.env` and fill in:

### Azure AD

1. Go to [Azure Portal → App registrations](https://portal.azure.com) and create a new app
2. Add a **Mobile and desktop** redirect URI: `https://login.microsoftonline.com/common/oauth2/nativeclient`
3. Grant **Delegated** API permissions for Microsoft Graph:
   - `User.Read`, `Mail.Read`, `Calendars.Read`, `Notes.Read`, `Chat.Read`, `offline_access`

```env
MS_CLIENT_ID=your-azure-app-client-id
MS_TENANT_ID=common   # or your org tenant ID
```

### OpenRouter (Claude)

```env
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=anthropic/claude-sonnet-4-6
```

### Frappe HRMS (FlexHR)

```env
FLEXHR_URL=https://your-company.flexhr.com
FLEXHR_USERNAME=your.email@company.com
FLEXHR_PASSWORD=your-password
FLEXHR_EMPLOYEE=EMP001
FLEXHR_COMPANY=Your Company Name
FLEXHR_WORK_STATUS=Working From Office

# API user for timesheet submission (needs Create on Timesheet doctype)
FLEXHR_API_USERNAME=api-user@company.com
FLEXHR_API_PASSWORD=api-user-password
```

### Optional

```env
TIMEZONE=Asia/Kolkata         # defaults to UTC
NOTES_FOLDER=~/OneDrive/Notes # local folder with .txt/.md/.docx/image files
```

## Project structure

```
agent.py                    # CLI entry point (typer)
src/
  collectors/
    graph_client.py         # MSAL device-flow auth + Graph API
    calendar.py             # Outlook calendar events
    emails.py               # Outlook emails
    teams.py                # Teams chats (personal/group)
    onenote.py              # OneNote pages + OCR of images
    local_notes.py          # Local folder notes
  ai/
    processor.py            # Claude tool-use → structured entries
  flexhr/
    client.py               # Frappe HRMS REST client
  ui/
    preview.py              # Rich terminal review UI
  config.py                 # Env var loading
```

## Notes

- Teams reads personal and group chats (`Chat.Read`); channel messages require admin consent
- OneNote images are OCR'd via Claude vision (up to 4 images per page)
- The OAuth token is cached in `.token_cache.json` (excluded from git) — delete it to re-authenticate

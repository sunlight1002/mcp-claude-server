# MCP Claude Server

Unified MCP gateway for Lee Associates South Florida. One process serves four independent MCP servers behind a single domain:

| Path | Server | Description |
| --- | --- | --- |
| `/enformion` | EnformionGO | People and contact data lookups |
| `/zoominfo` | ZoomInfo | Contact and company enrich/search |
| `/parcelscraper` | Parcel Scraper | Property parcel enrichment automation |
| `/adminsite` | Admin Site | Property intelligence and CRE analysis |

**Production URL:** `https://mcp.claude.lee-associates-southflorida.com`

## Tools

### `/enformion`

| Tool | Description |
| --- | --- |
| `contact_enrichment` | Enrich a contact; returns best matching person |
| `person_search` | Search for people with optional includes |
| `caller_id` | Caller identity lookup for a phone number |
| `reverse_phone_search` | Find people associated with a phone number |
| `address_id` | Validate and identify an address |

### `/zoominfo`

| Tool | Description |
| --- | --- |
| `enrich_contact` | Enrich a contact by email, name, phone, or person ID |
| `enrich_company` | Enrich a company by name, website, or company ID |
| `search_contact` | Search contacts by name, company, email, or title |
| `search_company` | Search companies by name, website, state, or country |

### `/parcelscraper`

Blocking tools wait for the automation job, download the Supabase result file when needed, and return parsed parcel records instead of only a file link.

| Tool | Description |
| --- | --- |
| `scrape_parcels` | **Preferred.** Scrape parcel IDs, wait, return full records (propertyInfo, Sunbiz `company_owner_name`, EnformionGO `nameSearchResult`) |
| `scrape_dade_records` | **Preferred.** Scrape Miami-Dade Clerk records and return full enrichment records |
| `get_scrape_results` | Resolve full records from a finished jobId or Supabase file URL |
| `get_scrape_status` | Poll job status; when complete returns full Sunbiz + EnformionGO records |
| `start_parcel_scrape` | Start async parcel scrape without waiting (manual control) |
| `start_dade_scrape` | Start async Dade scrape without waiting (manual control) |
| `get_lee_associates_link` | Look up Lee Associates listing/PDF links for an address |

### `/adminsite`

| Tool | Description |
| --- | --- |
| `property_proximity` | Team-map proximity analysis for a property |
| `sales_comps` | AI sales comparables |
| `lease_comps` | AI lease comparables |
| `investment_rating` | 0–5 investment rating and summary |
| `call_script` | Owner call script with cap-rate math |
| `costar_changes` | CoStar tenant/rent roll changes |
| `extract_lease_rate` | Extract rent/SF from property OM documents |
| `active_prospect_web_scan` | Web diligence scan with source links |

## Setup

```bash
cd mcp-claude-server
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
```

## Run locally

```bash
python server.py
```

Health check: `http://127.0.0.1:8000/health`

MCP endpoints:

- `http://127.0.0.1:8000/enformion`
- `http://127.0.0.1:8000/zoominfo`
- `http://127.0.0.1:8000/parcelscraper`
- `http://127.0.0.1:8000/adminsite`

## Connect to Claude Desktop

Add each server to `claude_desktop_config.json` (Streamable HTTP transport):

```json
{
  "mcpServers": {
    "enformion": {
      "url": "https://mcp.claude.lee-associates-southflorida.com/enformion"
    },
    "zoominfo": {
      "url": "https://mcp.claude.lee-associates-southflorida.com/zoominfo"
    },
    "parcelscraper": {
      "url": "https://mcp.claude.lee-associates-southflorida.com/parcelscraper"
    },
    "adminsite": {
      "url": "https://mcp.claude.lee-associates-southflorida.com/adminsite"
    }
  }
}
```

## Deploy on Ubuntu

```bash
# 1. Clone and install
cd /home/ubuntu
git clone <repo-url> mcp-claude-server
cd mcp-claude-server
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env   # fill in credentials

# 2. Start with PM2
pm2 start ecosystem.config.cjs
pm2 save

# 3. Configure nginx
sudo cp deploy/nginx-mcp-claude.conf /etc/nginx/sites-available/mcp-claude
sudo ln -s /etc/nginx/sites-available/mcp-claude /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# 4. TLS with certbot
sudo certbot --nginx -d mcp.claude.lee-associates-southflorida.com
```

## Environment variables

See [`.env.example`](.env.example) for the full list. Key groups:

- **Shared:** `MCP_HOST`, `MCP_PORT`, `MCP_DOMAIN`
- **Enformion:** `ENFORMIONGO_ACCESS_PROFILE_NAME`, `ENFORMIONGO_ACCESS_PROFILE_PASSWORD`
- **ZoomInfo:** `ZOOMINFO_USERNAME`, `ZOOMINFO_PASSWORD` (or PKI credentials)
- **Parcelscraper:** `PARCELSCRAPER_API_URL`
- **Adminsite:** `ADMINSITE_API_URL`, optional `ADMINSITE_API_KEY`

## Architecture

```
Claude / MCP client
        │
        ▼
nginx (mcp.claude.lee-associates-southflorida.com)
        │
        ▼
uvicorn (server.py) ── Starlette app on 127.0.0.1:8000
        ├── /enformion     → EnformionGO API
        ├── /zoominfo      → ZoomInfo API
        ├── /parcelscraper → automation service
        └── /adminsite     → admin Next.js /api
```

Each path is an independent `FastMCP` instance mounted with `streamable_http_path="/"`. A combined Starlette lifespan manages all session managers.

# GSC Performance Analyzer

Local Python tool that pulls Google Search Console data for www.highlevel.ai and generates an actionable content strategy report.

## Setup (One-Time)

### Step 1: Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click the project dropdown (top left) and select **New Project**
3. Name it `HighlevelAI GSC Tool` and click **Create**
4. Make sure the new project is selected in the dropdown

### Step 2: Enable the Search Console API

1. Go to **APIs & Services > Library** (left sidebar)
2. Search for `Google Search Console API`
3. Click on it, then click **Enable**

### Step 3: Configure OAuth Consent Screen

1. Go to **APIs & Services > OAuth consent screen**
2. Select **External** and click **Create**
3. Fill in:
   - App name: `GSC Analyzer`
   - User support email: your email
   - Developer contact: your email
4. Click **Save and Continue**
5. On the **Scopes** screen, click **Add or Remove Scopes**
6. Search for `webmasters.readonly` and check it
7. Click **Update**, then **Save and Continue**
8. On the **Test users** screen, click **Add users**
9. Add the Google email that owns your GSC property
10. Click **Save and Continue**, then **Back to Dashboard**

### Step 4: Create OAuth Credentials

1. Go to **APIs & Services > Credentials**
2. Click **Create Credentials > OAuth client ID**
3. Application type: **Desktop app**
4. Name: `GSC Analyzer Desktop`
5. Click **Create**
6. Click **Download JSON**
7. Rename the downloaded file to `client_secret.json`
8. Move it to: `scripts/.credentials/client_secret.json`

### Step 5: Install Python Dependencies

```bash
pip3 install -r scripts/requirements.txt
```

## Usage

### Basic Run (last 28 days)

```bash
cd /Users/josh/Downloads/AI\ Projects/HighlevelAI/site
python3 scripts/gsc_analyze.py
```

On the first run, a browser window will open asking you to sign in with Google and grant read-only Search Console access. After that, the token is saved and future runs authenticate automatically.

### Save Report to File

```bash
python3 scripts/gsc_analyze.py --output reports/report.md
```

### Custom Date Range

```bash
python3 scripts/gsc_analyze.py --days 14
```

### Lower Threshold for New Sites

```bash
python3 scripts/gsc_analyze.py --min-impressions 1
```

### All Options

```
--days N              Lookback period in days (default: 28)
--min-impressions N   Minimum impressions to include (default: 10)
--output PATH         Save report to file (relative to scripts/)
--site-url URL        Override GSC property URL
```

## What the Report Tells You

| Section | What It Shows | What To Do |
|---------|--------------|-----------|
| Top Queries | Your best-performing search terms | Know what's working |
| Top Pages | Your best-performing pages | Double down on these |
| Low-Hanging Fruit | Queries at position 5-20 | Optimize these pages to rank higher |
| CTR Optimization | Pages with low click-through rates | Rewrite titles and meta descriptions |
| Content Gaps | Queries you rank for but don't target | Create new pages for these topics |
| New Article Ideas | Grouped query clusters | Prioritized content roadmap |

## Troubleshooting

**"client_secret.json not found"**
You need to complete Steps 1-4 above and place the file at `scripts/.credentials/client_secret.json`

**"Property not found in your GSC account"**
The script will list your available properties. Use `--site-url` with the correct one. Common formats:
- URL prefix: `https://www.highlevel.ai/`
- Domain: `sc-domain:highlevel.ai`

**"No data found for this period"**
GSC needs 2-4 weeks to accumulate data for new sites. Try `--days 7` or `--min-impressions 1`.

**Token expired / auth errors**
Delete `scripts/.credentials/token.json` and run again to re-authenticate.

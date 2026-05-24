"""
Run this once to import Instagram cookies from your real Chrome browser.

Steps:
1. Open Chrome and log into instagram.com
2. Install "Cookie-Editor" extension: https://cookie-editor.com
3. Go to instagram.com, click the Cookie-Editor icon
4. Click "Export" -> "Export as JSON"  (copies to clipboard)
5. Paste the JSON below when prompted, then press Enter twice

The cookies will be saved to instagram_session.json and the bot
will use them for all future runs without needing to log in.
"""

import json
import sys
from pathlib import Path

SESSION_FILE = Path("./instagram_session.json")

print("Paste your Instagram cookies JSON (from Cookie-Editor export),")
print("then press Enter twice when done:\n")

lines = []
try:
    while True:
        line = input()
        if line == "" and lines and lines[-1] == "":
            break
        lines.append(line)
except EOFError:
    pass

raw = "\n".join(lines).strip()
if not raw:
    print("No input received. Exiting.")
    sys.exit(1)

try:
    cookies = json.loads(raw)
except json.JSONDecodeError as e:
    print(f"Invalid JSON: {e}")
    sys.exit(1)

# Normalize to Playwright format (Cookie-Editor exports slightly different keys)
playwright_cookies = []
for c in cookies:
    pc = {
        "name":     c.get("name", ""),
        "value":    c.get("value", ""),
        "domain":   c.get("domain", ".instagram.com"),
        "path":     c.get("path", "/"),
        "httpOnly": c.get("httpOnly", False),
        "secure":   c.get("secure", True),
        "sameSite": c.get("sameSite", "None"),
    }
    if "expirationDate" in c:
        pc["expires"] = int(c["expirationDate"])
    playwright_cookies.append(pc)

SESSION_FILE.write_text(json.dumps(playwright_cookies, indent=2))
print(f"\nSaved {len(playwright_cookies)} cookies to {SESSION_FILE}")
print("The bot will now use these cookies and skip login entirely.")

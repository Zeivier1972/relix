"""Poll /status + log file every 30 seconds for up to 10 minutes."""
import time, json, urllib.request, sys, os

LOG = r"c:\Users\zreye\OneDrive\Desktop\relix\relix.log"
STATUS_URL = "http://127.0.0.1:8001/status"
MAX_SECONDS = 600
INTERVAL = 30

def tail(path, n=40):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except Exception as e:
        return f"(log read error: {e})"

def get_status():
    try:
        with urllib.request.urlopen(STATUS_URL, timeout=5) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}

start = time.time()
last_log_pos = 0
tick = 0

print(f"[Monitor] Started at {time.strftime('%H:%M:%S')} — polling every {INTERVAL}s for up to {MAX_SECONDS}s\n", flush=True)

while time.time() - start < MAX_SECONDS:
    time.sleep(INTERVAL)
    tick += 1
    elapsed = int(time.time() - start)
    ts = time.strftime('%H:%M:%S')

    status_data = get_status()
    job = status_data.get("job_status", status_data)
    job_state = job.get("status", "?")
    sources = job.get("sources", {})

    print(f"\n{'='*60}", flush=True)
    print(f"T+{elapsed}s [{ts}]  job={job_state}", flush=True)
    print(f"  scraped={job.get('leads_scraped',0)}  qualified={job.get('leads_qualified',0)}  HOT={job.get('hot_leads',0)}", flush=True)
    print(f"  sources: ig_ht={sources.get('instagram_hashtags',0)} ig_cm={sources.get('instagram_comments',0)} fb={sources.get('facebook_groups',0)} tt={sources.get('tiktok_comments',0)} reddit={sources.get('reddit',0)}", flush=True)

    log_now = tail(LOG, 25)
    print("--- recent log ---", flush=True)
    print(log_now, flush=True)

    if job_state == "idle" and tick > 1:
        print("\n[Monitor] Job finished — final results above.", flush=True)
        sys.exit(0)

print("\n[Monitor] 10-minute timeout reached.", flush=True)

# Final summary from log
print("\n=== FINAL LOG (last 60 lines) ===", flush=True)
print(tail(LOG, 60), flush=True)

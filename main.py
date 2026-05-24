from dotenv import load_dotenv
load_dotenv()

import asyncio
import os
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
import uvicorn

from database import LeadDatabase
from scrapers.instagram_playwright import InstagramPlaywrightScraper
from scrapers.facebook_playwright import FacebookPlaywrightScraper
from scrapers.tiktok_playwright import TikTokPlaywrightScraper
from scrapers.reddit_scraper import RedditScraper
from qualifier.claude_qualifier import ClaudeLeadQualifier
from pipeline.twilio_alert import TwilioWhatsAppAlerts
from pipeline.lofty import LoftyCRMClient
from pipeline.phantom import (run_dm_bot, get_dm_log, _count_dms_today,
                              send_dm_to_lead, build_dm_preview)

PORT = int(os.getenv("PORT", 8000))
DB_PATH = os.getenv("DB_PATH", "./leads.db")

# ── Tier 1 — Every 6 hours (highest buyer intent) ─────────────────────────
FACEBOOK_TIER1 = [
    "https://www.facebook.com/groups/colombianosenflorida",
    "https://www.facebook.com/groups/colombianosenmiami",
    "https://www.facebook.com/groups/preconstruccionflorida",
    "https://www.facebook.com/groups/miamirealestate",
    "https://www.facebook.com/groups/homesteadfloridarealstate",
    "https://www.facebook.com/groups/latinosenflorida",
    "https://www.facebook.com/groups/inversionistascolombianos",
]

IG_HASHTAGS_TIER1 = [
    "quieromprarencasa",
    "comprarcasaenmiami",
    "colombianosbuscandocasa",
    "preconstruccionmiami",
    "primeracasaenusa",
]

IG_ACCOUNTS_TIER1 = [
    "catherinegomez_realtor",
    "oneworldproperties",
    "onesothebysrealty",
    "compassflorida",
    "relatedgroup",
]

REDDIT_TIER1 = ["Miami", "Colombia", "FirstTimeHomeBuyer", "realestateinvesting"]

# ── Tier 2 — Every 12 hours (medium intent) ────────────────────────────────
FACEBOOK_TIER2 = [
    "https://www.facebook.com/groups/miamihomebuyers",
    "https://www.facebook.com/groups/miamiinvestors",
    "https://www.facebook.com/groups/brickellmiamirealestate",
    "https://www.facebook.com/groups/wynwoodhomes",
    "https://www.facebook.com/groups/miamibeachhomes",
    "https://www.facebook.com/groups/coralgablesproperties",
    "https://www.facebook.com/groups/doralfloridarealstate",
    "https://www.facebook.com/groups/hialeahrealestate",
    "https://www.facebook.com/groups/kendallfloridarealstate",
    "https://www.facebook.com/groups/sunriseflrealestate",
    "https://www.facebook.com/groups/pembrokepiresrealestate",
    "https://www.facebook.com/groups/fortlauderdalerealestate",
    "https://www.facebook.com/groups/bocaratonhomes",
    "https://www.facebook.com/groups/westpalmbeachrealestate",
    "https://www.facebook.com/groups/wellingtonflhomes",
    "https://www.facebook.com/groups/homesteadfloridahomes",
    "https://www.facebook.com/groups/orlandorealestate",
    "https://www.facebook.com/groups/orlandohomebuyers",
    "https://www.facebook.com/groups/tamparealestate",
    "https://www.facebook.com/groups/tampahomebuyers",
]

IG_HASHTAGS_TIER2 = [
    # City-specific
    "miamibeachhomes", "brickellmiami", "wynwoodhomes",
    "homesteadflorida", "coralgables", "doralfl",
    "hialeahhomes", "kendallfl", "sunrisefl",
    "fortlauderdalehomes", "bocaratonrealestate",
    "westpalmbeachhomes", "orlandohomes",
    # Pre-construction
    "preconstruccionflorida", "nuevaconstruccion", "casasnuevasmiami",
    "newconstructionmiami", "newconstructionflorida", "preconstruccionusa",
    "preventa",
]

IG_ACCOUNTS_TIER2 = [
    "totalbankrealty",
    "miamirealestate",
    "luxurymiamirealestate",
    "preconstruccionmiami",
    "brickellrealty",
    "miamicondoinvestments",
]

REDDIT_TIER2 = ["orlando", "WestPalmBeach", "Broward"]

# ── Tier 3 — Daily at 6 am (broad market) ─────────────────────────────────
FACEBOOK_TIER3 = [
    "https://www.facebook.com/groups/colombianosenorlando",
    "https://www.facebook.com/groups/colombianosinvertendousa",
    "https://www.facebook.com/groups/latinosinmiami",
    "https://www.facebook.com/groups/hispanosenmiami",
    "https://www.facebook.com/groups/venezolanosenflorida",
    "https://www.facebook.com/groups/venezolanosenmiami",
    "https://www.facebook.com/groups/argentinosenflorida",
    "https://www.facebook.com/groups/mexicanosenflorida",
    "https://www.facebook.com/groups/puertorriquenosenflorida",
    "https://www.facebook.com/groups/preconstruccionmiami",
    "https://www.facebook.com/groups/floridanewconstruction",
    "https://www.facebook.com/groups/miamiNewConstruction",
    "https://www.facebook.com/groups/preconstruccionusa",
    "https://www.facebook.com/groups/newhomesflorida",
    "https://www.facebook.com/groups/nuevascasasenmiami",
    "https://www.facebook.com/groups/nuevaconstruccionmiami",
    "https://www.facebook.com/groups/floridainvestors",
    "https://www.facebook.com/groups/miamiinvestmentproperties",
    "https://www.facebook.com/groups/southfloridainvestors",
    "https://www.facebook.com/groups/realestateinvestorsflorida",
    "https://www.facebook.com/groups/inversionistasflorida",
    "https://www.facebook.com/groups/inversionistasmiami",
    "https://www.facebook.com/groups/airbnbmiami",
    "https://www.facebook.com/groups/shorttermrentalsflorida",
]

IG_HASHTAGS_TIER3 = [
    "buscandocasaenmiami", "mudanzaaflorida", "latinosbuscandocasa",
    "comprarcasausa", "inversionistascolombianos", "vivireenflorida",
    "comprarcasaenflorida", "casapropia", "casapropiaflorida",
    "hogarpropioflorida", "colombianosenmiami", "colombianosenusa",
]

REDDIT_TIER3 = ["personalfinance", "realestate", "AirBnB", "fatFIRE", "legaladvice"]

# ── App setup ──────────────────────────────────────────────────────────────
app = FastAPI(title="RELIX Lead Generation System")
db = LeadDatabase(DB_PATH)
qualifier = ClaudeLeadQualifier()
scheduler = AsyncIOScheduler()

_job_running = False

job_status = {
    "tier1": {"status": "idle", "last_run": None, "leads_found": 0},
    "tier2": {"status": "idle", "last_run": None, "leads_found": 0},
    "tier3": {"status": "idle", "last_run": None, "leads_found": 0},
    "total_qualified": 0,
    "hot_leads": 0,
}


# ── Scraper helpers ────────────────────────────────────────────────────────

def _save_leads(leads, source_key, source_counts):
    saved = 0
    for lead in leads:
        try:
            lead_id = db.add_lead(
                name=lead.get("name"),
                property_url=lead.get("property_url"),
                source=lead.get("source", source_key),
                raw_data=lead.get("raw_data"),
            )
            if lead_id:
                saved += 1
                print(f"  [+] {source_key}: {lead.get('name')}")
        except Exception as e:
            print(f"  [-] {source_key} save error: {e}")
    source_counts[source_key] = source_counts.get(source_key, 0) + saved
    return saved


async def _run_instagram(source_counts, hashtags, accounts):
    print(f"[Instagram] {len(hashtags)} hashtags, {len(accounts)} accounts...")
    try:
        scraper = InstagramPlaywrightScraper()
        leads = await scraper.scrape_all(hashtags=hashtags, comment_accounts=accounts)
        ht = [l for l in leads if l.get("source") == "instagram_hashtags"]
        cm = [l for l in leads if l.get("source") == "instagram_comments"]
        total = _save_leads(ht, "instagram_hashtags", source_counts)
        total += _save_leads(cm, "instagram_comments", source_counts)
        print(f"[Instagram] Done: {total} leads")
        return total
    except Exception as e:
        print(f"[-] Instagram scraper failed: {e}")
        return 0


async def _run_facebook(source_counts, group_urls):
    print(f"[Facebook] {len(group_urls)} groups...")
    try:
        scraper = FacebookPlaywrightScraper()
        leads = await scraper.scrape_all(group_urls=group_urls)
        total = _save_leads(leads, "facebook_groups", source_counts)
        print(f"[Facebook] Done: {total} leads")
        return total
    except Exception as e:
        print(f"[-] Facebook scraper failed: {e}")
        return 0


async def _run_tiktok(source_counts):
    print("[TikTok] Searching buyer-intent keywords...")
    try:
        scraper = TikTokPlaywrightScraper()
        leads = await scraper.scrape_all()
        total = _save_leads(leads, "tiktok_comments", source_counts)
        print(f"[TikTok] Done: {total} leads")
        return total
    except Exception as e:
        print(f"[-] TikTok scraper failed: {e}")
        return 0


async def _run_reddit(source_counts, subreddits):
    print(f"[Reddit] {len(subreddits)} subreddits...")
    reddit = RedditScraper()
    try:
        leads = await reddit.scrape_all(subreddits=subreddits)
        total = _save_leads(leads, "reddit", source_counts)
        print(f"[Reddit] Done: {total} leads")
        return total
    except Exception as e:
        print(f"[-] Reddit scraper failed: {e}")
        return 0
    finally:
        await reddit.close()


async def _qualify_and_alert():
    """Qualify all new leads and send Twilio SMS for HOT/WARM."""
    twilio = TwilioWhatsAppAlerts()
    new_leads = db.get_new_leads(limit=150)
    qualified = 0
    hot = 0
    try:
        for lead in new_leads:
            try:
                score, reasoning, analysis = qualifier.qualify_lead(lead)
                db.add_qualification(lead_id=lead["id"], score=score,
                                     reasoning=reasoning, ai_analysis=analysis)
                qualified += 1
                print(f"  [Q] {lead['name']}: {score}")
                if score in ("HOT", "WARM"):
                    if score == "HOT":
                        hot += 1
                    await twilio.send_hot_lead_alert({
                        **lead,
                        "qualification_score": score,
                        "qualification_reasoning": reasoning,
                    })
            except Exception as e:
                print(f"  [-] Qualify error for {lead.get('name')}: {e}")
    finally:
        await twilio.close()
    return qualified, hot


# ── Core scrape+qualify engine ─────────────────────────────────────────────

async def _scrape_and_qualify(tier: str, fb_groups, ig_hashtags,
                               ig_accounts, rd_subreddits,
                               include_tiktok: bool = False):
    global _job_running, job_status

    if _job_running:
        print(f"[RELIX] Job already running — skipping {tier}.")
        return

    _job_running = True
    job_status[tier]["status"] = "running"
    job_status[tier]["last_run"] = datetime.now().isoformat()
    print(f"\n[{datetime.now()}] Starting {tier.upper()} scan...")

    source_counts = {
        "facebook_groups": 0, "instagram_hashtags": 0,
        "instagram_comments": 0, "tiktok_comments": 0, "reddit": 0,
    }

    try:
        ig_count = await _run_instagram(source_counts, ig_hashtags, ig_accounts)
        fb_count = await _run_facebook(source_counts, fb_groups)
        tt_count = await _run_tiktok(source_counts) if include_tiktok else 0
        rd_count = await _run_reddit(source_counts, rd_subreddits)
        total_scraped = ig_count + fb_count + tt_count + rd_count

        print(f"[{tier.upper()}] Scraped {total_scraped} leads — qualifying...")
        qualified, hot = await _qualify_and_alert()

        job_status[tier]["status"] = "idle"
        job_status[tier]["leads_found"] = total_scraped
        job_status["total_qualified"] = job_status.get("total_qualified", 0) + qualified
        job_status["hot_leads"] = job_status.get("hot_leads", 0) + hot

        print(f"[{tier.upper()}] Done — scraped={total_scraped} qualified={qualified} hot={hot}")
        print(f"  Sources: {source_counts}")

    except Exception as e:
        job_status[tier]["status"] = "error"
        print(f"[ERROR] {tier} failed: {e}")
    finally:
        _job_running = False


# ── Tier job wrappers ──────────────────────────────────────────────────────

async def scrape_tier1_job():
    await _scrape_and_qualify(
        tier="tier1",
        fb_groups=FACEBOOK_TIER1,
        ig_hashtags=IG_HASHTAGS_TIER1,
        ig_accounts=IG_ACCOUNTS_TIER1,
        rd_subreddits=REDDIT_TIER1,
        include_tiktok=True,
    )


async def scrape_tier2_job():
    await _scrape_and_qualify(
        tier="tier2",
        fb_groups=FACEBOOK_TIER2,
        ig_hashtags=IG_HASHTAGS_TIER2,
        ig_accounts=IG_ACCOUNTS_TIER2,
        rd_subreddits=REDDIT_TIER2,
        include_tiktok=False,
    )


async def scrape_tier3_job():
    await _scrape_and_qualify(
        tier="tier3",
        fb_groups=FACEBOOK_TIER3,
        ig_hashtags=IG_HASHTAGS_TIER3,
        ig_accounts=[],
        rd_subreddits=REDDIT_TIER3,
        include_tiktok=False,
    )


# ── Scheduler ──────────────────────────────────────────────────────────────

def schedule_jobs():
    # Tier 1: every 6 hours + immediate startup run
    scheduler.add_job(scrape_tier1_job, IntervalTrigger(hours=6),
                      id="tier1_interval", name="Tier 1 — High Intent (6h)")
    scheduler.add_job(scrape_tier1_job,
                      id="tier1_init", name="Tier 1 — Initial Run")

    # Tier 2: every 12 hours
    scheduler.add_job(scrape_tier2_job, IntervalTrigger(hours=12),
                      id="tier2_interval", name="Tier 2 — City/Pre-Con (12h)")

    # Tier 3: daily at 6 am
    scheduler.add_job(scrape_tier3_job, CronTrigger(hour=6, minute=0),
                      id="tier3_daily", name="Tier 3 — Broad Market (6am)")

    scheduler.start()


@app.on_event("startup")
async def startup_event():
    print("RELIX Lead Generation System starting...")
    schedule_jobs()
    print("[+] Scheduler: Tier1=6h | Tier2=12h | Tier3=daily@6am")


@app.on_event("shutdown")
async def shutdown_event():
    if scheduler.running:
        scheduler.shutdown()
    print("[+] System shutdown complete")


# ── API endpoints ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    with open("dashboard.html", encoding="utf-8") as f:
        return f.read()


@app.get("/api/dashboard")
async def api_dashboard():
    return {
        "stats": db.get_stats(),
        "leads": db.get_leads_with_scores(limit=200),
    }


@app.post("/scan/now")
async def scan_now(background_tasks: BackgroundTasks):
    background_tasks.add_task(scrape_tier1_job)
    return {"status": "Tier 1 scan triggered", "timestamp": datetime.now().isoformat()}


@app.post("/scan/tier2")
async def scan_tier2(background_tasks: BackgroundTasks):
    background_tasks.add_task(scrape_tier2_job)
    return {"status": "Tier 2 scan triggered", "timestamp": datetime.now().isoformat()}


@app.post("/scan/tier3")
async def scan_tier3(background_tasks: BackgroundTasks):
    background_tasks.add_task(scrape_tier3_job)
    return {"status": "Tier 3 scan triggered", "timestamp": datetime.now().isoformat()}


@app.get("/status")
async def get_status():
    return {"job_status": job_status, "timestamp": datetime.now().isoformat()}


@app.get("/leads")
async def get_leads(limit: int = 100):
    leads = db.get_all_leads(limit=limit)
    return {"count": len(leads), "leads": leads}


@app.get("/leads/hot")
async def get_hot_leads():
    hot_leads = db.get_hot_leads()
    return {"count": len(hot_leads), "leads": hot_leads}


@app.post("/trigger-job")
async def trigger_job(background_tasks: BackgroundTasks):
    background_tasks.add_task(scrape_tier1_job)
    return {"status": "Tier 1 triggered", "timestamp": datetime.now().isoformat()}


@app.post("/trigger-phantom")
async def trigger_phantom(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_dm_bot)
    return {"status": "Phantom DM bot triggered", "timestamp": datetime.now().isoformat()}


@app.get("/phantom/status")
async def phantom_status():
    return {
        "dms_today": _count_dms_today(),
        "daily_limit": 8,
        "recent_log": get_dm_log(limit=50),
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/dm-status")
async def api_dm_status():
    return {
        "stats": db.get_dm_stats(),
        "leads": db.get_leads_with_dm_status(limit=500),
        "dms_today": _count_dms_today(),
        "daily_limit": 8,
    }


@app.get("/api/dm/preview/{lead_id}")
async def dm_preview(lead_id: int):
    lead = db.get_lead(lead_id)
    if not lead:
        return {"error": "Lead not found"}
    return {
        "lead_id": lead_id,
        "username": lead.get("name"),
        "message": build_dm_preview(lead.get("name", ""), lead.get("source", "")),
    }


@app.post("/api/dm/send/{lead_id}")
async def dm_send_one(lead_id: int, background_tasks: BackgroundTasks):
    lead = db.get_lead(lead_id)
    if not lead:
        return {"error": "Lead not found"}
    ig_username = lead.get("name", "")
    source = lead.get("source", "")
    background_tasks.add_task(send_dm_to_lead, lead_id, ig_username, source)
    return {
        "status": "queued",
        "username": ig_username,
        "message_preview": build_dm_preview(ig_username, source)[:120],
    }


@app.post("/leads/{lead_id}/push-to-lofty")
async def push_lead_to_lofty(lead_id: int, contact: dict):
    lead = db.get_lead(lead_id)
    if not lead:
        return {"error": f"Lead {lead_id} not found"}, 404

    enriched = {
        **lead,
        "phone": contact.get("phone") or lead.get("phone"),
        "email": contact.get("email") or lead.get("email"),
        "name": contact.get("name") or lead.get("name"),
        "qualification_score": contact.get("qualification_score") or lead.get("qualification_score") or "WARM",
    }

    lofty = LoftyCRMClient()
    result = await lofty.create_lead(enriched)
    await lofty.close()

    if result:
        return {"status": "pushed", "lead_id": lead_id, "name": enriched.get("name")}
    return {"status": "skipped", "reason": "webhook not configured"}


@app.post("/leads/push-to-lofty")
async def push_lead_by_username(contact: dict):
    """
    ManyChat webhook — called after a prospect replies with their contact info.
    Body: {"username": "...", "name": "...", "phone": "...", "email": "..."}
    """
    username = (contact.get("username") or "").strip().lstrip("@")
    if not username:
        return {"error": "username is required"}, 400

    lead = db.get_lead_by_username(username)
    if not lead:
        return {"error": f"No lead found for username '{username}'"}, 404

    enriched = {
        **lead,
        "phone": contact.get("phone") or lead.get("phone"),
        "email": contact.get("email") or lead.get("email"),
        "name": contact.get("name") or lead.get("name") or username,
        "qualification_score": lead.get("qualification_score") or "WARM",
    }

    lofty = LoftyCRMClient()
    result = await lofty.create_lead(enriched)
    await lofty.close()

    if result:
        return {"status": "pushed", "lead_id": lead.get("id"), "name": enriched.get("name")}
    return {"status": "skipped", "reason": "webhook not configured"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")

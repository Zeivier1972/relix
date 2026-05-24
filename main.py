from dotenv import load_dotenv
load_dotenv()

import asyncio
import os
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import uvicorn

from database import LeadDatabase
from scrapers.instagram_playwright import InstagramPlaywrightScraper
from scrapers.facebook_playwright import FacebookPlaywrightScraper
from scrapers.tiktok_playwright import TikTokPlaywrightScraper
from scrapers.reddit_scraper import RedditScraper
from qualifier.claude_qualifier import ClaudeLeadQualifier
from pipeline.twilio_alert import TwilioWhatsAppAlerts
from pipeline.lofty import LoftyCRMClient
from pipeline.phantom import run_dm_bot, get_dm_log, _count_dms_today

PORT = int(os.getenv("PORT", 8000))
DB_PATH = os.getenv("DB_PATH", "./leads.db")

app = FastAPI(title="RELIX Lead Generation System")
db = LeadDatabase(DB_PATH)
qualifier = ClaudeLeadQualifier()
scheduler = AsyncIOScheduler()

_job_running = False

job_status = {
    "last_run": None,
    "next_run": None,
    "status": "idle",
    "leads_scraped": 0,
    "leads_qualified": 0,
    "hot_leads": 0,
    "sources": {
        "facebook_groups": 0,
        "instagram_hashtags": 0,
        "instagram_comments": 0,
        "tiktok_comments": 0,
        "reddit": 0,
    },
}


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


async def _run_instagram(source_counts):
    print("[Instagram] Scraping hashtags and comments...")
    try:
        scraper = InstagramPlaywrightScraper()
        leads = await scraper.scrape_all()
        ht = [l for l in leads if l.get("source") == "instagram_hashtags"]
        cm = [l for l in leads if l.get("source") == "instagram_comments"]
        total = _save_leads(ht, "instagram_hashtags", source_counts)
        total += _save_leads(cm, "instagram_comments", source_counts)
        print(f"[Instagram] Done: {total} leads")
        return total
    except Exception as e:
        print(f"[-] Instagram scraper failed: {e}")
        return 0


async def _run_facebook(source_counts):
    print("[Facebook] Scraping groups...")
    try:
        scraper = FacebookPlaywrightScraper()
        leads = await scraper.scrape_all()
        total = _save_leads(leads, "facebook_groups", source_counts)
        print(f"[Facebook] Done: {total} leads")
        return total
    except Exception as e:
        print(f"[-] Facebook scraper failed: {e}")
        return 0


async def _run_tiktok(source_counts):
    print("[TikTok] Scraping accounts and search...")
    try:
        scraper = TikTokPlaywrightScraper()
        leads = await scraper.scrape_all()
        total = _save_leads(leads, "tiktok_comments", source_counts)
        print(f"[TikTok] Done: {total} leads")
        return total
    except Exception as e:
        print(f"[-] TikTok scraper failed: {e}")
        return 0


async def _run_reddit(source_counts):
    print("[Reddit] Scraping buyer-intent posts...")
    reddit = RedditScraper()
    try:
        leads = await reddit.scrape_all()
        total = _save_leads(leads, "reddit", source_counts)
        print(f"[Reddit] Done: {total} leads")
        return total
    except Exception as e:
        print(f"[-] Reddit scraper failed: {e}")
        return 0
    finally:
        await reddit.close()


async def scrape_leads_job():
    global job_status, _job_running

    if _job_running:
        print("[RELIX] Job already running — skipping.")
        return

    _job_running = True
    job_status["status"] = "running"
    job_status["last_run"] = datetime.now().isoformat()

    print(f"\n[{datetime.now()}] Starting RELIX lead scraping job...")

    source_counts = {
        "facebook_groups": 0,
        "instagram_hashtags": 0,
        "instagram_comments": 0,
        "tiktok_comments": 0,
        "reddit": 0,
    }

    try:
        twilio = TwilioWhatsAppAlerts()

        # Run Playwright scrapers sequentially (one Chromium at a time),
        # then Reddit concurrently since it's httpx-based and lightweight.
        ig_count = await _run_instagram(source_counts)
        fb_count = await _run_facebook(source_counts)
        tt_count = await _run_tiktok(source_counts)
        rd_count = await _run_reddit(source_counts)
        leads_scraped = ig_count + fb_count + tt_count + rd_count

        # Qualify new leads
        print("[Qualification] Processing new leads...")
        new_leads = db.get_new_leads(limit=100)

        leads_qualified = 0
        hot_leads = 0

        for lead in new_leads:
            try:
                score, reasoning, analysis = qualifier.qualify_lead(lead)
                db.add_qualification(
                    lead_id=lead["id"],
                    score=score,
                    reasoning=reasoning,
                    ai_analysis=analysis,
                )
                leads_qualified += 1
                print(f"  [+] Qualified {lead['name']}: {score}")

                if score in ("HOT", "WARM"):
                    lead_with_score = {
                        **lead,
                        "qualification_score": score,
                        "qualification_reasoning": reasoning,
                    }

                    if score == "HOT":
                        hot_leads += 1

                    # SMS directly to the lead's phone (if available)
                    await twilio.send_hot_lead_alert(lead_with_score)

            except Exception as e:
                print(f"  [-] Error qualifying {lead.get('name')}: {e}")

        await twilio.close()

        job_status["status"] = "idle"
        job_status["leads_scraped"] = leads_scraped
        job_status["leads_qualified"] = leads_qualified
        job_status["hot_leads"] = hot_leads
        job_status["sources"] = source_counts

        print(f"\n[{datetime.now()}] Job complete!")
        print(f"  Scraped:    {leads_scraped} leads")
        print(f"  Qualified:  {leads_qualified} leads")
        print(f"  HOT leads:  {hot_leads}")
        print(f"  By source:  {source_counts}")

    except Exception as e:
        job_status["status"] = "error"
        print(f"[ERROR] Job failed: {e}")
    finally:
        _job_running = False


def schedule_jobs():
    scheduler.add_job(
        scrape_leads_job,
        trigger=IntervalTrigger(hours=6),
        id="scrape_leads_job",
        name="Lead Scraping & Qualification Job",
    )
    scheduler.add_job(
        scrape_leads_job,
        id="initial_scrape",
        name="Initial Lead Scraping",
    )
    scheduler.start()


@app.on_event("startup")
async def startup_event():
    print("RELIX Lead Generation System starting...")
    schedule_jobs()
    print("[+] Scheduler initialized (6-hour intervals)")


@app.on_event("shutdown")
async def shutdown_event():
    if scheduler.running:
        scheduler.shutdown()
    print("[+] System shutdown complete")


@app.get("/")
async def root():
    return {
        "status": "RELIX Lead Generation System",
        "version": "2.0.0",
        "job_status": job_status,
        "timestamp": datetime.now().isoformat(),
    }


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
    background_tasks.add_task(scrape_leads_job)
    return {"status": "Job triggered", "timestamp": datetime.now().isoformat()}


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
    ManyChat webhook endpoint. Called after a prospect replies with their contact info.
    Body: {"username": "...", "name": "...", "phone": "...", "email": "..."}
    Looks up the lead by Instagram username, enriches it, and pushes to Lofty.
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

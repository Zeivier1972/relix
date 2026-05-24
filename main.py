from dotenv import load_dotenv
load_dotenv()

import asyncio
import json
import os
from datetime import datetime

import httpx
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
                              send_dm_to_lead, build_dm_preview, process_dm_queue)
from pipeline.lofty import push_directly_to_lofty
from pipeline.twilio_alert import send_property_sms
from scrapers.zillow_playwright import ZillowScraper
from scrapers.realtor_playwright import RealtorScraper
from scrapers.redfin_playwright import RedfinScraper
from scrapers.preforeclosure_scraper import PreForeclosureScraper
from scrapers.public_records_scraper import PublicRecordsScraper
from scrapers.sunbiz_scraper import SunbizScraper
from scrapers.new_construction_scraper import NewConstructionScraper
from scrapers.facebook_ads_scraper import FacebookAdsLibraryScraper, top_advertisers

PORT = int(os.getenv("PORT", 8000))
DB_PATH = os.getenv("DB_PATH", "./leads.db")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "")

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
auto_dm_enabled = True          # toggled by /api/auto-dm/toggle

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


_IG_SOURCES = {"instagram_hashtags", "instagram_comments", "instagram"}


async def _qualify_and_alert():
    """Qualify all new leads, send Twilio SMS, and queue Instagram HOT/WARM for auto-DM."""
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
                    # Auto-DM queue: only Instagram leads can be DM'd
                    if lead.get("source") in _IG_SOURCES:
                        db.add_to_dm_queue(
                            lead_id=lead["id"],
                            ig_username=lead["name"],
                            source=lead["source"],
                            score=score,
                        )
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


# ── Property lead helpers ──────────────────────────────────────────────────

async def _send_n8n(lead: dict):
    """Fire-and-forget N8N webhook with property lead data."""
    if not N8N_WEBHOOK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(N8N_WEBHOOK_URL, json={
                "name": lead.get("owner_name") or "",
                "phone": lead.get("phone") or "",
                "email": lead.get("email") or "",
                "leadType": lead.get("lead_type") or "",
                "score": lead.get("score") or "",
                "source": lead.get("source") or "",
                "propertyAddress": lead.get("address") or "",
                "propertyPrice": lead.get("listing_price") or 0,
                "daysOnMarket": lead.get("days_on_market") or 0,
            })
    except Exception as e:
        print(f"[N8N] Webhook error: {e}")


async def _process_property_lead(lead: dict, lead_id: int):
    """
    After a property lead is saved:
    - If HOT or WARM and has name + (phone/email): push directly to Lofty
    - If HOT and has phone: send property SMS (9am-7pm, 30-day cooldown, 50/day limit)
    - Fire N8N webhook for any lead with contact info
    """
    score = lead.get("score", "")
    phone = lead.get("phone") or ""
    has_contact = bool((lead.get("owner_name") or lead.get("name")) and (phone or lead.get("email")))

    if score in ("HOT", "WARM") and has_contact:
        lead_with_id = {**lead, "id": lead_id}
        result = await push_directly_to_lofty(lead_with_id)
        if result:
            db.mark_property_lofty_pushed(lead_id)

        if score == "HOT" and phone:
            await send_property_sms(lead_with_id, db=db, language="spanish")

        if has_contact:
            asyncio.create_task(_send_n8n(lead_with_id))


async def _save_and_process_property_leads(leads: list, source_tag: str) -> int:
    """Save property leads to DB and trigger Lofty/SMS/N8N for qualifying ones."""
    saved = 0
    for lead in leads:
        try:
            lead_id = db.add_property_lead(lead)
            if lead_id:
                saved += 1
                await _process_property_lead(lead, lead_id)
        except Exception as e:
            print(f"[{source_tag}] Save error: {e}")
    return saved


# ── Facebook Ads Library job ───────────────────────────────────────────────

async def run_fb_ads_job():
    """Every 6 hours — competitor intel + buyer lead harvesting from FB Ad Library."""
    print(f"\n[{datetime.now()}] Running Facebook Ads Library scraper...")
    s = FacebookAdsLibraryScraper()
    try:
        result = await s.scrape_all()
        ads   = result.get("ads", [])
        leads = result.get("leads", [])

        # Save competitor ads
        saved_ads = 0
        for ad in ads:
            if db.save_fb_ad(ad):
                saved_ads += 1

        # Save buyer leads + queue Instagram handles for auto-DM
        saved_leads = 0
        for lead in leads:
            lead_db_id = db.save_fb_ad_lead(lead)
            if not lead_db_id:
                continue
            saved_leads += 1
            ig = lead.get("instagram_handle")
            if ig:
                # Add to phantom DM queue using the existing leads table
                new_id = db.add_lead(
                    name=ig,
                    source="facebook_ads_comment",
                    raw_data={"comment": lead.get("comment_text", ""), "ad_id": lead.get("ad_id")},
                )
                if new_id:
                    db.add_to_dm_queue(
                        lead_id=new_id,
                        ig_username=ig,
                        source="facebook_ads_comment",
                        score="HOT",
                    )
                    db.mark_fb_lead_dm_queued(lead_db_id, new_id)
                    print(f"[FBAds] Queued IG DM: @{ig}")
            else:
                # Save as WARM Facebook lead for manual follow-up
                db.add_lead(
                    name=lead.get("profile_name", "unknown"),
                    property_url=lead.get("profile_url"),
                    source="facebook_ads_comment",
                    raw_data={"comment": lead.get("comment_text", ""), "ad_id": lead.get("ad_id")},
                )

        print(f"[FBAds] Saved {saved_ads} ads | {saved_leads} buyer leads")
    except Exception as e:
        print(f"[FBAds] Job error: {e}")
    finally:
        await s.close()


# ── Auto-DM queue processor job ────────────────────────────────────────────

async def dm_queue_processor_job():
    global auto_dm_enabled
    result = await process_dm_queue(auto_dm_enabled=auto_dm_enabled)
    if result["status"] not in ("no_due_items", "outside_hours", "disabled"):
        print(f"[AutoDM] Queue processor: {result}")


# ── Property listing scraper jobs ─────────────────────────────────────────

async def run_listing_scrapers():
    """Daily 7am — Zillow, Realtor.com, Redfin."""
    print(f"\n[{datetime.now()}] Running listing scrapers (Zillow/Realtor/Redfin)...")
    for ScraperClass, tag in [
        (ZillowScraper, "Zillow"),
        (RealtorScraper, "Realtor"),
        (RedfinScraper, "Redfin"),
    ]:
        s = ScraperClass()
        try:
            leads = await s.scrape_all()
            saved = await _save_and_process_property_leads(leads, tag)
            print(f"[{tag}] Saved {saved}/{len(leads)} new property leads")
        except Exception as e:
            print(f"[{tag}] Job error: {e}")
        finally:
            await s.close()


async def run_preforeclosure_job():
    """Monday 6am — county clerk Lis Pendens."""
    print(f"\n[{datetime.now()}] Running pre-foreclosure scraper...")
    s = PreForeclosureScraper()
    try:
        leads = await s.scrape_all()
        saved = await _save_and_process_property_leads(leads, "PreForeclosure")
        print(f"[PreForeclosure] Saved {saved}/{len(leads)} leads")
    except Exception as e:
        print(f"[PreForeclosure] Job error: {e}")
    finally:
        await s.close()


async def run_public_records_job():
    """Monday 6am — deed records for cash buyers and LLC purchases."""
    print(f"\n[{datetime.now()}] Running public records scraper...")
    s = PublicRecordsScraper()
    try:
        leads = await s.scrape_all()
        saved = await _save_and_process_property_leads(leads, "PublicRecords")
        print(f"[PublicRecords] Saved {saved}/{len(leads)} leads")
    except Exception as e:
        print(f"[PublicRecords] Job error: {e}")
    finally:
        await s.close()


async def run_sunbiz_job():
    """Monday 6am — new LLC registrations."""
    print(f"\n[{datetime.now()}] Running Sunbiz LLC scraper...")
    s = SunbizScraper()
    try:
        leads = await s.scrape_all()
        saved = await _save_and_process_property_leads(leads, "Sunbiz")
        print(f"[Sunbiz] Saved {saved}/{len(leads)} leads")
    except Exception as e:
        print(f"[Sunbiz] Job error: {e}")
    finally:
        await s.close()


async def run_new_construction_job():
    """Daily 8am — builder community listings."""
    print(f"\n[{datetime.now()}] Running new construction scraper...")
    s = NewConstructionScraper()
    try:
        leads = await s.scrape_all()
        saved = await _save_and_process_property_leads(leads, "NewConstruction")
        print(f"[NewConstruction] Saved {saved}/{len(leads)} leads")
    except Exception as e:
        print(f"[NewConstruction] Job error: {e}")
    finally:
        await s.close()


# ── Scheduler ──────────────────────────────────────────────────────────────

def schedule_jobs():
    # Tier 1: every 6 hours + immediate startup run
    scheduler.add_job(scrape_tier1_job, IntervalTrigger(hours=6),
                      id="tier1_interval", name="Tier 1 — High Intent (6h)")
    scheduler.add_job(scrape_tier1_job,
                      id="tier1_init", name="Tier 1 — Initial Run")

    # Facebook Ads Library: every 6 hours (offset 30min from Tier1)
    scheduler.add_job(run_fb_ads_job, IntervalTrigger(hours=6, start_date=datetime.now().replace(minute=30)),
                      id="fb_ads", name="Facebook Ads Library (6h)")

    # Tier 2: every 12 hours
    scheduler.add_job(scrape_tier2_job, IntervalTrigger(hours=12),
                      id="tier2_interval", name="Tier 2 — City/Pre-Con (12h)")

    # Tier 3: daily at 6 am
    scheduler.add_job(scrape_tier3_job, CronTrigger(hour=6, minute=0),
                      id="tier3_daily", name="Tier 3 — Broad Market (6am)")

    # Auto-DM queue: check every 5 minutes
    scheduler.add_job(dm_queue_processor_job, IntervalTrigger(minutes=5),
                      id="dm_queue", name="Auto-DM Queue Processor (5min)")

    # Listing scrapers: daily at 7am
    scheduler.add_job(run_listing_scrapers, CronTrigger(hour=7, minute=0),
                      id="listing_scrapers", name="Listing Scrapers — Zillow/Realtor/Redfin (7am)")

    # New construction: daily at 8am
    scheduler.add_job(run_new_construction_job, CronTrigger(hour=8, minute=0),
                      id="new_construction", name="New Construction Scraper (8am)")

    # Pre-foreclosure + public records + Sunbiz: Monday at 6am
    scheduler.add_job(run_preforeclosure_job, CronTrigger(day_of_week="mon", hour=6, minute=0),
                      id="preforeclosure", name="Pre-Foreclosure Scraper (Mon 6am)")
    scheduler.add_job(run_public_records_job, CronTrigger(day_of_week="mon", hour=6, minute=15),
                      id="public_records", name="Public Records Scraper (Mon 6:15am)")
    scheduler.add_job(run_sunbiz_job, CronTrigger(day_of_week="mon", hour=6, minute=30),
                      id="sunbiz", name="Sunbiz LLC Scraper (Mon 6:30am)")

    scheduler.start()


@app.on_event("startup")
async def startup_event():
    from database import USE_POSTGRES, DATABASE_URL
    backend = "PostgreSQL ✓" if USE_POSTGRES else "SQLite ⚠️  (DATA WILL BE LOST ON REDEPLOY — set DATABASE_URL)"
    print("=" * 60)
    print("RELIX Lead Generation System starting...")
    print(f"  Database backend : {backend}")
    if USE_POSTGRES:
        # Mask credentials but show host for confirmation
        safe_url = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else "connected"
        print(f"  Postgres host    : {safe_url}")
    print("=" * 60)
    schedule_jobs()
    print("[+] Scheduler: Tier1=6h | Tier2=12h | Tier3=daily@6am")
    print("[+] Property: Listings=daily@7am | NewCon=daily@8am | PreForeclosure/Records/Sunbiz=Mon@6am")


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
    queue_stats = db.get_dm_queue_stats()
    return {
        "stats": db.get_stats(),
        "leads": db.get_leads_with_scores(limit=200),
        "auto_dm": {
            "enabled": auto_dm_enabled,
            "pending_in_queue": queue_stats["pending"],
            "next_scheduled": queue_stats["next_scheduled"],
        },
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
    queue_stats = db.get_dm_queue_stats()
    return {
        "stats": db.get_dm_stats(),
        "leads": db.get_leads_with_dm_status(limit=500),
        "dms_today": _count_dms_today(),
        "daily_limit": 15,
        "auto_dm": {
            "enabled": auto_dm_enabled,
            "pending_in_queue": queue_stats["pending"],
            "next_scheduled": queue_stats["next_scheduled"],
        },
    }


@app.post("/api/auto-dm/toggle")
async def toggle_auto_dm():
    global auto_dm_enabled
    auto_dm_enabled = not auto_dm_enabled
    queue_stats = db.get_dm_queue_stats()
    print(f"[AutoDM] {'ENABLED' if auto_dm_enabled else 'DISABLED'} by user")
    return {
        "auto_dm_enabled": auto_dm_enabled,
        "pending_in_queue": queue_stats["pending"],
        "next_scheduled": queue_stats["next_scheduled"],
    }


@app.get("/api/auto-dm/status")
async def auto_dm_status():
    queue_stats = db.get_dm_queue_stats()
    return {
        "enabled": auto_dm_enabled,
        "pending_in_queue": queue_stats["pending"],
        "next_scheduled": queue_stats["next_scheduled"],
        "dms_today": _count_dms_today(),
        "daily_limit": 15,
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


@app.get("/api/db-info")
async def db_info():
    from database import USE_POSTGRES, DATABASE_URL, DB_PATH
    stats = db.get_stats()
    info = {
        "backend": "postgresql" if USE_POSTGRES else "sqlite",
        "persistent": USE_POSTGRES,
        "warning": None if USE_POSTGRES else "SQLite is in use — all data is lost on redeploy. Add DATABASE_URL in Railway.",
        "stats": stats,
    }
    if USE_POSTGRES:
        safe_url = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else "connected"
        info["postgres_host"] = safe_url
    else:
        info["sqlite_path"] = DB_PATH
    return info


@app.get("/api/reddit-leads")
async def get_reddit_leads(limit: int = 100):
    leads = db.get_hot_reddit_leads(limit=limit)
    return {"count": len(leads), "leads": leads}


@app.post("/api/reddit-leads/{lead_id}/generate-reply")
async def generate_reddit_reply(lead_id: int):
    # Return cached reply instantly if already generated
    cached = db.get_reddit_reply(lead_id)
    if cached and cached.get("reply_text"):
        return {"reply": cached["reply_text"], "cached": True}

    lead = db.get_lead(lead_id)
    if not lead:
        return {"error": "Lead not found"}

    raw = lead.get("raw_data")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    lead["raw_data"] = raw or {}

    reply = await asyncio.to_thread(qualifier.generate_reddit_reply, lead)
    db.save_reddit_reply(lead_id, reply)
    return {"reply": reply, "cached": False}


@app.post("/api/reddit-leads/{lead_id}/mark-replied")
async def mark_reddit_replied(lead_id: int, body: dict):
    replied = bool(body.get("replied", True))
    db.mark_reddit_replied(lead_id, replied)
    return {"lead_id": lead_id, "marked_replied": replied}


# ── Property Leads endpoints ───────────────────────────────────────────────

@app.get("/api/property-leads")
async def get_property_leads(score: str = None, lead_type: str = None, limit: int = 200):
    leads = db.get_property_leads(score=score, lead_type=lead_type, limit=limit)
    stats = db.get_property_lead_stats()
    return {"count": len(leads), "stats": stats, "leads": leads}


@app.post("/api/property-leads/{lead_id}/push-to-lofty")
async def push_property_to_lofty(lead_id: int):
    lead = db.get_property_lead(lead_id)
    if not lead:
        return {"error": "Property lead not found"}
    result = await push_directly_to_lofty(lead)
    if result:
        db.mark_property_lofty_pushed(lead_id)
        return {"status": "pushed", "lead_id": lead_id}
    return {"status": "skipped", "reason": "Missing contact info or API key not configured"}


@app.post("/api/property-leads/{lead_id}/send-sms")
async def send_property_lead_sms(lead_id: int, body: dict = None):
    lead = db.get_property_lead(lead_id)
    if not lead:
        return {"error": "Property lead not found"}
    language = (body or {}).get("language", "spanish")
    sent = await send_property_sms(lead, db=db, language=language)
    if sent:
        return {"status": "sent", "lead_id": lead_id}
    return {"status": "skipped", "reason": "No phone, outside hours, cooldown active, or limit reached"}


@app.post("/scan/listings")
async def scan_listings(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_listing_scrapers)
    return {"status": "Listing scraper triggered", "timestamp": datetime.now().isoformat()}


@app.post("/scan/preforeclosure")
async def scan_preforeclosure(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_preforeclosure_job)
    return {"status": "Pre-foreclosure scraper triggered", "timestamp": datetime.now().isoformat()}


@app.post("/scan/fb-ads")
async def scan_fb_ads(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_fb_ads_job)
    return {"status": "Facebook Ads Library scan triggered", "timestamp": datetime.now().isoformat()}


@app.get("/api/fb-ads")
async def get_fb_ads(limit: int = 200):
    ads   = db.get_fb_ads(limit=limit)
    leads = db.get_fb_ad_leads(limit=100)
    stats = db.get_fb_stats()
    top10 = top_advertisers(ads, n=10)
    return {
        "stats":            stats,
        "top_advertisers":  top10,
        "ads":              ads,
        "buyer_leads":      leads,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")

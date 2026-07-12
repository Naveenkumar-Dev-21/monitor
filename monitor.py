import os
import datetime
import re
import sys
import difflib
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Load local environment variables from .env if present
load_dotenv()

def get_current_time_str():
    """Gets current timestamp formatted in Indian Standard Time (IST)."""
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    # Remove timezone info so we can add timedelta safely if utc_now is timezone-aware
    utc_now = utc_now.replace(tzinfo=None)
    ist_now = utc_now + datetime.timedelta(hours=5, minutes=30)
    return ist_now.strftime("%Y-%m-%d %I:%M:%S %p (IST)")

# Configuration from Environment Variables
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Target URLs
PUBLIC_TARGET_URL = os.environ.get("PUBLIC_TARGET_URL", "https://results.kongu.edu")
PORTAL_LOGIN_URL = os.environ.get("PORTAL_LOGIN_URL")
PORTAL_TARGET_URL = os.environ.get("PORTAL_TARGET_URL")

# Credentials & Field Mappings
PORTAL_USERNAME = os.environ.get("PORTAL_USERNAME")
PORTAL_PASSWORD = os.environ.get("PORTAL_PASSWORD")
PORTAL_USERNAME_FIELD = os.environ.get("PORTAL_USERNAME_FIELD", "email")
PORTAL_PASSWORD_FIELD = os.environ.get("PORTAL_PASSWORD_FIELD", "password")
PORTAL_PAYLOAD_FORMAT = os.environ.get("PORTAL_PAYLOAD_FORMAT", "form")  # 'form' or 'json'

# Noise filter patterns (case-insensitive regexes) to avoid alerts on visitor counters, times, etc.
NOISE_PATTERNS = [
    r'^\d+$',                                     # Pure numbers
    r'visitor(s)?\s*:\s*\d+',                     # Visitor counters
    r'hit(s)?\s*:\s*\d+',                         # Hits counter
    r'page\s*loaded\s*in',                        # Performance metrics
    r'copyright\s*©',                             # Copyright updates
    r'last\s*updated',                            # Update timestamps
    r'\d{1,2}[:.]\d{2}\s*(?:am|pm)?',            # Times (e.g. 10:30 AM, 14.25)
    r'\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b',  # Days of week
]

def clean_text_content(html_content):
    """
    Cleans HTML by removing scripting, styling, and navigation noise,
    returning structured clean lines of text.
    """
    if not html_content:
        return ""
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Remove structural/interactive noise
    for element in soup(["script", "style", "meta", "link", "noscript", "header", "footer", "nav", "input", "button"]):
        element.decompose()
        
    text_lines = []
    for string in soup.stripped_strings:
        cleaned = " ".join(string.split())
        if not cleaned:
            continue
            
        # Apply noise filter
        should_ignore = False
        for pattern in NOISE_PATTERNS:
            if re.search(pattern, cleaned, re.IGNORECASE):
                should_ignore = True
                break
                
        if not should_ignore:
            text_lines.append(cleaned)
            
    return "\n".join(text_lines)

def send_telegram_notification(message):
    """Sends a formatted Markdown message to Telegram."""
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram BOT_TOKEN or CHAT_ID not set. Skipping notification.")
        print(f"Message that would have been sent:\n{message}")
        return
        
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        print("Telegram notification sent successfully.")
    except Exception as e:
        print(f"Error sending Telegram notification: {e}")

def get_diff_summary(old_text, new_text):
    """Computes a structured list of additions and removals."""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    
    diff = list(difflib.ndiff(old_lines, new_lines))
    
    added = []
    removed = []
    
    for line in diff:
        if line.startswith('+ '):
            val = line[2:].strip()
            if val:
                added.append(val)
        elif line.startswith('- '):
            val = line[2:].strip()
            if val:
                removed.append(val)
                
    return added, removed

def monitor_public_site():
    """Monitors the public website for updates."""
    print(f"Checking public website: {PUBLIC_TARGET_URL}")
    state_file = "state_public.txt"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        r = requests.get(PUBLIC_TARGET_URL, headers=headers, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"Error requesting public target URL: {e}")
        return
        
    current_text = clean_text_content(r.content)
    if not current_text:
        print("Warning: Cleaned public content is empty. Skipping comparison.")
        return
        
    if not os.path.exists(state_file):
        # Initial run: Save state and notify user that monitoring has started
        with open(state_file, "w", encoding="utf-8") as f:
            f.write(current_text)
        print("Initial run. State file created for public site.")
        send_telegram_notification(f"🟢 <b>Started Monitoring</b>\nURL: {PUBLIC_TARGET_URL}\nBaseline state has been saved.\n\n🕒 <b>Time:</b> {get_current_time_str()}")
        return

    with open(state_file, "r", encoding="utf-8") as f:
        previous_text = f.read()
        
    added, removed = get_diff_summary(previous_text, current_text)
    
    if added or removed:
        print(f"Changes detected on {PUBLIC_TARGET_URL}!")
        
        # Build message
        msg_parts = [f"🔔 <b>Change Detected</b> on {PUBLIC_TARGET_URL}"]
        
        if added:
            msg_parts.append("\n➕ <b>Added:</b>")
            for item in added[:15]:  # Limit output length to prevent telegram limit overflow
                msg_parts.append(f"• {item}")
            if len(added) > 15:
                msg_parts.append(f"• <i>...and {len(added)-15} more additions</i>")
                
        if removed:
            msg_parts.append("\n➖ <b>Removed:</b>")
            for item in removed[:10]:
                msg_parts.append(f"• <s>{item}</s>")
            if len(removed) > 10:
                msg_parts.append(f"• <i>...and {len(removed)-10} more removals</i>")
                
        msg_parts.append(f"\n🕒 <b>Detected At:</b> {get_current_time_str()}")
        # Send Notification
        send_telegram_notification("\n".join(msg_parts))
        
        # Update State
        with open(state_file, "w", encoding="utf-8") as f:
            f.write(current_text)
    else:
        print(f"No changes detected on {PUBLIC_TARGET_URL}.")

def monitor_portal_site():
    """Logs into the portal site, fetches the authenticated page, and monitors it."""
    if not PORTAL_LOGIN_URL or not PORTAL_TARGET_URL or not PORTAL_USERNAME or not PORTAL_PASSWORD:
        print("Portal configuration or credentials not fully set. Skipping portal monitoring.")
        return
        
    print(f"Checking portal login: {PORTAL_LOGIN_URL}")
    state_file = "state_portal.txt"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    session = requests.Session()
    session.headers.update(headers)
    
    try:
        # Step 1: GET login page to collect cookies and parse CSRF tokens if present
        login_page_resp = session.get(PORTAL_LOGIN_URL, timeout=20)
        login_page_resp.raise_for_status()
        
        # Parse for CSRF tokens or other hidden fields
        soup = BeautifulSoup(login_page_resp.content, "html.parser")
        payload = {}
        
        # Look for CSRF tokens
        csrf_selectors = [
            'input[name*="csrf"]',
            'input[name*="token"]',
            'input[name*="_csrf"]',
            'input[name*="middlewaretoken"]'
        ]
        
        for selector in csrf_selectors:
            for field in soup.select(selector):
                if field.get("name") and field.get("value"):
                    payload[field["name"]] = field["value"]
                    
        # Add credentials
        payload[PORTAL_USERNAME_FIELD] = PORTAL_USERNAME
        payload[PORTAL_PASSWORD_FIELD] = PORTAL_PASSWORD
        
        print(f"Sending login POST request to {PORTAL_LOGIN_URL}")
        # Step 2: POST credentials to authenticate
        if PORTAL_PAYLOAD_FORMAT.lower() == "json":
            login_resp = session.post(PORTAL_LOGIN_URL, json=payload, timeout=20)
        else:
            login_resp = session.post(PORTAL_LOGIN_URL, data=payload, timeout=20)
            
        login_resp.raise_for_status()
        
        # Step 3: Fetch target authenticated page
        print(f"Fetching authenticated target page: {PORTAL_TARGET_URL}")
        target_resp = session.get(PORTAL_TARGET_URL, timeout=20)
        target_resp.raise_for_status()
        
    except Exception as e:
        print(f"Error executing portal login or fetch: {e}")
        return
        
    current_text = clean_text_content(target_resp.content)
    if not current_text:
        print("Warning: Cleaned portal content is empty. Skipping comparison.")
        return
        
    if not os.path.exists(state_file):
        # Initial run: Save state and notify user
        with open(state_file, "w", encoding="utf-8") as f:
            f.write(current_text)
        print("Initial run. State file created for portal site.")
        send_telegram_notification(f"🟢 <b>Started Monitoring</b>\nURL: {PORTAL_TARGET_URL} (Authenticated)\nBaseline state has been saved.\n\n🕒 <b>Time:</b> {get_current_time_str()}")
        return

    with open(state_file, "r", encoding="utf-8") as f:
        previous_text = f.read()
        
    added, removed = get_diff_summary(previous_text, current_text)
    
    if added or removed:
        print(f"Changes detected on {PORTAL_TARGET_URL}!")
        
        # Build message
        msg_parts = [f"🔔 <b>Change Detected</b> on portal target:\n<code>{PORTAL_TARGET_URL}</code>"]
        
        if added:
            msg_parts.append("\n➕ <b>Added:</b>")
            for item in added[:15]:
                msg_parts.append(f"• {item}")
            if len(added) > 15:
                msg_parts.append(f"• <i>...and {len(added)-15} more additions</i>")
                
        if removed:
            msg_parts.append("\n➖ <b>Removed:</b>")
            for item in removed[:10]:
                msg_parts.append(f"• <s>{item}</s>")
            if len(removed) > 10:
                msg_parts.append(f"• <i>...and {len(removed)-10} more removals</i>")
                
        msg_parts.append(f"\n🕒 <b>Detected At:</b> {get_current_time_str()}")
        # Send Notification
        send_telegram_notification("\n".join(msg_parts))
        
        # Update State
        with open(state_file, "w", encoding="utf-8") as f:
            f.write(current_text)
    else:
        print(f"No changes detected on authenticated target: {PORTAL_TARGET_URL}.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Website monitor bot")
    parser.add_argument("--public", action="store_true", help="Monitor public site")
    parser.add_argument("--portal", action="store_true", help="Monitor authenticated portal")
    args = parser.parse_args()
    
    # If no flags are set, run both by default
    run_all = not args.public and not args.portal
    
    print("Running website monitor checking routines...")
    if run_all or args.public:
        monitor_public_site()
    if run_all or args.portal:
        monitor_portal_site()
    print("Routine check complete.")

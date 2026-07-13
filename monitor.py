import os
import datetime
import re
import sys
import difflib
import requests
import json
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
PUBLIC_TARGET_URL = os.environ.get("PUBLIC_TARGET_URL", "https://ssm.cdac.in/api/score-update-status")

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

def fetch_url_with_proxy(url):
    """Attempts to fetch the URL directly, and falls back to free Indian proxies if geoblocked/timed out."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    # Try direct connection first
    try:
        print(f"Attempting direct connection to {url}")
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        return r
    except Exception as direct_err:
        print(f"Direct connection failed (possibly geoblocked): {direct_err}")
        
    # If direct connection fails, try using free Indian proxies
    print("Attempting to bypass using free Indian proxies...")
    proxy_api_url = "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&protocol=http&proxy_format=ipport&format=text&country=in"
    try:
        proxy_resp = requests.get(proxy_api_url, timeout=10)
        if proxy_resp.status_code == 200:
            proxies_list = [line.strip() for line in proxy_resp.text.splitlines() if line.strip()]
            print(f"Found {len(proxies_list)} potential Indian proxies.")
            
            # Try top 10 proxies from the list
            for proxy in proxies_list[:10]:
                proxy_dict = {
                    "http": f"http://{proxy}",
                    "https": f"http://{proxy}"
                }
                print(f"Trying Indian proxy: {proxy}")
                try:
                    r = requests.get(url, headers=headers, proxies=proxy_dict, timeout=7)
                    r.raise_for_status()
                    print(f"Successfully connected via proxy: {proxy}")
                    return r
                except Exception as proxy_err:
                    print(f"Proxy {proxy} failed: {proxy_err}")
        else:
            print(f"Failed to fetch proxy list. HTTP Status: {proxy_resp.status_code}")
    except Exception as e:
        print(f"Error fetching proxy list: {e}")
        
    # If everything fails, raise the original direct connection error
    raise direct_err

def monitor_public_site():
    """Monitors the public website for updates."""
    print(f"Checking public website: {PUBLIC_TARGET_URL}")
    state_file = "state_public.txt"
    
    try:
        r = fetch_url_with_proxy(PUBLIC_TARGET_URL)
    except Exception as e:
        print(f"Error requesting public target URL: {e}")
        return
        
    try:
        json_data = r.json()
        current_text = json.dumps(json_data, indent=2, sort_keys=True)
    except Exception:
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

if __name__ == "__main__":
    print("Running website monitor checking routine...")
    monitor_public_site()
    print("Routine check complete.")

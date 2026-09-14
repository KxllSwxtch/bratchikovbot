"""
Che168 Global client.

Fetches car listings from global.che168.com (Autohome's export marketplace)
through its public JSON API. The HTML pages sit behind a Tencent EdgeOne
captcha, but globalapi.che168.com answers plain requests without cookies
or request signing.
"""

import logging
import os
import re
import uuid

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

GLOBAL_SITE = "https://global.che168.com"
GLOBAL_API_BASE = "https://globalapi.che168.com/api/v1"
APPID = "global.pc"
DEVICE_ID = str(uuid.uuid4())

# (connect, read). Kept short: TeleBot runs only 2 worker threads, so a stalled
# request freezes the bot for everyone.
REQUEST_TIMEOUT = (5, 10)

# The site shows the domestic CNY price converted to USD at its own fixed rate
# and rounded to $10. On 9 cars checked against m.che168.com, 6.575 turned every
# USD price back into the exact yuan price. To re-derive: divide the ¥ price
# from m.che168.com by the $ price on global.che168.com for 2-3 cars.
DEFAULT_CNY_PER_USD = float(os.getenv("CHE168_CNY_PER_USD", "6.575"))
KW_TO_HP = 1.35962

# specparam item ids (the same in every language)
SPEC_DISPLACEMENT_ML = 39   # Displacement (mL)
SPEC_DISPLACEMENT_L = 40    # Displacement (L)
SPEC_ENGINE_PS = 49         # Maximum horsepower (Ps)
SPEC_ENGINE_KW = 50         # Maximum power (kW)
SPEC_MOTOR_KW = 63          # Total Motor Power (kW)
SPEC_COMBINED_KW = 70       # System Combined Power (kW)
SPEC_MOTOR_PS_BASIC = 114   # Electric Motor (Ps), "Basic Specifications" group
SPEC_MOTOR_PS = 120         # Total Electric Motor Horsepower (Ps)
SPEC_COMBINED_PS = 121      # System Combined Power (Ps)

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en,ru;q=0.9",
    "Origin": GLOBAL_SITE,
    "Referer": f"{GLOBAL_SITE}/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
}

# Body fragments of anti-bot pages (EdgeOne captcha, JS challenge)
BLOCK_MARKERS = ("TEOCaptcha", "Security Verification", "solveChallenge")

GLOBAL_DETAIL_RE = re.compile(
    r"https?://global\.che168\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?detail/(\d{6,12})",
    re.IGNORECASE,
)
LEGACY_URL_RE = re.compile(r"https?://(?:www\.|m\.|i\.)?che168\.com/\S+", re.IGNORECASE)

_MISSING = {"", "-", "--"}


class Che168Error(Exception):
    """The car could not be loaded from che168."""


class Che168NotFound(Che168Error):
    """The listing does not exist on global.che168.com (sold or removed)."""


class Che168Blocked(Che168Error):
    """The API answered with an anti-bot page instead of JSON."""


class Che168Unavailable(Che168Error):
    """Network or HTTP failure, or an unexpected API response."""


class Che168DataError(Che168Error):
    """The listing lacks a value the calculation needs."""


# ==================== HTTP ====================

def _get_proxy_config():
    """Return proxy config from CHE168_PROXY_URL env var, or None for direct connection."""
    url = os.getenv("CHE168_PROXY_URL", "").strip()
    if url:
        return {"http": url, "https": url}
    return None


def _create_session():
    """Create a requests session with retry logic and optional proxy."""
    session = requests.Session()
    retry = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(HEADERS)
    proxy = _get_proxy_config()
    if proxy:
        session.proxies.update(proxy)
    return session


def _api_get(session, path, params=None):
    """
    GET a globalapi.che168.com endpoint and return its `result`.

    Raises:
        Che168NotFound: returncode 101 (invalid listing id)
        Che168Blocked: anti-bot page instead of JSON
        Che168Unavailable: network/HTTP error or unexpected returncode
    """
    query = {
        "_appid": APPID,
        "deviceid": DEVICE_ID,
        "language": "en",
        "fromsource": "0",
        **(params or {}),
    }

    try:
        response = session.get(f"{GLOBAL_API_BASE}/{path}", params=query, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise Che168Unavailable(f"{path}: {e}") from e

    content_type = response.headers.get("Content-Type", "")
    if "json" not in content_type:
        body = response.text
        logging.warning(
            "che168 %s: non-JSON response (HTTP %s, server=%s): %r",
            path, response.status_code, response.headers.get("Server"), body[:100],
        )
        if response.status_code == 200 or any(marker in body for marker in BLOCK_MARKERS):
            raise Che168Blocked(f"{path}: anti-bot page instead of JSON")
        raise Che168Unavailable(f"{path}: HTTP {response.status_code}")

    if response.status_code != 200:
        raise Che168Unavailable(f"{path}: HTTP {response.status_code}")

    try:
        data = response.json()
    except ValueError as e:
        raise Che168Unavailable(f"{path}: invalid JSON") from e

    returncode = data.get("returncode")
    if returncode == 101:
        raise Che168NotFound(f"{path}: {data.get('message')}")
    if returncode != 0:
        raise Che168Unavailable(f"{path}: returncode={returncode} {data.get('message')}")
    return data.get("result")


# ==================== URLs ====================

def extract_global_car_id(text):
    """Car id from a global.che168.com detail link anywhere in text, or None."""
    match = GLOBAL_DETAIL_RE.search(text or "")
    return match.group(1) if match else None


def is_che168_global_url(text):
    """True if text contains a global.che168.com detail link."""
    return extract_global_car_id(text) is not None


def extract_legacy_car_id(text):
    """
    Car id from an old www./m./i.che168.com link, or None.

    Only used to point the user to the same car on global.che168.com.
    """
    match = LEGACY_URL_RE.search(text or "")
    if not match:
        return None
    url = match.group(0)
    found = re.search(r"[?&]infoid=(\d{6,12})", url) or re.search(r"/(\d{7,12})(?:\.html)?/?(?:[?#]|$)", url)
    return found.group(1) if found else None


def build_global_link(car_id):
    """Canonical Russian-locale link to a listing."""
    return f"{GLOBAL_SITE}/ru/detail/{car_id}"


# ==================== Parsing ====================

def _num(value):
    """Leading number of an API string ("150/218" -> 150.0, "--" -> None)."""
    match = re.match(r"\s*(\d+(?:\.\d+)?)", str(value if value is not None else ""))
    return float(match.group(1)) if match else None


def _spec_values(spec_result):
    """
    Flatten a specparam result into {item_id: value}.

    The same id can appear in several groups; the first non-empty value wins.
    A "--" value with a sublist (e.g. drive type) takes the first subvalue.
    """
    values = {}
    for group in (spec_result or {}).get("paramtypeitems") or []:
        for item in group.get("paramitems") or []:
            item_id = item.get("id")
            if item_id in values:
                continue
            value = str(item.get("value") or "").strip()
            if value in _MISSING:
                sublist = item.get("sublist") or []
                value = str(sublist[0].get("subvalue") or "").strip() if sublist else ""
            if value not in _MISSING:
                values[item_id] = value
    return values


def _spec_number(spec, item_id):
    """Numeric spec value, or None."""
    return _num(spec.get(item_id))


def _kw_to_hp(kw):
    """Convert kW to metric horsepower (л.с.), or None."""
    return round(kw * KW_TO_HP) if kw else None


def _parse_year_month(value):
    """(year, month) from "2022.01" or "2022-01-01 00:00:00", or None."""
    match = re.match(r"\s*(\d{4})[.\-/](\d{1,2})", str(value or ""))
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if year < 1950 or not 1 <= month <= 12:
        return None
    return year, month


def _clean_car_name(name):
    """Strip and drop a repeated leading brand ("Benz Benz C-Class" -> "Benz C-Class")."""
    name = re.sub(r"\s+", " ", name or "").strip()
    return re.sub(r"^(.+?) \1 ", r"\1 ", name)


def map_energy_type(fuel_name):
    """
    Map an English fuelname to (calcus engine code, Russian name).

    calcus.ru codes: 1 petrol, 2 diesel, 4 electric, 5 series hybrid, 6 parallel hybrid.
    Returns (None, original name) for unknown values such as "--".
    """
    name = (fuel_name or "").lower()
    if re.search(r"range|extend", name):
        return 5, "Гибрид (рейндж-экстендер)"
    if re.search(r"mild|48v|light hybrid", name):
        if "diesel" in name:
            return 2, "Дизель (мягкий гибрид)"
        return 1, "Бензин (мягкий гибрид)"
    if "plug-in" in name:
        return 6, "Гибрид (подзарядка)"
    if "hybrid" in name:
        return 6, "Гибрид"
    if "electric" in name:
        return 4, "Электро"
    if "diesel" in name:
        return 2, "Дизель"
    if re.search(r"gasoline|petrol", name):
        return 1, "Бензин"
    return None, (fuel_name or "").strip() or "Неизвестно"


def select_power_hp(fuel_code, spec, engine):
    """
    Pick the horsepower (л.с.) sent to calcus.ru for this fuel type.

    Args:
        fuel_code: calcus engine code from map_energy_type (1, 2, 4, 5, 6)
        spec: {item_id: value} from _spec_values
        engine: carinfo "engine" string, e.g. "1.5T 170hp L4"

    Returns:
        int horsepower, or None if it can't be determined (the bot then asks the user)
    """
    if fuel_code in (4, 5):
        # Electric and range extender: the wheels are driven by the motor only
        candidates = [
            _spec_number(spec, SPEC_MOTOR_PS),
            _spec_number(spec, SPEC_MOTOR_PS_BASIC),
            _kw_to_hp(_spec_number(spec, SPEC_MOTOR_KW)),
        ]
    elif fuel_code == 6:
        # Parallel hybrid: combined system power; engine + motor peaks only as a last resort
        engine_ps = _spec_number(spec, SPEC_ENGINE_PS)
        motor_ps = _spec_number(spec, SPEC_MOTOR_PS)
        candidates = [
            _spec_number(spec, SPEC_COMBINED_PS),
            _kw_to_hp(_spec_number(spec, SPEC_COMBINED_KW)),
            engine_ps + motor_ps if engine_ps and motor_ps else None,
        ]
    else:
        # Petrol/diesel (including 48V mild hybrids): engine power
        hp_match = re.search(r"(\d+)\s*hp", engine or "", re.IGNORECASE)
        candidates = [
            _spec_number(spec, SPEC_ENGINE_PS),
            _kw_to_hp(_spec_number(spec, SPEC_ENGINE_KW)),
            float(hp_match.group(1)) if hp_match else None,
        ]
    return next((int(round(value)) for value in candidates if value), None)


def _displacement_cc(spec, engine):
    """Engine displacement in cc from specs, falling back to the engine string."""
    ml = _spec_number(spec, SPEC_DISPLACEMENT_ML)
    if ml:
        return int(ml)
    liters = _spec_number(spec, SPEC_DISPLACEMENT_L)
    if liters:
        return int(round(liters * 1000))
    match = re.match(r"\s*(\d+\.\d+)\s*[TL]?(?:\s|$)", engine or "")
    if match:
        return int(round(float(match.group(1)) * 1000))
    return None


def usd_to_cny(price_usd, rate):
    """
    Recover the domestic yuan price from the site's USD price.

    Domestic prices are whole hundreds of yuan and the site rounds USD to $10,
    so the product lands within ~33 yuan of a round hundred. A larger gap means
    the site's rate has changed and CHE168_CNY_PER_USD needs updating.
    """
    raw = price_usd * rate
    price_cny = int(round(raw / 100) * 100)
    if abs(raw - price_cny) > 35:
        logging.warning(
            "che168: $%s x %s = %.0f CNY is not near a round hundred; "
            "the site CNY/USD rate may have changed (CHE168_CNY_PER_USD)",
            price_usd, rate, raw,
        )
    return price_cny


def parse_global_car(carinfo, spec_result, rate):
    """
    Turn carinfo + specparam results into the dict used by the China calculation.

    Raises:
        Che168DataError: price, date or (for non-EVs) displacement is missing
    """
    price_usd = _num(carinfo.get("price"))
    if not price_usd:
        raise Che168DataError("цена")
    price_usd = int(price_usd)

    age, age_source = None, None
    for field, source in (
        ("manufacturedate", "manufacture"),
        ("regdate", "registration"),
        ("producedate", "manufacture"),
    ):
        age = _parse_year_month(carinfo.get(field))
        if age:
            age_source = source
            break
    if not age:
        raise Che168DataError("дата выпуска")

    fuel_name = (carinfo.get("fuelname") or "").strip()
    fuel_code, fuel_ru = map_energy_type(fuel_name)
    spec = _spec_values(spec_result)
    engine = carinfo.get("engine") or ""

    if fuel_code == 4:
        displacement_cc = 0
    else:
        displacement_cc = _displacement_cc(spec, engine)
        if displacement_cc is None and fuel_code is not None:
            raise Che168DataError("объём двигателя")

    horsepower = select_power_hp(fuel_code, spec, engine) if fuel_code else None

    photos = [
        url
        for group in carinfo.get("catepiclist") or []
        for url in group.get("list") or []
        if isinstance(url, str) and url.startswith("http")
    ][:10]

    infoid = carinfo.get("infoid")
    mileage = _num(carinfo.get("mileage"))

    return {
        "infoid": infoid,
        "car_name": _clean_car_name(carinfo.get("carname")),
        "price_usd": price_usd,
        "price_cny": usd_to_cny(price_usd, rate),
        "displacement_cc": displacement_cc,
        "age_year": age[0],
        "age_month": age[1],
        "age_source": age_source,
        "mileage_km": int(mileage) if mileage else 0,
        "fuel_name": fuel_name,
        "fuel_type_code": fuel_code,
        "fuel_type_ru": fuel_ru,
        "horsepower": horsepower,
        "gearbox": (carinfo.get("gearbox") or "").strip(),
        "city_name": (carinfo.get("cname") or "").strip().title(),
        "photos": photos,
        "link": build_global_link(infoid),
        "source": "che168_global",
    }


def get_global_car_info(car_id):
    """
    Fetch and parse a global.che168.com listing.

    A failed specs request is not fatal: horsepower stays None and the bot
    asks the user for it.

    Raises:
        Che168NotFound, Che168Blocked, Che168Unavailable, Che168DataError
    """
    session = _create_session()

    carinfo = _api_get(session, f"carinfo/{car_id}")
    if not carinfo:
        raise Che168NotFound(f"carinfo/{car_id}: empty result")

    spec_result = {}
    specid = carinfo.get("specid")
    if specid:
        try:
            spec_result = _api_get(session, "specparam", {"specid": specid}) or {}
        except Che168Error as e:
            logging.warning("che168 specparam for %s failed, continuing without specs: %s", car_id, e)

    return parse_global_car(carinfo, spec_result, DEFAULT_CNY_PER_USD)


# ==================== Formatting ====================

def format_mileage(mileage_km):
    """
    Format mileage for display.

    Args:
        mileage_km: Mileage in kilometers

    Returns:
        str: Formatted mileage string
    """
    if mileage_km >= 1000:
        thousands = f"{mileage_km / 1000:.1f}".rstrip("0").rstrip(".")
        return f"{thousands} тыс. км"
    return f"{mileage_km} км"


def format_gearbox(gearbox):
    """
    Translate an English gearbox description to Russian.

    Args:
        gearbox: e.g. "9-speed automatic transmission"

    Returns:
        str: Russian gearbox name, or the original text if unrecognised
    """
    text = (gearbox or "").lower()
    if "dual-clutch" in text or "dct" in text:
        return "Робот (DCT)"
    if "cvt" in text or "continuously variable" in text:
        return "Вариатор"
    if "single-speed" in text:
        return "Редуктор"
    if "manual" in text and "automatic" not in text:
        return "Механика"
    if "automatic" in text or "amt" in text:
        return "Автомат"
    return (gearbox or "").strip() or "—"


if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) != 2:
        print("Usage: python che168_scraper.py <global.che168.com detail link>")
        sys.exit(2)

    car_id = extract_global_car_id(sys.argv[1])
    if not car_id:
        print("Not a global.che168.com detail link")
        sys.exit(2)

    info = get_global_car_info(car_id)
    info["photos"] = f"{len(info['photos'])} photos"
    print(json.dumps(info, ensure_ascii=False, indent=2))

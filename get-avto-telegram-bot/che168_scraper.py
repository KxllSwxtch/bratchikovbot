"""
Che168.com Car Scraper Module

Fetches car listing data from Che168.com (Chinese used car marketplace)
using their mobile API endpoint.
"""

import re
import time
import requests
import logging
from datetime import datetime

# Che168 API endpoints
CHE168_API_URL = "https://apiuscdt.che168.com/apic/v2/car/getcarinfo"
CHE168_SPECS_API_URL = "https://apiuscdt.che168.com/api/v1/car/getparamtypeitems"

# Request headers for Che168 API
CHE168_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en,ru;q=0.9",
    "Origin": "https://m.che168.com",
    "Referer": "https://m.che168.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

# Fuel type mapping from Chinese to internal codes
FUEL_TYPE_MAPPING = {
    "汽油": 1,      # Gasoline
    "柴油": 2,      # Diesel
    "纯电动": 4,    # Electric
    "电动": 4,      # Electric
    "插电混动": 5,  # Plug-in hybrid (series)
    "油电混合": 6,  # Hybrid (parallel)
    "增程式": 5,    # Range extender (series hybrid)
}

# Fuel type display names in Russian
FUEL_TYPE_NAMES_RU = {
    "汽油": "Бензин",
    "柴油": "Дизель",
    "纯电动": "Электро",
    "电动": "Электро",
    "插电混动": "Гибрид (подзарядка)",
    "油电混合": "Гибрид",
    "增程式": "Гибрид (рейндж-экстендер)",
}

# Proxy configurations for Che168 (with fallback options)
CHE168_PROXY_OPTIONS = [
    # Oxylabs China proxy (primary)
    {"http": "http://customer-tiksanauto_M2zEp-cc-cn:Tiksan_auto99@pr.oxylabs.io:7777",
     "https": "http://customer-tiksanauto_M2zEp-cc-cn:Tiksan_auto99@pr.oxylabs.io:7777"},
    # Russian datacenter proxy (fallback)
    {"http": "http://B01vby:GBno0x@45.118.250.2:8000",
     "https": "http://B01vby:GBno0x@45.118.250.2:8000"},
    # No proxy (last resort)
    None,
]


def extract_car_id_from_che168_url(url):
    """
    Extract car ID (infoid) from Che168 URL.

    Supported URL formats:
    - https://m.che168.com/dealer/657408/56913158.html
    - https://www.che168.com/usedcar/56913158.html
    - https://m.che168.com/v/56913158.html

    Args:
        url: Che168 listing URL

    Returns:
        str: Car ID (infoid), or None if not found
    """
    # Pattern to match 8-digit car IDs in URL path
    match = re.search(r'/(\d{7,9})(?:\.html)?', url)
    if match:
        return match.group(1)

    # Alternative pattern for query parameters
    match = re.search(r'infoid=(\d{7,9})', url)
    if match:
        return match.group(1)

    return None


def is_che168_url(url):
    """
    Check if the given URL is a Che168.com listing URL.

    Args:
        url: URL to check

    Returns:
        bool: True if it's a Che168 URL
    """
    return bool(re.match(r'^https?://(www\.|m\.)?che168\.com/.*', url))


def get_che168_car_info(infoid, proxies=None):
    """
    Fetch car information from Che168 API.

    Args:
        infoid: Car listing ID (infoid)
        proxies: Optional proxy configuration dict

    Returns:
        dict: Car information, or None if fetching fails
    """
    try:
        params = {
            "infoid": str(infoid),
            "_appid": "2sc.m",
            "v": "11.41.5",
            "deviceid": "",
            "offertype": "0",
            "ucuserauth": "",
            "gpscid": "0",
            "iscardetailab": "B",
            "encryptinfo": "",
            "fromtag": "0",
            "test103157": "X",
            "userid": "0",
            "s_pid": "0",
            "s_cid": "0",
            "_subappid": "",
        }

        response = requests.get(
            CHE168_API_URL,
            params=params,
            headers=CHE168_HEADERS,
            proxies=proxies,
            timeout=30
        )

        if response.status_code == 200:
            data = response.json()

            if data.get("returncode") == 0 and data.get("result"):
                return parse_che168_response(data["result"])
            else:
                logging.warning(f"Che168 API returned error: {data.get('message')}")
                print(f"Che168 API error: {data.get('message')}")
                return None

        logging.warning(f"Che168 API HTTP error: {response.status_code}")
        return None

    except requests.exceptions.Timeout:
        logging.error("Che168 API timeout")
        print("Che168 API timeout")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Che168 API request failed: {e}")
        print(f"Che168 API request failed: {e}")
        return None
    except Exception as e:
        logging.error(f"Che168 scraper error: {e}")
        print(f"Che168 scraper error: {e}")
        return None


def get_che168_car_specs(infoid, proxies=None):
    """
    Fetch car specifications from Che168 specs API.
    Returns detailed specs including HP.

    Args:
        infoid: Car listing ID
        proxies: Optional proxy configuration dict

    Returns:
        dict: Specs API response, or None if fetching fails
    """
    try:
        params = {
            "infoid": str(infoid),
            "_appid": "2sc.m",
            "v": "11.41.5",
            "deviceid": "",
            "userid": "0",
            "s_pid": "0",
            "s_cid": "0",
            "_subappid": "",
        }

        response = requests.get(
            CHE168_SPECS_API_URL,
            params=params,
            headers=CHE168_HEADERS,
            proxies=proxies,
            timeout=30
        )

        if response.status_code == 200:
            return response.json()

        logging.warning(f"Che168 specs API HTTP error: {response.status_code}")
        return None

    except requests.exceptions.Timeout:
        logging.error("Che168 specs API timeout")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Che168 specs API request failed: {e}")
        return None
    except Exception as e:
        logging.error(f"Che168 specs API error: {e}")
        return None


def extract_hp_from_specs(specs_data):
    """
    Extract horsepower from specs API response.
    Looks for "最大马力(Ps)" in the engine section.

    API returns list of sections like:
    [
      {"title": "发动机", "data": [
        {"name": "最大马力(Ps)", "content": "340", ...},
        ...
      ]},
      ...
    ]

    Args:
        specs_data: Response from get_che168_car_specs()

    Returns:
        int: Horsepower value, or None if not found
    """
    if not specs_data or specs_data.get("returncode") != 0:
        return None

    result = specs_data.get("result", [])

    for section in result:
        # Look in "发动机" (Engine) section for direct HP value
        if section.get("title") == "发动机":
            for item in section.get("data", []):
                if item.get("name") == "最大马力(Ps)":
                    try:
                        return int(item.get("content", 0))
                    except (ValueError, TypeError):
                        pass

        # Fallback: Look in "基本参数" for engine string with HP
        if section.get("title") == "基本参数":
            for item in section.get("data", []):
                if item.get("name") == "发动机":
                    content = item.get("content", "")
                    match = re.search(r'(\d+)马力', content)
                    if match:
                        return int(match.group(1))

    return None


def parse_che168_response(result):
    """
    Parse Che168 API response and extract relevant car data.

    Args:
        result: The 'result' object from API response

    Returns:
        dict: Parsed car information
    """
    # Extract and transform price (multiply by 10,000 as prices are in 万元)
    price_raw = result.get("price", 0)
    price_cny = int(float(price_raw) * 10000)

    # Extract and transform displacement (multiply by 1,000 for cc)
    displacement_raw = result.get("displacement", "0")
    try:
        displacement_liters = float(displacement_raw)
        displacement_cc = int(displacement_liters * 1000)
    except (ValueError, TypeError):
        displacement_cc = 0

    # Parse first registration date (format: "2020-01")
    first_reg_date = result.get("firstregdate", "")
    year, month = parse_registration_date(first_reg_date)

    # Extract mileage (multiply by 10,000 as it's in 万公里)
    mileage_raw = result.get("mileage", 0)
    mileage_km = int(float(mileage_raw) * 10000)

    # Get fuel type code
    fuel_name = result.get("fuelname", "汽油")
    fuel_type_code = FUEL_TYPE_MAPPING.get(fuel_name, 1)
    fuel_type_ru = FUEL_TYPE_NAMES_RU.get(fuel_name, "Бензин")

    # Get photos (limit to 10)
    photos = result.get("piclist", [])[:10]

    # Extract guidance price (original MSRP, also in 万元)
    guidance_price = result.get("guidanceprice", 0)
    guidance_price_cny = int(float(guidance_price) * 10000) if guidance_price else 0

    return {
        # Basic info
        "infoid": result.get("infoid"),
        "car_name": result.get("carname", ""),
        "brand_name": result.get("brandname", ""),
        "series_name": result.get("seriesname", ""),
        "vin_code": result.get("vincode", ""),

        # Price
        "price_cny": price_cny,
        "price_raw": price_raw,  # Original value from API
        "guidance_price_cny": guidance_price_cny,

        # Technical specs
        "displacement_cc": displacement_cc,
        "displacement_liters": displacement_liters,
        "engine": result.get("engine", ""),  # e.g., "3.0T"
        "gearbox": result.get("gearbox", ""),  # e.g., "自动"
        "driving_mode": result.get("drivingmode", ""),  # e.g., "前置四驱"
        "level_name": result.get("levelname", ""),  # e.g., "中大型SUV"

        # Registration and age
        "first_reg_date": first_reg_date,
        "first_reg_year": year,
        "first_reg_month": month,
        "first_reg_str": result.get("firstregstr", ""),  # e.g., "6年1个月"

        # Mileage and condition
        "mileage_km": mileage_km,
        "transfer_count": result.get("transfercount", 0),  # Number of owners
        "color_name": result.get("colorname", ""),
        "car_use_name": result.get("carusename", ""),  # e.g., "家用"

        # Fuel
        "fuel_name": fuel_name,
        "fuel_type_code": fuel_type_code,
        "fuel_type_ru": fuel_type_ru,

        # Location
        "city_id": result.get("cid"),
        "city_name": result.get("cname", ""),
        "province_id": result.get("pid"),

        # Inspection dates
        "examine_date": result.get("examine", ""),  # Next inspection
        "insurance_date": result.get("insurance", ""),  # Insurance expiry

        # Environmental
        "environmental": result.get("environmental", ""),  # e.g., "国VI"

        # Dealer info
        "dealer_id": result.get("dealerid"),
        "user_id": result.get("userid"),

        # Photos
        "photos": photos,
        "main_photo": result.get("imageurl", ""),

        # Loan info
        "is_loan": result.get("isloan", 0),
        "down_payment": result.get("downpayment", 0),

        # Performance (if available)
        "accelerate": result.get("accelerate", ""),  # 0-100 km/h time

        # Consumption
        "nedc_fuel_consumption": result.get("nedc_fuelconsumption", ""),
        "wltc_fuel_consumption": result.get("wltc_fuelconsumption", ""),

        # Source
        "source": "che168",
    }


def parse_registration_date(date_str):
    """
    Parse registration date string into year and month.

    Args:
        date_str: Date string in format "YYYY-MM" (e.g., "2020-01")

    Returns:
        tuple: (year: int, month: int)
    """
    try:
        if "-" in date_str:
            parts = date_str.split("-")
            year = int(parts[0])
            month = int(parts[1])
            return year, month
    except (ValueError, IndexError):
        pass

    # Return current year/month as fallback
    now = datetime.now()
    return now.year, now.month


def format_mileage(mileage_km):
    """
    Format mileage for display.

    Args:
        mileage_km: Mileage in kilometers

    Returns:
        str: Formatted mileage string
    """
    if mileage_km >= 10000:
        return f"{mileage_km / 10000:.1f} тыс. км"
    else:
        return f"{mileage_km:,} км"


def format_gearbox(gearbox_cn):
    """
    Translate gearbox type from Chinese to Russian.

    Args:
        gearbox_cn: Chinese gearbox name

    Returns:
        str: Russian gearbox name
    """
    mapping = {
        "自动": "Автомат",
        "手动": "Механика",
        "手自一体": "Типтроник",
        "无级变速": "Вариатор",
        "双离合": "Робот (DCT)",
    }
    return mapping.get(gearbox_cn, gearbox_cn)


def get_che168_car_info_with_fallback(infoid):
    """
    Fetch car info with proxy fallback mechanism.
    Tries: Oxylabs China → Russian datacenter → direct connection
    Also fetches specs to get HP value.

    Args:
        infoid: Car listing ID (infoid)

    Returns:
        dict: Car information with HP, or None if all proxies fail
    """
    proxy_names = ["Oxylabs China", "Russian datacenter", "direct"]

    for i, proxy in enumerate(CHE168_PROXY_OPTIONS):
        print(f"Trying Che168 API with {proxy_names[i]} proxy...")
        logging.info(f"Trying Che168 API with {proxy_names[i]} proxy...")

        result = get_che168_car_info(infoid, proxies=proxy)
        if result is not None:
            print(f"Success with {proxy_names[i]} proxy")
            logging.info(f"Che168 success with {proxy_names[i]} proxy")

            # Fetch HP from specs API
            print(f"Fetching specs for HP...")
            specs = get_che168_car_specs(infoid, proxies=proxy)
            hp = extract_hp_from_specs(specs)
            result["horsepower"] = hp or 200  # Default to 200 if not found

            if hp:
                print(f"HP extracted: {hp}")
                logging.info(f"Che168 HP extracted: {hp}")
            else:
                print("HP not found in specs, using default: 200")
                logging.warning("Che168 HP not found, using default: 200")

            return result

        print(f"Failed with {proxy_names[i]} proxy, trying next...")
        time.sleep(1)

    logging.error("All Che168 proxy options exhausted")
    print("All Che168 proxy options exhausted")
    return None


if __name__ == "__main__":
    # Test the scraper
    print("Testing Che168 scraper...")

    # Test URL parsing
    test_urls = [
        "https://m.che168.com/dealer/657408/56913158.html",
        "https://www.che168.com/usedcar/56913158.html",
        "https://m.che168.com/v/56913158.html",
    ]

    for url in test_urls:
        car_id = extract_car_id_from_che168_url(url)
        print(f"URL: {url}")
        print(f"  Car ID: {car_id}")
        print(f"  Is Che168 URL: {is_che168_url(url)}")
        print()

    # Test API fetch
    print("Fetching car info for ID 56913158...")
    car_info = get_che168_car_info("56913158")

    if car_info:
        print(f"\nCar: {car_info['car_name']}")
        print(f"Price: ¥{car_info['price_cny']:,}")
        print(f"Displacement: {car_info['displacement_cc']}cc ({car_info['displacement_liters']}L)")
        print(f"First Registration: {car_info['first_reg_date']} ({car_info['first_reg_year']}-{car_info['first_reg_month']:02d})")
        print(f"Mileage: {format_mileage(car_info['mileage_km'])}")
        print(f"Fuel: {car_info['fuel_type_ru']} ({car_info['fuel_name']})")
        print(f"Location: {car_info['city_name']}")
        print(f"VIN: {car_info['vin_code']}")
        print(f"Photos: {len(car_info['photos'])} images")
    else:
        print("Failed to fetch car info")

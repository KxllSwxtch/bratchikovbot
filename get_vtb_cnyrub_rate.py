"""
VTB Bank CNY/RUB Exchange Rate Fetcher

Fetches the Chinese Yuan (CNY) to Russian Ruble (RUB) exchange rate
from VTB Bank API with fallback to Central Bank of Russia (CBR).
"""

import requests
import logging

# VTB Bank API endpoint
VTB_API_URL = "https://www.vtb.ru/api/currencyrates/table/lite"

# CBR API fallback
CBR_API_URL = "https://www.cbr-xml-daily.ru/daily_json.js"

# Request headers for VTB API
VTB_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en,ru;q=0.9",
    "Connection": "keep-alive",
    "Referer": "https://www.vtb.ru/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


def get_vtb_cnyrub_rate():
    """
    Fetch CNY/RUB exchange rate from VTB Bank API.
    Uses the 'offer' rate (bank's sell rate for individuals).

    Returns:
        float: CNY to RUB exchange rate, or None if fetching fails
    """
    try:
        # VTB API parameters
        params = {
            "category": "3",  # Internet/mobile banking rates
            "type": "1",      # Exchange rates
        }

        response = requests.get(
            VTB_API_URL,
            params=params,
            headers=VTB_HEADERS,
            timeout=15
        )

        if response.status_code == 200:
            data = response.json()
            rates = data.get("rates", [])

            # Find CNY rate in the rates list
            for rate in rates:
                currency1 = rate.get("currency1", {})
                currency2 = rate.get("currency2", {})

                if currency1.get("code") == "CNY" and currency2.get("code") == "RUB":
                    # Use 'offer' rate (bank's sell rate)
                    cny_rub_rate = rate.get("offer")
                    if cny_rub_rate:
                        adjusted_rate = float(cny_rub_rate) * 1.025  # Add 2.5%
                        logging.info(f"VTB CNY/RUB rate fetched: {cny_rub_rate}, adjusted (+2.5%): {adjusted_rate}")
                        print(f"VTB CNY/RUB rate fetched: {cny_rub_rate}, adjusted (+2.5%): {adjusted_rate}")
                        return adjusted_rate

            logging.warning("CNY rate not found in VTB response")
            print("CNY rate not found in VTB response, trying CBR fallback...")

    except requests.exceptions.Timeout:
        logging.warning("VTB API timeout, trying CBR fallback...")
        print("VTB API timeout, trying CBR fallback...")
    except requests.exceptions.RequestException as e:
        logging.warning(f"VTB API request failed: {e}, trying CBR fallback...")
        print(f"VTB API request failed: {e}, trying CBR fallback...")
    except Exception as e:
        logging.error(f"VTB rate fetch error: {e}")
        print(f"VTB rate fetch error: {e}, trying CBR fallback...")

    # Fallback to CBR API
    return get_cbr_cnyrub_rate()


def get_cbr_cnyrub_rate():
    """
    Fallback: Fetch CNY/RUB rate from Central Bank of Russia API.

    Returns:
        float: CNY to RUB exchange rate, or None if fetching fails
    """
    try:
        response = requests.get(CBR_API_URL, timeout=10)

        if response.status_code == 200:
            data = response.json()
            cny_data = data.get("Valute", {}).get("CNY", {})

            if cny_data:
                # CBR returns rate per nominal (usually 1 CNY)
                value = cny_data.get("Value", 0)
                nominal = cny_data.get("Nominal", 1)
                rate = value / nominal
                adjusted_rate = float(rate) * 1.025  # Add 2.5%

                logging.info(f"CBR CNY/RUB rate fetched: {rate}, adjusted (+2.5%): {adjusted_rate}")
                print(f"CBR CNY/RUB rate fetched (fallback): {rate}, adjusted (+2.5%): {adjusted_rate}")
                return adjusted_rate

    except requests.exceptions.Timeout:
        logging.error("CBR API timeout")
        print("CBR API timeout")
    except requests.exceptions.RequestException as e:
        logging.error(f"CBR API request failed: {e}")
        print(f"CBR API request failed: {e}")
    except Exception as e:
        logging.error(f"CBR rate fetch error: {e}")
        print(f"CBR rate fetch error: {e}")

    return None


def get_all_vtb_rates():
    """
    Fetch all available currency rates from VTB Bank API.
    Useful for debugging and getting multiple rates at once.

    Returns:
        dict: Dictionary of currency rates {code: offer_rate}
    """
    try:
        params = {
            "category": "3",
            "type": "1",
        }

        response = requests.get(
            VTB_API_URL,
            params=params,
            headers=VTB_HEADERS,
            timeout=15
        )

        if response.status_code == 200:
            data = response.json()
            rates = data.get("rates", [])

            result = {}
            for rate in rates:
                currency1 = rate.get("currency1", {})
                code = currency1.get("code")
                offer = rate.get("offer")
                if code and offer:
                    result[code] = float(offer)

            return result

    except Exception as e:
        logging.error(f"Failed to fetch all VTB rates: {e}")
        return {}


if __name__ == "__main__":
    # Test the rate fetching
    print("Testing VTB CNY/RUB rate fetcher...")
    rate = get_vtb_cnyrub_rate()
    if rate:
        print(f"CNY/RUB rate: {rate}")
        print(f"Example: 100 CNY = {100 * rate:.2f} RUB")
    else:
        print("Failed to fetch rate")

    print("\nAll available VTB rates:")
    all_rates = get_all_vtb_rates()
    for code, offer in all_rates.items():
        print(f"  {code}/RUB: {offer}")

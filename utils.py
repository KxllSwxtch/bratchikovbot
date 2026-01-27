import time
import requests
import datetime
import locale
import random

PROXY_URL = "http://B01vby:GBno0x@45.118.250.2:8000"
proxies = {"http": PROXY_URL, "https": PROXY_URL}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def format_number(number):
    return locale.format_string("%d", int(number), grouping=True).replace(",", ".")


def get_pan_auto_data(car_id):
    """
    Получает данные автомобиля из pan-auto.ru API.
    Возвращает dict с hp, costs и характеристиками авто при успехе.
    Возвращает None при 404 или ошибке.
    """
    url = f"https://zefir.pan-auto.ru/api/cars/{car_id}/"
    headers = {
        "Accept": "*/*",
        "Accept-Language": "en,ru;q=0.9",
        "Cache-Control": "no-cache",
        "Origin": "https://pan-auto.ru",
        "Referer": "https://pan-auto.ru/",
        "User-Agent": random.choice(USER_AGENTS),
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 404:
            print(f"Pan-auto.ru: Car {car_id} not found (404)")
            return None
        response.raise_for_status()
        data = response.json()

        costs_rub = data.get("costs", {}).get("RUB", {})

        return {
            "hp": data.get("hp"),
            "manufacturer": data.get("manufacturer", {}).get("translation"),
            "model": data.get("model", {}).get("translation"),
            "generation": data.get("generation", {}).get("translation") if data.get("generation") else None,
            "displacement": data.get("displacement"),
            "clearance_cost": costs_rub.get("clearanceCost"),
            "customs_duty": costs_rub.get("customsDuty"),
            "utilization_fee": costs_rub.get("utilizationFee"),
            "final_cost": costs_rub.get("finalCost"),
            "car_price_rub": costs_rub.get("carPrice"),
            "delivery_cost": costs_rub.get("deliveryCost"),
            "car_price_encar": costs_rub.get("carPriceEncar"),
            "vladivostok_services": costs_rub.get("vladivostokServices"),
            # Дополнительные поля
            "mileage": data.get("mileage"),
            "year": data.get("formYear"),
            "fuel_type": data.get("fuelType"),
            "color": data.get("color"),
            "badge": data.get("badge"),
            "badge_detail": data.get("badgeDetail"),
            "photos": data.get("photos", []),
        }
    except requests.RequestException as e:
        print(f"Ошибка при запросе к pan-auto.ru: {e}")
        return None


def calculate_age(year, month):
    """
    Рассчитывает возрастную категорию автомобиля по классификации calcus.ru.

    :param year: Год выпуска автомобиля
    :param month: Месяц выпуска автомобиля
    :return: Возрастная категория ("0-3", "3-5", "5-7", "7-0")
    """
    # Убираем ведущий ноль у месяца, если он есть
    month = int(month.lstrip("0")) if isinstance(month, str) else int(month)

    current_date = datetime.datetime.now()
    car_date = datetime.datetime(year=int(year), month=month, day=1)

    age_in_months = (
        (current_date.year - car_date.year) * 12 + current_date.month - car_date.month
    )

    if age_in_months < 36:
        return "0-3"
    elif 36 <= age_in_months < 60:
        return "3-5"
    elif 60 <= age_in_months < 84:
        return "5-7"
    else:
        return "7-0"


def get_customs_fees_manual(engine_volume, car_price, car_age, engine_type=1, power=1, currency="KRW"):
    """
    Запрашивает расчёт таможенных платежей с сайта calcus.ru.
    :param engine_volume: Объём двигателя (куб. см)
    :param car_price: Цена авто в вонах
    :param car_age: Возрастная категория авто
    :param engine_type: Тип двигателя (1 - бензин, 2 - дизель, 3 - гибрид, 4 - электромобиль)
    :param power: Мощность двигателя в л.с.
    :return: JSON с результатами расчёта
    """
    url = "https://calcus.ru/calculate/Customs"

    payload = {
        "owner": 1,  # Физлицо
        "age": car_age,  # Возрастная категория
        "engine": engine_type,  # Тип двигателя (по умолчанию 1 - бензин)
        "power": power,  # Лошадиные силы
        "power_unit": 1,  # Тип мощности (1 - л.с.)
        "value": int(engine_volume),  # Объём двигателя
        "price": int(car_price),  # Цена авто
        "curr": currency,  # Валюта
    }

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://calcus.ru/",
        "Origin": "https://calcus.ru",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    try:
        response = requests.post(url, data=payload, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Ошибка при запросе к calcus.ru: {e}")
        return None


def get_customs_fees(
    engine_volume, car_price, car_year, car_month, engine_type=1, owner_type=1, power=1, currency="KRW"
):
    """
    Запрашивает расчёт таможенных платежей с сайта calcus.ru.
    :param engine_volume: Объём двигателя (куб. см)
    :param car_price: Цена авто в вонах
    :param car_year: Год выпуска авто
    :param car_month: Месяц выпуска авто
    :param engine_type: Тип двигателя (1 - бензин, 2 - дизель, 3 - гибрид, 4 - электромобиль)
    :param owner_type: Тип владельца (1 - физлицо, 2 - юрлицо)
    :param power: Мощность двигателя в л.с.
    :return: JSON с результатами расчёта
    """
    url = "https://calcus.ru/calculate/Customs"

    payload = {
        "owner": owner_type,  # 1 - Физлицо, 2 - Юрлицо
        "age": calculate_age(car_year, car_month),  # Возрастная категория
        "engine": engine_type,  # Тип двигателя (по умолчанию 1 - бензин)
        "power": power,  # Лошадиные силы
        "power_unit": 1,  # Тип мощности (1 - л.с.)
        "value": int(engine_volume),  # Объём двигателя
        "price": int(car_price),  # Цена авто
        "curr": currency,  # Валюта
    }

    print(engine_volume, car_price, car_year, car_month, engine_type, owner_type, power)

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://calcus.ru/",
        "Origin": "https://calcus.ru",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    try:
        response = requests.post(url, data=payload, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Ошибка при запросе к calcus.ru: {e}")
        return None


def clean_number(value):
    """Очищает строку от пробелов и преобразует в число"""
    return int(float(value.replace(" ", "").replace(",", ".")))


def generate_encar_photo_url(photo_path):
    """
    Формирует правильный URL для фотографий Encar.
    Пример результата: https://ci.encar.com/carpicture02/pic3902/39027097_006.jpg
    """

    base_url = "https://ci.encar.com"
    photo_url = f"{base_url}/{photo_path}"

    return photo_url

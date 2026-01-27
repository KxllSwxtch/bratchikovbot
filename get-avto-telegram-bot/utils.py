import requests
import datetime
import locale
import math
import gc
import time
import threading
import re

PROXY = "http://B01vby:GBno0x@45.118.250.2:8000"
RESIDENTIAL_PROXY = (
    "http://oGKgjVaIooWADkOR:O8J73QYtjYWgQj4m_country-ru@geo.iproyal.com:12321"
)
proxies = {"http": PROXY, "https": PROXY}

# Calcus.ru fuel type mappings (from calcus.ru website)
FUEL_TYPE_GASOLINE = 1       # Бензиновый
FUEL_TYPE_DIESEL = 2         # Дизельный
FUEL_TYPE_ELECTRIC = 4       # Электрический
FUEL_TYPE_HYBRID_SERIES = 5  # Последовательный гибрид
FUEL_TYPE_HYBRID_PARALLEL = 6  # Параллельный гибрид

FUEL_TYPE_NAMES = {
    FUEL_TYPE_GASOLINE: "Бензин",
    FUEL_TYPE_DIESEL: "Дизель",
    FUEL_TYPE_ELECTRIC: "Электро",
    FUEL_TYPE_HYBRID_SERIES: "Гибрид (посл.)",
    FUEL_TYPE_HYBRID_PARALLEL: "Гибрид (парал.)",
}


class RateLimiter:
    """Простой rate limiter для ограничения количества запросов в секунду"""

    def __init__(self, rate_limit=5):
        """
        :param rate_limit: Максимальное количество запросов в секунду
        """
        self.rate_limit = rate_limit
        self.tokens = rate_limit
        self.last_update = time.time()
        self.lock = threading.Lock()

    def acquire(self):
        """Ожидает, пока можно будет сделать запрос"""
        with self.lock:
            while True:
                now = time.time()
                time_passed = now - self.last_update
                self.tokens = min(
                    self.rate_limit, self.tokens + time_passed * self.rate_limit
                )
                self.last_update = now

                if self.tokens >= 1:
                    self.tokens -= 1
                    return

                # Ждем, пока появится токен
                sleep_time = (1 - self.tokens) / self.rate_limit
                time.sleep(sleep_time)


# Создаем глобальный rate limiter для calcus.ru (5 запросов в секунду)
calcus_rate_limiter = RateLimiter(rate_limit=5)

# Список User-Agent для запросов к calcus.ru
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]


def get_random_user_agent():
    """
    Returns a random user agent from the USER_AGENTS list.
    This is useful for avoiding detection as a bot.
    """
    import random

    return random.choice(USER_AGENTS)


def get_pan_auto_car_data(car_id):
    """
    Fetches car data from pan-auto.ru API including HP and pre-calculated customs.

    :param car_id: Encar car ID (e.g., "41074555")
    :return: dict with car data or None if not found
    """
    url = f"https://zefir.pan-auto.ru/api/cars/{car_id}/"

    headers = {
        "Accept": "*/*",
        "Origin": "https://pan-auto.ru",
        "Referer": "https://pan-auto.ru/",
        "User-Agent": get_random_user_agent(),
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json()
        return None
    except requests.RequestException as e:
        print(f"Error fetching pan-auto.ru data: {e}")
        return None


def generate_encar_photo_url(photo_path):
    """
    Формирует правильный URL для фотографий Encar.
    Пример результата: https://ci.encar.com/carpicture02/pic3902/39027097_006.jpg
    """

    base_url = "https://ci.encar.com"
    photo_url = f"{base_url}/{photo_path}"

    return photo_url


def sort_photo_urls(photo_urls):
    """
    Sort photo URLs by their numeric key (e.g., 41074555_001.jpg -> 001).
    URLs without a valid key are placed at the end.
    """

    def extract_photo_number(url):
        # Match pattern like 41074555_019.jpg or similar
        match = re.search(r"_(\d+)\.jpg", url)
        if match:
            return int(match.group(1))
        return float("inf")  # Put URLs without numbers at the end

    return sorted(photo_urls, key=extract_photo_number)


def get_rub_to_krw_rate():
    url = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/rub.json"

    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        original_rate = data["rub"]["krw"]
        adjusted_rate = original_rate * 1.03
        return adjusted_rate
    except requests.RequestException as e:
        print(f"Error fetching exchange rate: {e}")
        return None


def clean_number(value):
    """Очищает строку от пробелов и преобразует в число"""
    return int(float(value.replace(" ", "").replace(",", ".")))


def get_customs_fees(engine_volume, car_price, car_year, car_month, power=1, engine_type=1, currency="KRW"):
    """
    Запрашивает расчёт таможенных платежей с сайта calcus.ru.
    :param engine_volume: Объём двигателя (куб. см)
    :param car_price: Цена авто в указанной валюте
    :param car_year: Год выпуска авто
    :param car_month: Месяц выпуска авто
    :param power: Мощность двигателя в л.с. (важно для расчёта утильсбора с 01.12.2024)
    :param engine_type: Тип двигателя (1 - бензин, 2 - дизель, 4 - электро, 5 - гибрид посл., 6 - гибрид парал.)
    :param currency: Валюта цены ("KRW" для Кореи, "CNY" для Китая)
    :return: JSON с результатами расчёта
    """
    url = "https://calcus.ru/calculate/Customs"

    payload = {
        "owner": 1,  # Физлицо
        "age": calculate_age(car_year, car_month),  # Возрастная категория
        "engine": engine_type,  # Тип двигателя (по умолчанию 1 - бензин)
        "power": int(power),  # Мощность двигателя в л.с.
        "power_unit": 1,  # Тип мощности (1 - л.с.)
        "value": int(engine_volume),  # Объём двигателя
        "price": int(car_price),  # Цена авто
        "curr": currency,  # Валюта (KRW или CNY)
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
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


# Utility function to calculate the age category
# BACKUP
# def calculate_age(year, month):
#     # Убираем ведущий ноль у месяца, если он есть
#     month = int(month.lstrip("0")) if isinstance(month, str) else int(month)

#     current_date = datetime.datetime.now()
#     car_date = datetime.datetime(year=int(year), month=month, day=1)

#     age_in_months = (
#         (current_date.year - car_date.year) * 12 + current_date.month - car_date.month
#     )

#     if age_in_months < 36:
#         return f"До 3 лет"
#     elif 36 <= age_in_months < 60:
#         return f"от 3 до 5 лет"
#     else:
#         return f"от 5 лет"


# Utility function to calculate the age category
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


def format_number(number):
    return locale.format_string("%d", number, grouping=True)


# Округляем объёмы ДВС
def round_engine_volume(volume):
    return math.ceil(int(volume) / 100) * 100  # Округление вверх до ближайшей сотни


# Очищение памяти
def clear_memory():
    gc.collect()


# Расчёт таможенного сбора
def calculate_customs_fee(car_price_rub):
    """
    Рассчитывает таможенный сбор в зависимости от стоимости автомобиля в рублях.
    """
    if car_price_rub <= 200000:
        return 1067
    elif car_price_rub <= 450000:
        return 2134
    elif car_price_rub <= 1200000:
        return 4269
    elif car_price_rub <= 2700000:
        return 11746
    elif car_price_rub <= 4200000:
        return 16524
    elif car_price_rub <= 5500000:
        return 21344
    elif car_price_rub <= 7000000:
        return 27540
    else:
        return 30000


# Таможенная пошлина
def calculate_customs_duty(car_price_euro, engine_volume, euro_to_rub_rate, age):
    """
    Рассчитывает таможенную пошлину для РФ в зависимости от стоимости автомобиля в евро,
    объема двигателя, курса евро к рублю и возраста автомобиля.
    """
    engine_volume = int(engine_volume)

    # Для автомобилей младше 3 лет
    if age == "до 3 лет":
        if car_price_euro <= 8500:
            duty = max(car_price_euro * 0.54, engine_volume * 2.5)
        elif car_price_euro <= 16700:
            duty = max(car_price_euro * 0.48, engine_volume * 3.5)
        elif car_price_euro <= 42300:
            duty = max(car_price_euro * 0.48, engine_volume * 5.5)
        elif car_price_euro <= 84500:
            duty = max(car_price_euro * 0.48, engine_volume * 7.5)
        elif car_price_euro <= 169000:
            duty = max(car_price_euro * 0.48, engine_volume * 15)
        else:
            duty = max(car_price_euro * 0.48, engine_volume * 20)

    # Для автомобилей от 3 до 5 лет
    elif age == "от 3 до 5 лет":
        if engine_volume <= 1000:
            duty = engine_volume * 1.5
        elif engine_volume <= 1500:
            duty = engine_volume * 1.7
        elif engine_volume <= 1800:
            duty = engine_volume * 2.5
        elif engine_volume <= 2300:
            duty = engine_volume * 2.7
        elif engine_volume <= 3000:
            duty = engine_volume * 3
        else:
            duty = engine_volume * 3.6

    # Для автомобилей старше 5 лет
    elif age == "старше 5 лет" or age == "от 5 лет":
        if engine_volume <= 1000:
            duty = engine_volume * 3
        elif engine_volume <= 1500:
            duty = engine_volume * 3.2
        elif engine_volume <= 1800:
            duty = engine_volume * 3.5
        elif engine_volume <= 2300:
            duty = engine_volume * 4.8
        elif engine_volume <= 3000:
            duty = engine_volume * 5
        else:
            duty = engine_volume * 5.7

    else:
        raise ValueError("Некорректный возраст автомобиля")

    return round(duty * euro_to_rub_rate, 2)


# Утильсбор
def calculate_recycling_fee(engine_volume, age):
    """
    Рассчитывает утилизационный сбор в России для физических лиц.

    :param engine_volume: Объём двигателя в куб. см.
    :param age: Возраст автомобиля.
    :return: Утилизационный сбор в рублях.
    """
    base_rate = 20000  # Базовая ставка для легковых авто

    # Проверяем возраст автомобиля и устанавливаем соответствующий коэффициент
    if age == "до 3 лет":
        if engine_volume <= 1000:
            coefficient = 0.17
        elif engine_volume <= 2000:
            coefficient = 0.17
        elif engine_volume <= 3000:
            coefficient = 0.17
        elif engine_volume <= 3500:
            coefficient = 107.67
        else:  # Для свыше 3500 см³
            coefficient = 137.11
    else:  # Для автомобилей старше 3 лет (от 3 до 5 лет и старше 5 лет)
        if engine_volume <= 1000:
            coefficient = 0.26
        elif engine_volume <= 2000:
            coefficient = 0.26
        elif engine_volume <= 3000:
            coefficient = 0.26
        elif engine_volume <= 3500:
            coefficient = 165.84
        else:  # Для свыше 3500 см³
            coefficient = 180.24  # Исправленный коэффициент

    # Рассчитываем утилизационный сбор
    recycling_fee = base_rate * coefficient
    return round(recycling_fee, 2)

import requests


def clean_number(value):
    """Очищает строку от пробелов и преобразует в число"""
    return int(float(value.replace(" ", "").replace(",", ".")))


def get_customs_fees(engine_volume, car_price, car_year, car_month, engine_type=1):
    """
    Запрашивает расчёт таможенных платежей с сайта calcus.ru.
    :param engine_volume: Объём двигателя (куб. см)
    :param car_price: Цена авто в вонах
    :param car_year: Год выпуска авто
    :param engine_type: Тип двигателя (1 - бензин, 2 - дизель, 3 - гибрид, 4 - электромобиль)
    :return: JSON с результатами расчёта
    """
    url = "https://calcus.ru/calculate/Customs"

    payload = {
        "owner": 1,  # Физлицо
        "age": "0-3",  # Возрастная категория
        "engine": engine_type,  # Тип двигателя (по умолчанию 1 - бензин)
        "power": 1,  # Лошадиные силы (можно оставить 1)
        "power_unit": 1,  # Тип мощности (1 - л.с.)
        "value": engine_volume,  # Объём двигателя
        "price": car_price,  # Цена авто в KRW
        "curr": "KRW",  # Валюта
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


result = get_customs_fees(3342, 30600000, 2022, 4, engine_type=1)

print(result)

customs_fee = clean_number(result["sbor"])
customs_duty = clean_number(result["tax"])
util = clean_number(result["util"])

print(customs_fee, customs_duty, util)

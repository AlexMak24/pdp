from bisect import bisect_right

bins = [1, 10, 30, 70, 150, 300, 500]

stop_loss_dict = {
    (0.0, 'BUYING'): -1.33 / 100,
    (0.0, 'SELLING'): 1.03 / 100,
    (1.0, 'BUYING'): -1.85 / 100,
    (1.0, 'SELLING'): 1.57 / 100,
    (2.0, 'BUYING'): -3.01 / 100,
    (2.0, 'SELLING'): 3.26 / 100,
    (3.0, 'BUYING'): -3.62 / 100,
    (3.0, 'SELLING'): 3.67 / 100,
    (4.0, 'BUYING'): -4.48 / 100,
    (4.0, 'SELLING'): 5.29 / 100,
    (5.0, 'BUYING'): -1.78 / 100,
    (5.0, 'SELLING'): 18.52 / 100,
}

def get_stop_loss_percent(time_minutes: int, direction: str) -> float:
    """
    Возвращает стоп-лосс в процентах на основе времени и направления сделки
    """
    bin_index = bisect_right(bins, time_minutes) - 1
    bin_index = float(bin_index)

    key = (bin_index, direction.upper())
    if key not in stop_loss_dict:
        raise ValueError(f"Нет стоп-лосса для ключа {key}")

    return round(stop_loss_dict[key] * 100, 2)
sl = get_stop_loss_percent(78, 'buying')
print(f"Стоп-лосс: {sl}%")

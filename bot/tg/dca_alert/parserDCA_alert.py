import asyncio
import json
import re
from datetime import datetime, timedelta
from pyrogram import Client, filters
import queue

# Настройки Pyrogram
API_ID = 2867162
API_HASH = '0e7fe16d0f9ecfec4c58315e32991ea8'
SESSION_NAME = "my_session"
SOURCE_CHANNEL = -1001863236190  # ID канала DCA Alert

# Инициализация клиента Pyrogram
pyro_client = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH)

# Очередь для передачи сообщений в Telebot
message_queue = queue.Queue()

# Структура для хранения текущих сделок
current_trades = {}

# Файл для хранения закрытых сделок
ALL_TRADES_FILE = '../all_trades.json'

# Регулярные выражения для извлечения данных
regex = {
    'ticker': re.compile(
        r'\$([\d.]+[MK]?)\s+(?:'  # Сумма
        r'(BUYING|SELLING)\s+\$?([A-Za-z]+)(?:\s*[🟥🟩]|\s|$)|'  # Стандартный формат: BUYING/SELLING
        r'([A-Za-z]+)\s*\([+-]?\d+\.?\d+%\)\s*[🟥🟩]\s*➞\s*([A-Za-z]+)(?:\s*\([+-]?\d+\.?\d+%\)\s*[🟥🟩]|\s|$)'  # Конвертация
        r')', re.IGNORECASE
    ),
    'potential_price_change': re.compile(r'potential price change:\s*([+-]?\d+.?\d+)%', re.IGNORECASE),
    'mcap': re.compile(r'MCAP:\s*\$?([\d.]+[MK]?B?)', re.IGNORECASE),
    'liquidity': re.compile(r'Liquidity:\s*\$?([\d.]+[MK]?)', re.IGNORECASE),
    'vol_24h': re.compile(r'Vol 24h:\s*\$?([\d.]+[MK]?)', re.IGNORECASE),
    'vol_1h': re.compile(r'Vol 1h:\s*\$?([\d.]+[MK]?)', re.IGNORECASE),
    'frequency': re.compile(r'Frequency:\s*\$?([\d.]+[MK]?\s+every\s+[\d\w]+(?:in)?)', re.IGNORECASE),
    'eta': re.compile(r'ETA:\s*(\d+\.?\d*h)?\s*(\d+\.?\d*m)?', re.IGNORECASE),
    'futures': re.compile(r'Futures:\s*([\w\s]+?)(?:\n|$)', re.IGNORECASE),
    'period': re.compile(r'(\d{2} \w{3} \d{4} \d{2}:\d{2}:\d{2} UTC ➞ \d{2} \w{3} \d{4} \d{2}:\d{2}:\d{2} UTC)'),
    'ca': re.compile(r'CA:\s*([A-Za-z0-9]+)'),
    'user': re.compile(r'User:\s*([A-Za-z0-9]+)'),
    'amount': re.compile(r'Amount:\s*([\d.]+)\s+[A-Za-z]+\s+\$([\d.]+[MK]?)'),
    'created': re.compile(
        r'Created:\s*((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s*\d{2} \w{3} \d{4} \d{2}:\d{2}:\d{2} (GMT|UTC))'),
    'finish': re.compile(
        r'Finish:\s*((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s*\d{2} \w{3} \d{4} \d{2}:\d{2}:\d{2} (GMT|UTC))')
}

# Функция для преобразования суммы в USD
def parse_amount(amount_str):
    """Преобразует строку суммы (например, '$150.02K' или '$544.42M') в float (в USD)."""
    if not amount_str:
        return 0.0
    amount_str = amount_str.replace('$', '').strip()
    multiplier = 1
    if 'k' in amount_str.lower():
        multiplier = 1000
        amount_str = amount_str.lower().replace('k', '')
    elif 'm' in amount_str.lower():
        multiplier = 1000000
        amount_str = amount_str.lower().replace('m', '')
    elif 'b' in amount_str.lower():
        multiplier = 1000000000
        amount_str = amount_str.lower().replace('b', '')
    return float(amount_str) * multiplier

# Функция для парсинга ETA
def parse_eta(eta_text):
    """Извлекает ETA из текста и возвращает его в минутах."""
    if not eta_text:
        return 0
    hours = 0
    minutes = 0
    hours_match = re.search(r'(\d+\.?\d*)h', eta_text.lower())
    if hours_match:
        hours = float(hours_match.group(1))
    minutes_match = re.search(r'(\d+\.?\d*)m', eta_text.lower())
    if minutes_match:
        minutes = float(minutes_match.group(1))
    return int(hours * 60 + minutes)

# Функция для форматирования уведомления о новой сделке
def format_trade_notification(trade_data):
    """Форматирует данные о сделке в читаемое уведомление."""
    symbol = trade_data['symbol']
    side = trade_data['side'].capitalize()
    amount_usd = f"${trade_data['amount_usd'] / 1000:.2f}K"
    price = f"${trade_data['price']:.6f}" if trade_data['price'] else "N/A"
    eta_minutes = trade_data['eta_minutes']
    eta_str = f"{eta_minutes // 60}h {eta_minutes % 60}min" if eta_minutes else "N/A"
    start_time = trade_data['start_time']
    end_time = trade_data['end_time'] or "N/A"
    potential_change = f"{trade_data['potential_price_change']:.2f}%" if trade_data['potential_price_change'] else "N/A"

    notification = (
        f"📈 Новая сделка:\n"
        f"Символ: {symbol}\n"
        f"Тип: {side}\n"
        f"Сумма: {amount_usd}\n"
        f"Цена: {price}\n"
        f"Потенциальное изменение: {potential_change}\n"
        f"ETA: {eta_str}\n"
        f"Начало: {start_time}\n"
        f"Конец: {end_time}"
    )
    return notification

# Функция для сериализации сообщения
def serialize_message(message):
    """Извлекает ключевые данные из текста сообщения с использованием регулярных выражений."""
    text = message.text.lower()
    if "range: ❌" in text:
        print(f"Сообщение {message.id} игнорируется из-за 'range: ❌'")
        return None
    try:
        # Если сообщение ссылается на другое, это закрытие
        reply_to_id = message.reply_to_message_id
        if reply_to_id:
            print(f"Распознано сообщение о закрытии с reply_to_message_id: {reply_to_id}")
            symbol_match = re.search(r'\[(\w+)\]', text)
            symbol = symbol_match.group(1) if symbol_match else None
            return {
                'type': 'close',
                'symbol': symbol,
                'message_id': message.id,
                'reply_to_message_id': reply_to_id,
                'text': message.text
            }

        # Извлечение данных о сделке с помощью регулярных выражений
        ticker_match = regex['ticker'].search(text)
        if not ticker_match:
            print(f"Сообщение {message.id} не распознано как новая сделка (нет ticker)")
            return None

        # Разбираем группы из регулярки
        amount_str, side, symbol_std, symbol_from, symbol_to = ticker_match.groups()
        if side:  # Стандартный формат: BUYING/SELLING
            amount_usd = parse_amount(amount_str)
            symbol = symbol_std
            side = side.lower()
        elif symbol_from and symbol_to:  # Формат конвертации
            amount_usd = parse_amount(amount_str)
            symbol = symbol_to  # Используем второй тикер (POPCAT)
            side = 'buy'  # Конвертация подразумевает покупку второго актива
        else:
            print(f"Сообщение {message.id} не распознано как сделка")
            return None

        # Извлечение цены (берем цену второго тикера для конвертации)
        price_match = re.search(r'price:\s*\$?([\d.]+)', text.split(symbol)[1] if side == 'buy' and symbol_to else text)
        price = float(price_match.group(1)) if price_match else None

        # Извлечение ETA
        eta_match = regex['eta'].search(text)
        eta_text = eta_match.group(0) if eta_match else None
        eta_minutes = parse_eta(eta_text.split('eta:')[1].strip() if eta_text else None)

        # Извлечение периода
        period_match = regex['period'].search(text)
        if period_match:
            start_time_str, end_time_str = period_match.group(1).split(' ➞ ')
            start_time = datetime.strptime(start_time_str.strip(), '%d %b %Y %H:%M:%S UTC')
            end_time = datetime.strptime(end_time_str.strip(), '%d %b %Y %H:%M:%S UTC')
        else:
            start_time = datetime.utcnow()
            end_time = start_time + timedelta(minutes=eta_minutes) if eta_minutes else None

        # Дополнительные данные (опционально)
        potential_price_change = regex['potential_price_change'].search(text.split(symbol)[1] if side == 'buy' and symbol_to else text)
        mcap = regex['mcap'].search(text)
        liquidity = regex['liquidity'].search(text)
        vol_24h = regex['vol_24h'].search(text)
        vol_1h = regex['vol_1h'].search(text)
        frequency = regex['frequency'].search(text)
        futures = regex['futures'].search(text)
        ca = regex['ca'].search(text)
        user = regex['user'].search(text)

        trade_data = {
            'type': 'trade',
            'message_id': message.id,
            'symbol': symbol,
            'side': side,
            'amount_usd': amount_usd,
            'price': price,
            'start_time': str(start_time),
            'end_time': str(end_time) if end_time else None,
            'eta_minutes': eta_minutes,
            'status': 'pending',
            'text': message.text,
            'potential_price_change': float(potential_price_change.group(1)) if potential_price_change else None,
            'mcap': parse_amount(mcap.group(1)) if mcap else None,
            'liquidity': parse_amount(liquidity.group(1)) if liquidity else None,
            'vol_24h': parse_amount(vol_24h.group(1)) if vol_24h else None,
            'vol_1h': parse_amount(vol_1h.group(1)) if vol_1h else None,
            'frequency': frequency.group(1) if frequency else None,
            'futures': futures.group(1).strip() if futures else None,
            'ca': ca.group(1) if ca else None,
            'user': user.group(1) if user else None
        }
        print(f"Сериализована новая сделка: {trade_data}")
        message_queue.put(format_trade_notification(trade_data))
        return trade_data

    except Exception as e:
        print(f"Ошибка при сериализации сообщения {message.id}: {e}")
        return None

# Функция для закрытия сделки
def close_trade(trade, reason):
    """Закрывает сделку и сохраняет её в файл."""
    trade['status'] = 'closed'
    trade['close_time'] = str(datetime.utcnow())
    trade['close_reason'] = reason
    with open(ALL_TRADES_FILE, 'a') as f:
        json.dump(trade, f)
        f.write('\n')
    print(f"Сделка {trade['message_id']} закрыта: {reason}")
    notification = f"Сделка закрыта: {trade['symbol']} {trade['side']} по причине: {reason}"
    message_queue.put(notification)

# Функция для обработки сообщения о закрытии
def handle_close_message(close_data):
    """Обрабатывает сообщение о закрытии сделки."""
    reply_to_id = close_data['reply_to_message_id']
    symbol = close_data['symbol'] if close_data['symbol'] else "unknown"

    if reply_to_id and reply_to_id in current_trades:
        trade = current_trades[reply_to_id]
        if trade['status'] == 'pending':
            close_trade(trade, 'closed by user')
            del current_trades[reply_to_id]
            print(f"Сделка {reply_to_id} для {symbol} закрыта по сообщению {close_data['message_id']}")
        else:
            print(f"Сделка {reply_to_id} уже закрыта или неактивна")
    else:
        print(f"Не найдена открытая сделка для reply_to_message_id: {reply_to_id} по сообщению {close_data['message_id']}")

# Обработчик сообщений из канала
@pyro_client.on_message(filters.chat(SOURCE_CHANNEL))
async def new_message_handler(client, message):
    if message.text:
        print(f"\nКанал: {SOURCE_CHANNEL}")
        print(f"ID сообщения: {message.id}")
        print(f"Текст: {message.text}")

        data = serialize_message(message)
        if data:
            if data['type'] == 'trade':
                current_trades[data['message_id']] = data
                print(f"Добавлена новая сделка: {data['symbol']} {data['side']}")
            elif data['type'] == 'close':
                handle_close_message(data)
        else:
            print(f"Сообщение {message.id} не распознано")

        print(f"Текущие сделки: {current_trades}")
        print('-' * 50)

# Функция для проверки таймаутов
async def check_trade_timeouts():
    """Периодически проверяет сделки на истечение времени."""
    while True:
        current_time = datetime.utcnow()
        for trade_id, trade in list(current_trades.items()):
            if trade['status'] == 'pending' and trade['end_time']:
                end_time = datetime.strptime(trade['end_time'], '%Y-%m-%d %H:%M:%S')
                if current_time >= end_time:
                    close_trade(trade, 'timeout')
                    del current_trades[trade_id]
        await asyncio.sleep(60)  # Проверка каждую минуту

# Основная функция
async def main():
    """Запускает программу."""
    asyncio.create_task(check_trade_timeouts())  # Фоновая проверка таймаутов
    await pyro_client.start()

# Запуск программы
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Программа остановлена пользователем")
    except Exception as e:
        print(f"Необработанная ошибка: {e}")
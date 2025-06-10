import asyncio
import json
import re
import time
import logging
from datetime import datetime, UTC, timedelta
from pyrogram import Client, filters
from pyrogram.errors.exceptions.bad_request_400 import PeerIdInvalid
import queue
from telebot.async_telebot import AsyncTeleBot
from bot.tg.DCACall.bybit_trader import BybitTrader
from bisect import bisect_right

import telebot
BOT_TOKEN = "8023569170:AAHo_j_38aFIV07KpppsX4zOTwl97lnRg_E"
tb_bot = telebot.TeleBot(BOT_TOKEN)

# Настройка логирования в файл
logging.basicConfig(filename='bot.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)
ALL_TRADES_FILE = '../all_trades.json'
# Настройка логирования в консоль
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(message)s')
console_handler.setFormatter(console_formatter)
console_logger = logging.getLogger('console')
console_logger.setLevel(logging.INFO)
console_logger.addHandler(console_handler)
console_logger.propagate = False

# Настройки Pyrogram
API_ID = 2867162
API_HASH = '0e7fe16d0f9ecfec4c58315e32991ea8'
SESSION_NAME = "my_session"
SOURCE_CHANNELS = [-1002258474088,-1001537682698]

# Настройки Telebot
TELEBOT_TOKEN = "8023569170:AAHo_j_38aFIV07KpppsX4zOTwl97lnRg_E"
USER_ID = 793784229

# Путь к файлу для хранения состояния trading
TRADING_STATE_FILE = "trading_state.json"

# Инициализация клиентов
pyro_client = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH)
telebot_client = AsyncTeleBot(TELEBOT_TOKEN)

# Инициализация BybitTrader
trader = BybitTrader(
    api_key="577ZLJi9GvUAWLRyDt",
    api_secret="CWmMdET6GhOhJJCHAWhS2GZo9rB5R9sKxZYy",
    testnet=False,
    position_size_usd=100
)

# Очередь для передачи сообщений
message_queue = queue.Queue()

# Структура для хранения текущих сделок
pending_signals = {}
current_trades = {}

# Файл для хранения закрытых сделок
ALL_TRADES_FILE = '../all_trades.json'

# Регулярные выражения для извлечения данных
regex = {
    'ticker': re.compile(
        r'\$([\d.]+[MK]?)\s+(?:'
        r'(BUYING|SELLING)\s+\$?([A-Za-z]+)(?:\s*[🟥🟩]|\s|$)|'
        r'([A-Za-z]+)\s*\([+-]?\d+\.?\d+%\)\s*[🟥🟩]\s*➞\s*([A-Za-z]+)(?:\s*\([+-]?\d+\.?\d+%\)\s*[🟥🟩]|\s|$)'
        r')', re.IGNORECASE
    ),
    'potential_price_change': re.compile(r'potential price change:\s*([+-]?\d+.?\d+)%', re.IGNORECASE),
    'mcap': re.compile(r'MCAP:\s*\$?([\d.]+[MK]?B?)', re.IGNORECASE),
    'liquidity': re.compile(r'Liquidity:\s*\$?([\d.]+[MK]?)', re.IGNORECASE),
    'vol_24h': re.compile(r'Vol 24h:\s*\$?([\d.]+[MK]?)', re.IGNORECASE),
    'vol_1h': re.compile(r'Vol 1h:\s*\$?([\d.]+[MK]?)', re.IGNORECASE),
    'frequency': re.compile(r'Frequency:\s*\$?([\d.]+[MK]?\s+every\s+[\d\w]+(?:in)?)', re.IGNORECASE),
    'eta': re.compile(r'ETA:\s*(\d+\.?\d*\s*h)?\s*,?\s*(\d+\.?\d*\s*(m|min))?', re.IGNORECASE),
    'futures': re.compile(r'Futures:\s*([\w\s]+?)(?:\n|$)', re.IGNORECASE),
    'period': re.compile(
        r'(\d{2} \w{3} \d{4} \d{2}:\d{2}:\d{2} (UTC|GMT) ➞ \d{2} \w{3} \d{4} \d{2}:\d{2}:\d{2} (UTC|GMT))'),
    'ca': re.compile(r'CA:\s*([A-Za-z0-9]+)'),
    'user': re.compile(r'User:\s*([A-Za-z0-9]+)'),
    'amount': re.compile(r'Amount:\s*([\d.]+)\s+[A-Za-z]+\s+\$([\d.]+[MK]?)'),
    'created': re.compile(
        r'Created:\s*((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s*\d{2} \w{3} \d{4} \d{2}:\d{2}:\d{2} (GMT|UTC))'),
    'finish': re.compile(
        r'Finish:\s*((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s*\d{2} \w{3} \d{4} \d{2}:\d{2}:\d{2} (GMT|UTC))')
}

# --- Логика стоп-лосса ---
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
    """Рассчитывает стоп-лосс в процентах на основе времени и направления."""
    bin_index = bisect_right(bins, time_minutes) - 1
    bin_index = float(bin_index)
    key = (bin_index, direction.upper())
    if key not in stop_loss_dict:
        raise ValueError(f"Нет стоп-лосса для ключа {key}")
    return round(stop_loss_dict[key] * 100, 2)

# --- Вспомогательные функции ---

def parse_amount(amount_str):
    """Парсит сумму с учетом множителей (K, M, B)."""
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

def parse_eta(eta_text):
    """Парсит ETA в минутах из текста."""
    if not eta_text:
        return 0
    hours = 0
    minutes = 0
    hours_match = re.search(r'(\d+\.?\d*)\s*h', eta_text.lower())
    if hours_match:
        hours = float(hours_match.group(1))
    minutes_match = re.search(r'(\d+\.?\d*)\s*(m|min)', eta_text.lower())
    if minutes_match:
        minutes = float(minutes_match.group(1))
    return int(hours * 60 + minutes)

def round_to_minute(timestamp):
    """Округляет время до ближайшей минуты."""
    dt = datetime.fromtimestamp(timestamp, UTC)
    dt = dt.replace(second=0, microsecond=0)
    return dt.timestamp()

def format_trade_notification(trade_data):
    """Форматирует уведомление о новой сделке."""
    symbol = trade_data['symbol']
    side = trade_data['side'].capitalize()
    amount_usd = f"${trade_data['amount_usd'] / 1000:.2f}K"
    price = f"${trade_data['price']:.6f}" if trade_data['price'] else "N/A"
    eta_minutes = trade_data['eta_minutes']
    eta_str = f"{eta_minutes // 60}h {eta_minutes % 60}min" if eta_minutes > 0 else "N/A"
    start_time = time.ctime(trade_data['start_time'])
    end_time = time.ctime(trade_data['start_time'] + eta_minutes * 60) if eta_minutes > 0 else "N/A"
    potential_change = f"{trade_data['potential_price_change']:.2f}%" if trade_data['potential_price_change'] else "N/A"
    return (
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

def format_close_notification(trade, reason):
    """Форматирует уведомление о закрытии сделки."""
    symbol = trade['symbol']
    side = trade['side'].capitalize()
    amount_usd = f"${trade['amount_usd'] / 1000:.2f}K"
    price = f"${trade['price']:.6f}" if trade['price'] else "N/A"
    eta_minutes = trade['eta_minutes']
    return (
        f"Сделка закрыта: {symbol} {side} | "
        f"Сумма: {amount_usd} | Цена: {price} | ETA: {eta_minutes} минут | "
        f"Причина: {reason}"
    )

def format_trade_info(trade_id, trade):
    """Форматирует информацию о текущей сделке."""
    symbol = trade['symbol']
    side = trade['side'].capitalize()
    amount_usd = f"${trade['amount_usd'] / 1000:.2f}K"
    price = f"${trade['price']:.6f}" if trade['price'] else "N/A"
    eta_minutes = trade['eta_minutes']
    return f"- ID: {trade_id} | Символ: {symbol} | Тип: {side} | Сумма: {amount_usd} | Цена: {price} | ETA: {eta_minutes} минут"

def format_pending_signal(signal_key, signal):
    """Форматирует информацию о сигнале в pending_signals."""
    symbol = signal['symbol']
    side = signal['side'].capitalize()
    message_id = signal['message_id']
    has_range_x = "(Range: ❌)" if signal.get('has_range_x', False) else ""
    return f"- Key: {signal_key} | ID: {message_id} | Символ: {symbol} | Тип: {side} {has_range_x}"

def get_trading_state():
    """Читает состояние trading из файла."""
    try:
        with open(TRADING_STATE_FILE, 'r') as f:
            state = json.load(f)
            return state.get('trading', True)
    except FileNotFoundError:
        logger.error(f"Файл {TRADING_STATE_FILE} не найден, trading по умолчанию True")
        return True

async def process_message_queue():
    """Обрабатывает очередь сообщений и отправляет уведомления через Telebot."""
    while True:
        try:
            if not message_queue.empty():
                notification = message_queue.get()
                logger.info(f"Отправка уведомления пользователю {USER_ID}: {notification}")
                console_logger.info(f"Отправка уведомления пользователю {USER_ID}: {notification}")
                for attempt in range(3):
                    try:
                        await tb_bot.send_message(USER_ID, notification)
                        logger.info(f"✅ Сообщение отправлено пользователю: {USER_ID}")
                        console_logger.info(f"✅ Сообщение отправлено пользователю: {USER_ID}")
                        break
                    except Exception as e:
                        logger.error(f"❌ Попытка {attempt + 1}/3 не удалась: {e}")
                        console_logger.error(f"❌ Попытка {attempt + 1}/3 не удалась: {e}")
                        if attempt < 2:
                            await asyncio.sleep(2)
                        else:
                            logger.error(f"❌ Не удалось отправить сообщение после 3 попыток")
                            console_logger.error(f"❌ Не удалось отправить сообщение после 3 попыток")
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Ошибка в обработке очереди сообщений: {e}")
            console_logger.error(f"Ошибка в обработке очереди сообщений: {e}")
            await asyncio.sleep(2)

async def add_trade(trade_id, trade_data):
    """Добавляет новую сделку и открывает позицию на Bybit, если trading=True."""
    trading = get_trading_state()
    trade_data['start_time'] = time.time()
    current_trades[trade_id] = trade_data
    trade_info = format_trade_info(trade_id, trade_data)
    logger.info(f"Сделка {trade_id} добавлена: ETA = {trade_data['eta_minutes']} минут")
    console_logger.info(f"Открыта сделка: {trade_info}")

    if trading:
        message_queue.put(format_trade_notification(trade_data))

        # Открытие позиции
        ticker = trade_data['symbol'].upper() + 'USDT'

        # Преобразуем side: SELLING → SELL, BUYING → BUY
        raw_side = trade_data['side'].upper()
        if raw_side == 'SELLING':
            side = 'sell'
        elif raw_side == 'BUYING':
            side = 'buy'
        else:
            side = raw_side

        try:
            stop_loss = get_stop_loss_percent(trade_data['eta_minutes'], raw_side)
            print("Stop_loss for trade: ", stop_loss)
        except ValueError as e:
            logger.error(f"Ошибка получения стоп-лосса: {e}")
            stop_loss = 3

        leverage = 15
        take_profit = trade_data.get('potential_price_change', 5)

        try:
            print(ticker, side, abs(stop_loss))
            trader.run(ticker.upper(), side, abs(stop_loss), leverage, abs(take_profit))
            logger.info(f"Позиция открыта: {ticker}, сторона: {side}, SL: {stop_loss}%")
        except Exception as e:
            logger.error(f"Ошибка при открытии позиции: {e}")
    else:
        logger.info(f"Сделка {trade_id} не открыта на Bybit: trading отключен")
        console_logger.info(f"Сделка {trade_id} не открыта на Bybit: trading отключен")

async def remove_trade(trade_id, reason):
    """Удаляет сделку и закрывает позицию на Bybit."""
    if trade_id in current_trades:
        trade = current_trades[trade_id]
        if trade['status'] == 'pending':
            trade['status'] = 'closed'
            trade['close_time'] = time.time()
            trade['close_reason'] = reason

            # Закрытие позиции
            ticker = trade['symbol'].upper() + 'USDT'
            try:
                success = trader.close_position(ticker)
                if success:
                    logger.info(f"Позиция для {ticker} закрыта")
                else:
                    logger.error(f"Не удалось закрыть позицию для {ticker}")
            except Exception as e:
                logger.error(f"Ошибка при закрытии позиции: {e}")

            with open(ALL_TRADES_FILE, 'a') as f:
                json.dump(trade, f, default=str)
                f.write('\n')
            notification = format_close_notification(trade, reason)
            del current_trades[trade_id]
            logger.info(f"Сделка {trade_id} закрыта: {reason}")
            logger.info(notification)
            console_logger.info(f"Закрыта сделка: {notification}")
            message_queue.put(notification)
        else:
            logger.info(f"Сделка {trade_id} уже закрыта или неактивна")
            console_logger.info(f"Сделка {trade_id} уже закрыта или неактивна")

async def check_trade_timeouts():
    """Проверяет тайм-ауты сделок и закрывает просроченные, выводит оставшееся время каждую минуту."""
    print("Запущена проверка тайм-аутов сделок")
    while True:
        await asyncio.sleep(5)
        current_time = time.time()
        print(f"Проверка тайм-аутов в {datetime.fromtimestamp(current_time).strftime('%Y-%m-%d %H:%M:%S')}")
        if not current_trades:
            logger.info("Нет активных сделок.")
            print("Нет активных сделок.")
            continue
        print(f"Всего активных сделок: {len(current_trades)}")
        to_remove = []
        for trade_id, trade in list(current_trades.items()):
            print(f"Проверка сделки {trade_id}: статус={trade['status']}, eta_minutes={trade['eta_minutes']}")
            if trade['status'] == 'pending' and trade['eta_minutes'] > 0:
                eta = trade['eta_minutes']
                start_time = trade['start_time']
                elapsed = (current_time - start_time) / 60
                remaining = eta - elapsed
                print(f"Сделка {trade_id}: осталось {remaining:.2f} минут")
                if remaining <= 0:
                    print(f"Сделка {trade_id} просрочена, закрываем")
                    await remove_trade(trade_id, reason='timeout')
                    to_remove.append(trade_id)
                else:
                    logger.info(f"Сделка {trade_id}: осталось {remaining:.2f} минут")
                    console_logger.info(f"Сделка {trade_id}: осталось {remaining:.2f} минут")
            else:
                print(f"Сделка {trade_id} пропущена: статус={trade['status']}, eta_minutes={trade['eta_minutes']}")
        for trade_id in to_remove:
            if trade_id in current_trades:
                del current_trades[trade_id]
                print(f"Сделка {trade_id} удалена из current_trades")

def get_all_tickers():
    tickers = set()

    # Тикеры из активных сделок
    for trade in current_trades.values():
        ticker = trade['symbol'].upper() + 'USDT'
        tickers.add(ticker)

    # Тикеры из закрытых сделок
    try:
        with open(ALL_TRADES_FILE, 'r') as f:
            for line in f:
                try:
                    trade = json.loads(line)
                    ticker = trade['symbol'].upper() + 'USDT'
                    tickers.add(ticker)
                except json.JSONDecodeError:
                    logger.error(f"Ошибка при чтении строки в {ALL_TRADES_FILE}")
    except FileNotFoundError:
        logger.error(f"Файл {ALL_TRADES_FILE} не найден")

    return list(tickers)

def is_duplicate(trade1, trade2):
    """Проверяет, являются ли две сделки дубликатами."""
    required_keys = ['symbol', 'side', 'start_time', 'eta_minutes']
    if not all(key in trade1 and key in trade2 for key in required_keys):
        logger.error("Ошибка: не хватает данных для сравнения сделок")
        return False
    if trade1['symbol'].lower() != trade2['symbol'].lower():
        logger.info(f"Символы различаются: {trade1['symbol']} vs {trade2['symbol']}")
        return False
    if trade1['side'].lower() != trade2['side'].lower():
        logger.info(f"Типы различаются: {trade1['side']} vs {trade2['side']}")
        return False
    start1 = round_to_minute(trade1['start_time'])
    start2 = round_to_minute(trade2['start_time'])
    time_diff = abs(start1 - start2)
    if time_diff > 180:
        logger.info(f"Время начала различается: {time_diff} секунд")
        return False
    eta_diff = abs(trade1['eta_minutes'] - trade2['eta_minutes'])
    if eta_diff > 2:
        logger.info(f"ETA различается: {eta_diff} минут")
        return False
    logger.info("Сделки признаны дубликатами")
    return True

def serialize_message(message):
    """Сериализует сообщение в данные сделки или закрытия."""
    text = message.text.lower()
    has_range_x = "range: ❌" in text
    try:
        ticker_match = regex['ticker'].search(text)
        if not ticker_match:
            logger.info(f"Сообщение {message.id} не распознано как новая сделка (нет ticker)")
            return None, False
        amount_str, side, symbol_std, symbol_from, symbol_to = ticker_match.groups()
        if side:
            amount_usd = parse_amount(amount_str)
            symbol = symbol_std
            side = side.lower()
        elif symbol_from and symbol_to:
            amount_usd = parse_amount(amount_str)
            symbol = symbol_to
            side = 'buy'
        else:
            logger.info(f"Сообщение {message.id} не распознано как сделка")
            return None, False
        if has_range_x:
            trade_data = {
                'type': 'trade',
                'message_id': message.id,
                'symbol': symbol,
                'side': side,
                'start_time': time.time(),
                'eta_minutes': 0,
                'status': 'ignored',
                'text': message.text
            }
            logger.info(f"Сообщение {message.id} помечено как игнорируемое из-за 'range: ❌'")
            return trade_data, True
        reply_to_id = message.reply_to_message_id
        if reply_to_id:
            logger.info(f"Распознано сообщение о закрытии с reply_to_message_id: {reply_to_id}")
            symbol_match = re.search(r'\[(\w+)\]', text)
            symbol = symbol_match.group(1) if symbol_match else None
            return {
                'type': 'close',
                'symbol': symbol,
                'message_id': message.id,
                'reply_to_message_id': reply_to_id,
                'text': message.text
            }, False
        price_match = re.search(r'price:\s*\$?([\d.]+)', text.split(symbol)[1] if side == 'buy' and symbol_to else text)
        price = float(price_match.group(1)) if price_match else None
        eta_match = regex['eta'].search(text)
        eta_minutes = parse_eta(eta_match.group(0)) if eta_match else 0
        period_match = regex['period'].search(text)
        if period_match:
            start_time_str, end_time_str = period_match.group(1).split(' ➞ ')
            start_time_str = start_time_str.strip().replace('GMT', 'UTC')
            end_time_str = end_time_str.strip().replace('GMT', 'UTC')
            try:
                start_time_dt = datetime.strptime(start_time_str, '%d %b %Y %H:%M:%S UTC')
                end_time_dt = datetime.strptime(end_time_str, '%d %b %Y %H:%M:%S UTC')
                eta_minutes = int((end_time_dt - start_time_dt).total_seconds() / 60)
            except ValueError as e:
                logger.error(f"Ошибка парсинга периода в сообщении {message.id}: {e}")
        potential_price_change = regex['potential_price_change'].search(
            text.split(symbol)[1] if side == 'buy' and symbol_to else text)
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
            'start_time': time.time(),
            'end_time': None,
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
        return trade_data, False
    except Exception as e:
        logger.error(f"Ошибка при сериализации сообщения {message.id}: {e}")
        return None, False

async def handle_close_message(close_data):
    """Обрабатывает сообщение о закрытии сделки и закрывает позицию."""
    reply_to_id = close_data['reply_to_message_id']
    if reply_to_id in current_trades:
        trade = current_trades[reply_to_id]
        if trade['status'] == 'pending':
            ticker = trade['symbol'].upper() + 'USDT'
            try:
                success = trader.close_position(ticker)
                if success:
                    logger.info(f"Позиция для {ticker} закрыта по ответному сообщению")
                else:
                    logger.error(f"Не удалось закрыть позицию для {ticker}")
            except Exception as e:
                logger.error(f"Ошибка при закрытии позиции: {e}")
            await remove_trade(reply_to_id, reason=f'closed by user (reply to message {close_data["message_id"]})')
        else:
            logger.info(f"Сделка {reply_to_id} уже закрыта (статус: {trade['status']})")
            console_logger.info(f"Сделка {reply_to_id} уже закрыта (статус: {trade['status']})")
    else:
        logger.info(f"Не найдена открытая сделка для reply_to_message_id: {reply_to_id} по сообщению {close_data['message_id']}")

@pyro_client.on_message(filters.chat(SOURCE_CHANNELS) & ~filters.story)
async def new_message_handler(client, message):
    """Обрабатывает новые сообщения из указанных каналов."""
    try:
        if message.text:
            logger.info(f"\nКанал: {message.chat.id}")
            logger.info(f"ID сообщения: {message.id}")
            logger.info(f"Текст: {message.text}")
            reply_to_id = message.reply_to_message_id
            if reply_to_id and reply_to_id in current_trades:
                logger.info(f"Сообщение {message.id} является ответом на сигнал {reply_to_id}, закрываем сделку")
                close_data = {
                    'symbol': current_trades[reply_to_id]['symbol'],
                    'message_id': message.id,
                    'reply_to_message_id': reply_to_id,
                    'text': message.text
                }
                await handle_close_message(close_data)
                return
            data, has_range_x = serialize_message(message)
            if data:
                if data['type'] == 'close':
                    await handle_close_message(data)
                elif data['type'] == 'trade':
                    rounded_start_time = round_to_minute(data['start_time'])
                    signal_key = f"{data['symbol'].lower()}_{data['side'].lower()}_{rounded_start_time}"
                    existing_trade_id = None
                    for trade_id, trade in current_trades.items():
                        if (trade['symbol'].lower() == data['symbol'].lower() and
                            trade['side'].lower() == data['side'].lower() and
                            trade['status'] == 'pending'):
                            existing_trade_id = trade_id
                            break
                    if existing_trade_id:
                        existing_trade = current_trades[existing_trade_id]
                        current_time = time.time()
                        elapsed_minutes = (current_time - existing_trade['start_time']) / 60
                        remaining_minutes = max(0, existing_trade['eta_minutes'] - elapsed_minutes)
                        new_eta_minutes = round(remaining_minutes + data['eta_minutes'])
                        existing_trade['eta_minutes'] = new_eta_minutes
                        logger.info(f"Сигнал {message.id} для {data['symbol']} ({data['side']}) продлевает сделку {existing_trade_id}: новый ETA = {new_eta_minutes} минут")
                        console_logger.info(f"Продлена сделка {existing_trade_id}: {data['symbol']} ({data['side']}), новый ETA = {new_eta_minutes} минут")
                        if get_trading_state():
                            message_queue.put(f"⏳ Сделка продлена: {data['symbol']} {data['side'].capitalize()} | Новый ETA: {new_eta_minutes} минут")
                        return
                    is_duplicate_in_trades = any(is_duplicate(data, trade) for trade in current_trades.values())
                    if is_duplicate_in_trades:
                        logger.info(f"Сигнал {message.id} является дубликатом существующей сделки, игнорируем")
                        return
                    if signal_key in pending_signals:
                        prev_signal = pending_signals[signal_key]
                        if is_duplicate(data, prev_signal):
                            logger.info(f"Сигнал {message.id} является дубликатом сигнала {prev_signal['message_id']}, игнорируем")
                            return
                    data['has_range_x'] = has_range_x
                    data['message_id'] = message.id
                    pending_signals[signal_key] = data
                    logger.info(f"Сигнал {message.id} добавлен в pending_signals")
                    #await asyncio.sleep(1) ожидание для сделки
                    if signal_key in pending_signals and pending_signals[signal_key]['message_id'] == message.id:
                        if not has_range_x:
                            logger.info(f"Сигнал {message.id} не имеет дубликатов, добавляем в current_trades")
                            await add_trade(message.id, data)
                        else:
                            logger.info(f"Сигнал {message.id} с Range: ❌, удаляем из pending_signals")
                        del pending_signals[signal_key]
            if pending_signals:
                logger.info("Текущие pending signals:")
                for signal_key, signal in pending_signals.items():
                    logger.info(format_pending_signal(signal_key, signal))
            else:
                logger.info("Pending signals: отсутствуют")
            if current_trades:
                logger.info("Текущие сделки:")
                for trade_id, trade in current_trades.items():
                    logger.info(format_trade_info(trade_id, trade))
            else:
                logger.info("Текущие сделки: отсутствуют")
            logger.info('-' * 50)
    except PeerIdInvalid as e:
        logger.error(f"Ошибка PeerIdInvalid при обработке сообщения {message.id}: {e}")
        logger.info("Пропускаем сообщение из-за неизвестного peer.")

async def main():
    """Запускает основной цикл программы."""
    logger.info("🚀 Запуск Telebot...")
    console_logger.info("🚀 Запуск Telebot...")
    logger.info("🔍 Запуск парсера Pyrogram...")
    console_logger.info("🔍 Запуск парсера Pyrogram...")
    asyncio.create_task(check_trade_timeouts())
    asyncio.create_task(process_message_queue())
    await pyro_client.start()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Программа остановлена пользователем")
        console_logger.info("Программа остановлена пользователем")
    except Exception as e:
        logger.error(f"Необработанная ошибка: {e}")
        console_logger.error(f"Необработанная ошибка: {e}")
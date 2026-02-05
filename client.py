import requests
import asyncio
import time
import hmac
import hashlib
import json

# --- КОНФИГУРАЦИЯ GATE.IO ---
GATE_KEY = '12c585b179b7e7cff1616a9e49420d9d'
GATE_SECRET = '58e365922c5ec8b10a4910f43308cec0e49bb7418bcba37bf85d7352f219eeb5'
GATE_HOST = "https://api.gateio.ws"
GATE_PREFIX = "/api/v4"

# ==========================================
# ЛОГИКА GATE.IO (API V4)
# ==========================================

def gate_v4_auth(method, endpoint, query_string='', body=''):
    t = str(int(time.time()))
    body_hash = hashlib.sha512(body.encode('utf-8')).hexdigest()
    string_to_sign = f"{method}\n{endpoint}\n{query_string}\n{body_hash}\n{t}"
    sign = hmac.new(GATE_SECRET.encode('utf-8'), string_to_sign.encode('utf-8'), hashlib.sha512).hexdigest()
    return {
        "KEY": GATE_KEY,
        "Timestamp": t,
        "SIGN": sign,
        "Content-Type": "application/json"
    }

def gate_set_leverage(symbol, leverage):
    endpoint = f"{GATE_PREFIX}/futures/usdt/positions/{symbol}/set_leverage"
    query = f"leverage={leverage}&margin_mode=cross"
    headers = gate_v4_auth("POST", endpoint, query, "")
    try:
        requests.post(f"{GATE_HOST}{endpoint}?{query}", headers=headers)
    except Exception as e:
        print(f"Ошибка установки плеча: {e}")

def gate_get_contract_meta(symbol):
    endpoint = f"{GATE_PREFIX}/futures/usdt/contracts/{symbol}"
    try:
        r = requests.get(f"{GATE_HOST}{endpoint}")
        data = r.json()
        if 'name' in data:
            p_str = str(data.get('order_price_round', '0.01'))
            precision = len(p_str.split('.')[-1]) if '.' in p_str else 0
            return {"multiplier": float(data.get('quanto_multiplier', 1.0)), "precision": precision}
    except:
        return None
    return None

def gate_get_bbo_price(symbol):
    endpoint = f"{GATE_PREFIX}/futures/usdt/tickers"
    try:
        r = requests.get(f"{GATE_HOST}{endpoint}?contract={symbol}")
        data = r.json()
        return data[0]['highest_bid']
    except:
        return None

def gate_get_order_status(order_id):
    endpoint = f"{GATE_PREFIX}/futures/usdt/orders/{order_id}"
    headers = gate_v4_auth("GET", endpoint)
    r = requests.get(GATE_HOST + endpoint, headers=headers)
    return r.json()

def gate_place_order(symbol, size, price):
    endpoint = f"{GATE_PREFIX}/futures/usdt/orders"
    body = json.dumps({
        "contract": symbol,
        "size": -int(size), 
        "price": price,
        "tif": "gtc",
        "text": "t-entry"
    })
    headers = gate_v4_auth("POST", endpoint, "", body)
    return requests.post(GATE_HOST + endpoint, headers=headers, data=body).json()

def gate_place_stop_loss(symbol, size, trigger_price):
    endpoint = f"{GATE_PREFIX}/futures/usdt/price_orders"
    body = json.dumps({
        "initial": {
            "contract": symbol,
            "size": int(size),
            "price": "0",
            "tif": "ioc"
        },
        "trigger": {
            "strategy_type": 0,
            "price_type": 0,
            "price": str(trigger_price),
            "rule": 1
        }
    })
    headers = gate_v4_auth("POST", endpoint, "", body)
    return requests.post(GATE_HOST + endpoint, headers=headers, data=body).json()

async def trade_execution(coin_ticker, settings):
    """
    settings: словарь с текущими параметрами (leverage, margin, stop_loss)
    """
    symbol = f"{coin_ticker}_USDT"
    print(f"\n[TRADE] Обработка {symbol}...")
    
    try:
        meta = gate_get_contract_meta(symbol)
        if not meta:
            print(f"❌ Контракт {symbol} не найден.")
            return

        gate_set_leverage(symbol, settings['leverage'])
        
        price_raw = gate_get_bbo_price(symbol)
        if not price_raw: return
        price = float(price_raw)
        
        size = int((settings['margin'] * settings['leverage']) / (price * meta['multiplier']))
        
        if size <= 0:
            print(f"❌ Слишком маленький размер для {symbol}")
            return

        order = gate_place_order(symbol, size, str(price))
        
        if 'id' in order:
            order_id = order['id']
            print(f"✅ Ордер на вход выставлен (GTC). ID: {order_id}. Ожидание...")
            
            is_filled = False
            for _ in range(12): # Ждем максимум 2 минуты (12 * 10 сек)
                status_res = gate_get_order_status(order_id)
                if status_res and status_res.get('status') == 'finished':
                    if status_res.get('finish_as') == 'filled':
                        print(f"✅ Ордер {order_id} исполнен.")
                        is_filled = True
                    break
                await asyncio.sleep(10)

            if is_filled:
                sl_price = round(price * (1 + settings['stop_loss'] / 100), meta['precision'])
                sl_order = gate_place_stop_loss(symbol, size, sl_price)
                if 'id' in sl_order:
                    print(f"✅ Стоп-Лосс выставлен на {sl_price}")
                else:
                    print(f"⚠️ Ошибка стопа: {sl_order}")
        else:
            print(f"❌ Ошибка входа: {order}")
            
    except Exception as e:
        print(f"❌ Ошибка в торговой логике: {e}")
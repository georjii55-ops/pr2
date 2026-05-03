from flask import Flask, render_template, jsonify, request, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import requests
import json
import hashlib
import sqlite3
import os
import time
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-change-this-in-production-12345'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///cryptodb.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

db = SQLAlchemy(app)

# ============ CoinMarketCap API Configuration ============
CMC_API_KEY = "32b765f8e66b43e0857c596af9732b21"  # Замените на ваш ключ

CMC_API_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
CMC_HEADERS = {
    'X-CMC_PRO_API_KEY': CMC_API_KEY,
    'Accept': 'application/json'
}

# Кэш для API запросов (чтобы не делать слишком много запросов)
api_cache = {}
last_request_time = 0
MIN_REQUEST_INTERVAL = 2  # Минимум 2 секунды между запросами

# ============ МОК-ДАННЫЕ (на случай ошибки API) ============
MOCK_CRYPTO_DATA = [
    {'id': '1', 'symbol': 'btc', 'name': 'Bitcoin',
     'image': 'https://s2.coinmarketcap.com/static/img/coins/64x64/1.png',
     'current_price': 65000, 'market_cap': 1250000000000, 'total_volume': 35000000000,
     'price_change_percentage_24h': 2.5, 'price_change_percentage_7d': 5.2, 'rank': 1},
    {'id': '2', 'symbol': 'eth', 'name': 'Ethereum',
     'image': 'https://s2.coinmarketcap.com/static/img/coins/64x64/1027.png',
     'current_price': 3200, 'market_cap': 385000000000, 'total_volume': 18000000000,
     'price_change_percentage_24h': 1.8, 'price_change_percentage_7d': 3.5, 'rank': 2},
    {'id': '3', 'symbol': 'sol', 'name': 'Solana',
     'image': 'https://s2.coinmarketcap.com/static/img/coins/64x64/5426.png',
     'current_price': 150, 'market_cap': 65000000000, 'total_volume': 3500000000,
     'price_change_percentage_24h': -1.2, 'price_change_percentage_7d': -2.8, 'rank': 5},
    {'id': '4', 'symbol': 'bnb', 'name': 'BNB', 'image': 'https://s2.coinmarketcap.com/static/img/coins/64x64/1839.png',
     'current_price': 580, 'market_cap': 89000000000, 'total_volume': 2500000000,
     'price_change_percentage_24h': 0.5, 'price_change_percentage_7d': 1.2, 'rank': 4},
    {'id': '5', 'symbol': 'xrp', 'name': 'XRP', 'image': 'https://s2.coinmarketcap.com/static/img/coins/64x64/52.png',
     'current_price': 0.62, 'market_cap': 34000000000, 'total_volume': 1200000000,
     'price_change_percentage_24h': -0.8, 'price_change_percentage_7d': -1.5, 'rank': 6},
]

MOCK_GLOBAL_DATA = {
    'total_market_cap': {'usd': 2500000000000},
    'total_volume': {'usd': 85000000000},
    'market_cap_percentage': {'btc': 48.5},
    'active_cryptocurrencies': 13100,
    'active_exchanges': 850
}


# Функция для хеширования пароля
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password, hash):
    return hash_password(password) == hash


# Функция для проверки и добавления колонки balance
def add_column_if_not_exists():
    db_path = 'cryptodb.db'
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("PRAGMA table_info(user)")
        columns = [column[1] for column in cursor.fetchall()]

        if 'balance' not in columns:
            print("Adding balance column to user table...")
            cursor.execute("ALTER TABLE user ADD COLUMN balance FLOAT DEFAULT 10000.0")
            conn.commit()
            print("Balance column added successfully!")

        conn.close()


# Модели базы данных
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='user')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)
    theme = db.Column(db.String(20), default='dark')
    balance = db.Column(db.Float, default=10000.0)

    def set_password(self, password):
        self.password_hash = hash_password(password)

    def check_password(self, password):
        return verify_password(password, self.password_hash)

    def is_admin(self):
        return self.role == 'admin'

    @classmethod
    def get_by_id(cls, user_id):
        """Современная альтернатива Query.get()"""
        return cls.query.filter_by(id=user_id).first()


class UserPortfolio(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    coin_id = db.Column(db.String(50))
    coin_name = db.Column(db.String(100))
    coin_symbol = db.Column(db.String(20))
    amount = db.Column(db.Float)
    purchase_price = db.Column(db.Float)
    purchase_date = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref=db.backref('portfolio', lazy=True))


class PriceHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    coin_id = db.Column(db.String(50))
    price_usd = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    coin_id = db.Column(db.String(50))
    coin_name = db.Column(db.String(100))
    amount = db.Column(db.Float)
    price = db.Column(db.Float)
    transaction_type = db.Column(db.String(10))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref=db.backref('transactions', lazy=True))


# Создаем таблицы и добавляем колонку если нужно
with app.app_context():
    add_column_if_not_exists()
    db.create_all()

    # Исправляем пользователей без баланса
    users_without_balance = User.query.filter(User.balance.is_(None)).all()
    for user in users_without_balance:
        user.balance = 10000.0
    if users_without_balance:
        db.session.commit()
        print(f"Updated balance for {len(users_without_balance)} users")

    if not User.query.filter_by(username='demo').first():
        demo_user = User(username='demo', email='demo@crypto.com', role='user', balance=10000.0)
        demo_user.set_password('demo123')
        db.session.add(demo_user)
        db.session.commit()
        print(" Demo user created: username='demo', password='demo123'")

    if not User.query.filter_by(username='admin').first():
        admin_user = User(username='admin', email='admin@crypto.com', role='admin', balance=100000.0)
        admin_user.set_password('admin123')
        db.session.add(admin_user)
        db.session.commit()
        print(" Admin user created: username='admin', password='admin123'")


# ============ CoinMarketCap API Functions with Fallback ============

def make_api_request(url, params=None, max_retries=3):
    """Выполняет API запрос с повторными попытками и кэшированием"""
    global last_request_time

    # Проверяем кэш
    cache_key = f"{url}_{json.dumps(params, sort_keys=True) if params else ''}"
    if cache_key in api_cache:
        cache_time, cache_data = api_cache[cache_key]
        if time.time() - cache_time < 60:  # Кэш на 60 секунд
            return cache_data

    # Ограничиваем частоту запросов
    time_since_last = time.time() - last_request_time
    if time_since_last < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - time_since_last)

    for attempt in range(max_retries):
        try:
            last_request_time = time.time()
            response = requests.get(url, headers=CMC_HEADERS, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            if data.get('status', {}).get('error_code') == 0:
                # Сохраняем в кэш
                api_cache[cache_key] = (time.time(), data)
                return data
            else:
                error_msg = data.get('status', {}).get('error_message', '')
                if "busy" in error_msg.lower() or "try again" in error_msg.lower():
                    print(f"API busy, retry {attempt + 1}/{max_retries}...")
                    time.sleep(2 ** attempt)  # Экспоненциальная задержка
                    continue
                else:
                    print(f"API Error: {error_msg}")
                    return None

        except requests.exceptions.RequestException as e:
            print(f"Request error (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                return None

    return None


def get_crypto_data(currency='USD', limit=100):
    """Получение данных с CoinMarketCap API с fallback на мок-данные"""

    # Если API ключ не настроен
    if CMC_API_KEY == "YOUR_API_KEY_HERE":
        print(" API key not configured, using mock data")
        return generate_mock_data(limit)

    params = {
        'start': 1,
        'limit': min(limit, 100),
        'convert': currency.upper(),
        'sort': 'market_cap',
        'sort_dir': 'desc'
    }

    try:
        data = make_api_request(f"{CMC_API_URL}/v1/cryptocurrency/listings/latest", params)

        if not data or data.get('status', {}).get('error_code') != 0:
            print("Failed to get data from API, using mock data")
            return generate_mock_data(limit)

        listings = data.get('data', [])
        if not listings:
            return generate_mock_data(limit)

        # Получаем метаданные
        coin_ids = [str(coin['id']) for coin in listings[:50]]
        info_data = {}

        if coin_ids:
            info_params = {'id': ','.join(coin_ids)}
            info_response = make_api_request(f"{CMC_API_URL}/v1/cryptocurrency/info", info_params)
            if info_response and info_response.get('status', {}).get('error_code') == 0:
                info_data = info_response.get('data', {})

        # Преобразуем данные
        formatted_data = []
        quote_currency = currency.upper()

        for idx, coin in enumerate(listings[:limit]):
            quote = coin.get('quote', {}).get(quote_currency, {})
            coin_info = info_data.get(str(coin['id']), {})

            formatted_coin = {
                'id': str(coin['id']),
                'symbol': coin['symbol'].lower(),
                'name': coin['name'],
                'image': coin_info.get('logo', f"https://s2.coinmarketcap.com/static/img/coins/64x64/{coin['id']}.png"),
                'current_price': quote.get('price', 0),
                'market_cap': quote.get('market_cap', 0),
                'total_volume': quote.get('volume_24h', 0),
                'price_change_percentage_24h': quote.get('percent_change_24h', 0),
                'price_change_percentage_7d': quote.get('percent_change_7d', 0),
                'circulating_supply': coin.get('circulating_supply', 0),
                'rank': coin.get('cmc_rank', idx + 1),
            }
            formatted_data.append(formatted_coin)

        return formatted_data if formatted_data else generate_mock_data(limit)

    except Exception as e:
        print(f"Error in get_crypto_data: {e}")
        return generate_mock_data(limit)


def generate_mock_data(limit=100):
    """Генерирует реалистичные мок-данные для криптовалют"""
    mock_data = []
    base_coins = [
        ('1', 'btc', 'Bitcoin', 65000, 2.5),
        ('2', 'eth', 'Ethereum', 3200, 1.8),
        ('3', 'sol', 'Solana', 150, -1.2),
        ('4', 'bnb', 'BNB', 580, 0.5),
        ('5', 'xrp', 'XRP', 0.62, -0.8),
        ('6', 'ada', 'Cardano', 0.45, -2.1),
        ('7', 'doge', 'Dogecoin', 0.12, 5.2),
        ('8', 'dot', 'Polkadot', 7.50, -1.5),
        ('9', 'matic', 'Polygon', 0.85, 4.2),
        ('10', 'shib', 'Shiba Inu', 0.000023, 3.8),
    ]

    for i, (coin_id, symbol, name, price, change) in enumerate(base_coins[:limit]):
        mock_data.append({
            'id': coin_id,
            'symbol': symbol,
            'name': name,
            'image': f"https://s2.coinmarketcap.com/static/img/coins/64x64/{coin_id}.png",
            'current_price': price,
            'market_cap': price * 1000000000,
            'total_volume': price * 100000000,
            'price_change_percentage_24h': change,
            'price_change_percentage_7d': change * 1.5,
            'rank': i + 1,
        })

    return mock_data


def get_global_data():
    """Получение глобальных метрик с fallback"""
    try:
        if CMC_API_KEY == "YOUR_API_KEY_HERE":
            return MOCK_GLOBAL_DATA

        data = make_api_request(f"{CMC_API_URL}/v1/global-metrics/quotes/latest", {'convert': 'USD'})

        if not data or data.get('status', {}).get('error_code') != 0:
            return MOCK_GLOBAL_DATA

        global_data = data.get('data', {})
        quote = global_data.get('quote', {}).get('USD', {})

        return {
            'total_market_cap': {'usd': quote.get('total_market_cap', 0)},
            'total_volume': {'usd': quote.get('total_volume_24h', 0)},
            'market_cap_percentage': {'btc': global_data.get('btc_dominance', 0)},
            'active_cryptocurrencies': global_data.get('active_cryptocurrencies', 0),
            'active_exchanges': global_data.get('active_exchanges', 0)
        }
    except Exception as e:
        print(f"Error in get_global_data: {e}")
        return MOCK_GLOBAL_DATA


def get_trending_coins():
    """Получение трендовых монет"""
    try:
        data = get_crypto_data('USD', 10)
        trending = []
        for idx, coin in enumerate(data[:5]):
            trending.append({
                'item': {
                    'id': coin['id'],
                    'name': coin['name'],
                    'symbol': coin['symbol'],
                    'small': coin['image'],
                    'score': idx + 1
                }
            })
        return trending
    except Exception as e:
        print(f"Error getting trending coins: {e}")
        return []


# ============ Flask Routes ============

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)

    return decorated_function


# Маршруты аутентификации
@app.route("/api/register", methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')

    if not username or not email or not password:
        return jsonify({'error': 'All fields are required'}), 400

    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username already exists'}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'Email already registered'}), 400

    user = User(username=username, email=email, role='user', balance=10000.0)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    return jsonify({'success': True, 'message': 'Registration successful!'}), 201


@app.route("/api/login", methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')

    user = User.query.filter_by(username=username).first()

    if not user or not user.check_password(password):
        return jsonify({'error': 'Invalid username or password'}), 401

    if not user.is_active:
        return jsonify({'error': 'Account is disabled'}), 403

    session['user_id'] = user.id
    session['username'] = user.username
    session['role'] = user.role
    session.permanent = True

    user.last_login = datetime.utcnow()
    db.session.commit()

    return jsonify({
        'success': True,
        'username': user.username,
        'role': user.role,
        'balance': user.balance if user.balance else 10000.0
    })


@app.route("/api/logout", methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})


@app.route("/api/check-auth")
def check_auth():
    if 'user_id' in session:
        user = User.get_by_id(session['user_id'])  # Используем новый метод
        return jsonify({
            'authenticated': True,
            'username': session['username'],
            'role': session.get('role', 'user'),
            'balance': user.balance if user and user.balance else 10000.0
        })
    return jsonify({'authenticated': False})


@app.route("/api/user-profile")
@login_required
def get_user_profile():
    user = User.get_by_id(session['user_id'])
    return jsonify({
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'role': user.role,
        'balance': user.balance if user.balance else 10000.0,
        'created_at': user.created_at.isoformat(),
        'last_login': user.last_login.isoformat() if user.last_login else None
    })


@app.route("/api/change-password", methods=['POST'])
@login_required
def change_password():
    data = request.json
    old_password = data.get('old_password')
    new_password = data.get('new_password')

    user = User.get_by_id(session['user_id'])

    if not user.check_password(old_password):
        return jsonify({'error': 'Current password is incorrect'}), 401

    if len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400

    user.set_password(new_password)
    db.session.commit()

    return jsonify({'success': True, 'message': 'Password changed successfully'})


# API маршруты для криптовалют
@app.route("/api/crypto-data")
def api_crypto_data():
    currency = request.args.get('currency', 'usd')
    per_page = request.args.get('per_page', 100, type=int)
    cmc_currency = currency.upper()
    data = get_crypto_data(cmc_currency, per_page)
    return jsonify(data)


@app.route("/api/global-data")
def api_global_data():
    return jsonify(get_global_data())


@app.route("/api/trending")
def api_trending():
    return jsonify(get_trending_coins())


# Маршруты для портфеля
@app.route("/api/portfolio", methods=['GET'])
@login_required
def get_portfolio():
    user_id = session['user_id']
    portfolio = UserPortfolio.query.filter_by(user_id=user_id).all()
    return jsonify([{
        'id': p.id,
        'coin_id': p.coin_id,
        'coin_name': p.coin_name,
        'coin_symbol': p.coin_symbol,
        'amount': p.amount,
        'purchase_price': p.purchase_price,
        'purchase_date': p.purchase_date.isoformat()
    } for p in portfolio])


@app.route("/api/portfolio/add", methods=['POST'])
@login_required
def add_to_portfolio():
    data = request.json
    user_id = session['user_id']
    user = User.get_by_id(user_id)

    coin_id = data.get('coin_id')
    coin_name = data.get('coin_name')
    coin_symbol = data.get('coin_symbol')
    amount = float(data.get('amount'))
    purchase_price = float(data.get('price'))
    total_cost = amount * purchase_price

    current_balance = user.balance if user.balance else 10000.0
    if current_balance < total_cost:
        return jsonify({'error': f'Insufficient balance. You have ${current_balance:.2f}'}), 400

    portfolio_item = UserPortfolio(
        user_id=user_id,
        coin_id=coin_id,
        coin_name=coin_name,
        coin_symbol=coin_symbol,
        amount=amount,
        purchase_price=purchase_price
    )
    db.session.add(portfolio_item)

    user.balance = current_balance - total_cost

    transaction = Transaction(
        user_id=user_id,
        coin_id=coin_id,
        coin_name=coin_name,
        amount=amount,
        price=purchase_price,
        transaction_type='buy'
    )
    db.session.add(transaction)
    db.session.commit()

    return jsonify({
        'success': True,
        'message': f'Successfully purchased {amount} {coin_symbol.upper()}!',
        'new_balance': user.balance
    })


@app.route("/api/portfolio/sell", methods=['POST'])
@login_required
def sell_from_portfolio():
    data = request.json
    user_id = session['user_id']
    user = User.get_by_id(user_id)
    portfolio_id = data.get('portfolio_id')
    sell_amount = float(data.get('amount'))
    current_price = float(data.get('current_price'))

    portfolio_item = UserPortfolio.query.filter_by(id=portfolio_id, user_id=user_id).first()

    if not portfolio_item:
        return jsonify({'error': 'Portfolio item not found'}), 404

    if sell_amount > portfolio_item.amount:
        return jsonify({'error': 'Not enough coins to sell'}), 400

    total_value = sell_amount * current_price

    if sell_amount == portfolio_item.amount:
        db.session.delete(portfolio_item)
    else:
        portfolio_item.amount -= sell_amount

    current_balance = user.balance if user.balance else 10000.0
    user.balance = current_balance + total_value

    transaction = Transaction(
        user_id=user_id,
        coin_id=portfolio_item.coin_id,
        coin_name=portfolio_item.coin_name,
        amount=sell_amount,
        price=current_price,
        transaction_type='sell'
    )
    db.session.add(transaction)
    db.session.commit()

    return jsonify({
        'success': True,
        'message': f'Successfully sold {sell_amount} {portfolio_item.coin_symbol.upper()}!',
        'new_balance': user.balance
    })


@app.route("/api/transactions")
@login_required
def get_transactions():
    user_id = session['user_id']
    transactions = Transaction.query.filter_by(user_id=user_id).order_by(Transaction.timestamp.desc()).limit(50).all()
    return jsonify([{
        'id': t.id,
        'coin_name': t.coin_name,
        'amount': t.amount,
        'price': t.price,
        'type': t.transaction_type,
        'timestamp': t.timestamp.isoformat(),
        'total': t.amount * t.price
    } for t in transactions])


@app.route("/api/settings/theme", methods=['POST'])
@login_required
def update_theme():
    data = request.json
    theme = data.get('theme')
    user = User.get_by_id(session['user_id'])
    user.theme = theme
    db.session.commit()
    return jsonify({'success': True})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/check-api-key")
def check_api_key():
    """Проверка статуса API ключа"""
    return jsonify({
        'valid': CMC_API_KEY != "YOUR_API_KEY_HERE",
        'message': 'API key configured' if CMC_API_KEY != "YOUR_API_KEY_HERE" else 'Please set your API key',
        'using_mock_data': CMC_API_KEY == "YOUR_API_KEY_HERE"
    })


@app.route("/api/coin-history/<coin_id>")
def get_coin_history(coin_id):
    days = request.args.get('days', 7)
    response = requests.get(
        f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
        params={"vs_currency": "usd", "days": days}
    )
    return jsonify(response.json())

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print(" CryptoDashboard Pro with CoinMarketCap API")
    print("=" * 60)

    if CMC_API_KEY == "YOUR_API_KEY_HERE":
        print("\n  Используются ДЕМО-ДАННЫЕ (API ключ не настроен)")
        print("\n Для получения реальных данных:")
        print("   1. Перейдите на https://coinmarketcap.com/api/")
        print("   2. Зарегистрируйтесь и получите API ключ")
        print("   3. Замените 'YOUR_API_KEY_HERE' в файле app.py")
        print("\n С демо-данными приложение работает полностью!")
    else:
        print(f"\n API ключ настроен: {CMC_API_KEY[:8]}...")

    print("\n Сервер запущен: http://127.0.0.1:5000")
    print(" Демо аккаунт: demo / demo123")
    print(" Админ аккаунт: admin / admin123")
    print("=" * 60 + "\n")

    app.run(debug=True)
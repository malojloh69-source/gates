"""Public, read-only market catalog compiled from the supplied archive."""
import gzip
import io
import json
import os
import re
import sqlite3
import time
import zipfile
from contextlib import closing
from decimal import Decimal, ROUND_HALF_UP, localcontext
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

from flask import Blueprint, Response, jsonify, request

import config
import database as db
from market_archive import MultipartArchive
from verification import THRESHOLDS

market = Blueprint('market', __name__)
BASE = Path(__file__).resolve().parent
CATALOG = BASE / 'static' / 'market-catalog.sqlite3'
PACKAGED_MEDIA = BASE / 'market-media.zip'
SPLIT_MEDIA = BASE / 'media' / 'market-media.json'
ARCHIVE = os.getenv('MARKET_MEDIA_ARCHIVE') or (str(PACKAGED_MEDIA) if PACKAGED_MEDIA.is_file() else str(SPLIT_MEDIA) if SPLIT_MEDIA.is_file() else None)
PAGE_SIZE = 24
RATES = {currency: Decimal(value) for currency, value in THRESHOLDS.items()}
PRECISION = {'STARS': 0, 'TON': 8, 'USDT': 6, 'BTC': 8}
SORT = {
    'archive': 'position',
    'low': 'ton_value ASC, position',
    'high': 'ton_value DESC, position',
}
TON_VALUE = 'CAST(source_price AS REAL) * (CASE source_currency ' + ' '.join(
    f"WHEN '{currency}' THEN :rate_{currency}" for currency in RATES) + ' END)'
OFFER_ROWS = '''(SELECT g.gift_id AS id,g.name,g.number,g.image_id,s.currency,s.id AS offer_id,
    printf('%d.%08d',s.amount / 100000000,s.amount % 100000000) AS source_price,s.currency AS source_currency,
    -s.id AS position,COALESCE(m.image_kind,0) AS image_kind,COALESCE(m.model,'') AS model,
    COALESCE(m.backdrop,'') AS backdrop,COALESCE(m.symbol,'') AS symbol,
    COALESCE(m.search,lower(g.name) || ' #' || g.number) AS search
    FROM accounts.market_resales s JOIN accounts.market_gifts g ON g.user_id=s.seller_id AND g.gift_id=s.gift_id
    LEFT JOIN main.listing m ON m.id=g.gift_id WHERE s.status='active')'''
FILTER_FIELDS = {'collection': 'name', 'model': 'model', 'backdrop': 'backdrop', 'symbol': 'symbol'}
MEDIA_MEMBER = re.compile(r'dist/(?:media/[a-zA-Z0-9_.-]+|gifts/originals/[0-9]+/Original\.png)')


def catalog():
    connection = sqlite3.connect(CATALOG.as_uri() + '?mode=ro&immutable=1', uri=True)
    connection.row_factory = sqlite3.Row
    return closing(connection)


def equivalent(price, source_currency, currency):
    with localcontext() as context:
        context.prec = 50
        value = Decimal(price)
        if source_currency != currency:
            value = value * RATES[currency] / RATES[source_currency]
        step = Decimal(1).scaleb(-PRECISION.get(currency, 2))
        return format(value.quantize(step, rounding=ROUND_HALF_UP), 'f')


def purchase(user_id, ids):
    with catalog() as connection:
        rows = connection.execute(f'SELECT * FROM listing WHERE id IN ({",".join("?" for _ in ids)})', ids).fetchall()
    if len(rows) != len(ids):
        raise ValueError('Подарок не найден')
    totals, gifts = {}, []
    created = int(time.time())
    for row in rows:
        currency = row['currency']
        charged = int(Decimal(equivalent(row['source_price'], row['source_currency'], currency)) * db.SCALE)
        if charged <= 0 or charged > config.MAX_AMOUNT * db.SCALE:
            raise ValueError('Покупка сейчас недоступна')
        totals[currency] = totals.get(currency, 0) + charged
        gifts.append((user_id,row['id'],row['name'],row['number'],row['image_id'],currency,charged,created))
    if any(total > config.MAX_AMOUNT * db.SCALE for total in totals.values()):
        raise ValueError('Недостаточно средств на балансе')
    with db.tx() as connection:
        connection.executemany('INSERT INTO market_gifts VALUES (?,?,?,?,?,?,?,?)', gifts)
        for currency, total in totals.items():
            db.move(connection, user_id, currency, -total)
            db.log(connection, user_id, 'market_purchase', currency, total)


def put_offer(seller, gift_id, currency, amount, previous=None):
    with db.tx() as connection:
        if not connection.execute('SELECT 1 FROM market_gifts WHERE user_id=? AND gift_id=?', (seller,gift_id)).fetchone():
            raise LookupError('Подарок не найден')
        current = connection.execute("SELECT id FROM market_resales WHERE seller_id=? AND gift_id=? AND status='active'", (seller,gift_id)).fetchone()
        if (current['id'] if current else None) != previous:
            raise LookupError('Лот уже изменился. Обновите Маркет.')
        if current:
            connection.execute("UPDATE market_resales SET status='cancelled' WHERE id=?", (previous,))
        return connection.execute("INSERT INTO market_resales(seller_id,gift_id,currency,amount,created,status) VALUES (?,?,?,?,?,'active')",
                                  (seller,gift_id,currency,amount,int(time.time()))).lastrowid


def cancel_offer(seller, offer_id):
    with db.tx() as connection:
        changed = connection.execute("UPDATE market_resales SET status='cancelled' WHERE id=? AND seller_id=? AND status='active'", (offer_id,seller))
        if not changed.rowcount:
            raise LookupError('Лот уже изменился. Обновите Маркет.')


def buy_offer(buyer, offer_id):
    with db.tx() as connection:
        row = connection.execute('''SELECT s.*,g.name,g.number,g.image_id FROM market_resales s JOIN market_gifts g
            ON g.user_id=s.seller_id AND g.gift_id=s.gift_id WHERE s.id=? AND s.status='active' ''', (offer_id,)).fetchone()
        if not row:
            raise LookupError('Лот уже изменился. Обновите Маркет.')
        if row['seller_id'] == buyer:
            raise ValueError('Это ваш лот')
        currency, amount, gift_id = row['currency'], row['amount'], row['gift_id']
        connection.execute('INSERT INTO market_gifts VALUES (?,?,?,?,?,?,?,?)',
                           (buyer,gift_id,row['name'],row['number'],row['image_id'],currency,amount,int(time.time())))
        db.move(connection,buyer,currency,-amount)
        db.move(connection,row['seller_id'],currency,amount)
        connection.execute('DELETE FROM market_gifts WHERE user_id=? AND gift_id=?', (row['seller_id'],gift_id))
        connection.execute("UPDATE market_resales SET status='sold',buyer_id=? WHERE id=?", (buyer,offer_id))
        db.log(connection,buyer,'market_purchase',currency,amount,ref=str(offer_id))
        db.log(connection,row['seller_id'],'market_sale',currency,amount,ref=str(offer_id))


def listing(row):
    item = {key: row[key] for key in ('id', 'name', 'number', 'currency', 'image_id', 'image_kind')}
    item['price'] = equivalent(row['source_price'], row['source_currency'], row['currency'])
    item['animated'] = bool(ARCHIVE and row['motion'])
    if 'offer_id' in row.keys():
        item['offer_id'] = row['offer_id']
    return item


def animated_gifts(items):
    images = {item['id']: item['image_id'] for item in items if item['image_id']}
    if not (ARCHIVE and images and CATALOG.is_file()):
        return set()
    with catalog() as connection:
        rows = connection.execute(
            """SELECT l.id, l.image_id FROM listing l JOIN image i ON i.id=l.image_id
               WHERE l.id IN (SELECT value FROM json_each(?)) AND i.motion<>''""",
            (json.dumps(list(images)),),
        )
        return {row['id'] for row in rows if row['image_id'] == images[row['id']]}


@market.before_request
def check_catalog():
    if not CATALOG.is_file():
        return jsonify(error='Каталог временно недоступен'), 503


@market.get('/api/market/listings')
def listings():
    params = {'query': request.args.get('q', '').casefold().strip()[:120],
              'currency': request.args.get('currency', ''),
              **{f'rate_{currency}': float(RATES['TON'] / rate) for currency,rate in RATES.items()}}
    offers = request.args.get('source') == 'offers'
    table = OFFER_ROWS if offers else 'main.listing'
    if params['currency'] not in config.CURRENCIES:
        params['currency'] = ''
    page = min(3000, max(1, request.args.get('page', 1, type=int)))
    order = SORT.get(request.args.get('sort'), SORT['archive'])
    conditions = []
    if params['currency']:
        conditions.append('currency = :currency')
    if params['query']:
        conditions.append('instr(search, :query) > 0')
    if 'ids' in request.args:
        ids = [value for value in request.args['ids'].split(',')[:250] if re.fullmatch(r'-?[0-9]{1,18}', value)]
        if ids:
            links = {f'id_{index}': int(value) for index, value in enumerate(ids)}
            params.update(links)
            conditions.append('listing.id IN (' + ','.join(':'+key for key in links) + ')')
        else:
            conditions.append('0')
    for key, field in FILTER_FIELDS.items():
        value = request.args.get(key, '').strip()[:160]
        if value:
            params[key] = value
            if key == 'symbol':
                conditions.append("(symbol = :symbol OR instr(symbol, :symbol || ' (') = 1)")
            else:
                conditions.append(f'{field} = :{key}')
    for key, operator in (('min', '>='), ('max', '<=')):
        bound = request.args.get(key, type=float)
        if bound is not None and 0 <= bound <= 1_000_000_000:
            params[key] = bound
            conditions.append(f'{TON_VALUE} {operator} :{key}')
    where = 'WHERE ' + ' AND '.join(conditions) if conditions else ''
    with catalog() as connection:
        if offers:
            connection.execute('ATTACH DATABASE ? AS accounts', (Path(config.DB_PATH).resolve().as_uri() + '?mode=ro',))
        count = connection.execute(f'SELECT COUNT(*) FROM {table} AS listing {where}', params).fetchone()[0]
        pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        rows = connection.execute(f'''
            SELECT listing.*, COALESCE(image.motion, '') AS motion, {TON_VALUE} AS ton_value
            FROM {table} AS listing LEFT JOIN image ON image.id = listing.image_id
            {where} ORDER BY {order} LIMIT :size OFFSET :offset
        ''', {**params, 'size': PAGE_SIZE, 'offset': (page - 1) * PAGE_SIZE}).fetchall()
    return jsonify(items=[listing(row) for row in rows], total=count, page=page, pages=pages)


@market.get('/api/market/options')
def options():
    collection = request.args.get('collection', '')[:160]
    result = {}
    with catalog() as connection:
        for key, field in FILTER_FIELDS.items():
            clauses, values = f'{field} <> ?', ['']
            if collection and key != 'collection':
                clauses += ' AND name = ?'
                values.append(collection)
            if key == 'model' and not collection:
                result[key] = []
                continue
            result[key] = [row[0] for row in connection.execute(f'SELECT DISTINCT {field} FROM listing WHERE {clauses} ORDER BY 1 COLLATE NOCASE', values)]
    return jsonify(result)


@market.get('/api/market/previews/<kind>')
def previews(kind):
    field = FILTER_FIELDS.get(kind)
    if kind not in ('collection', 'model', 'backdrop', 'symbol'):
        return jsonify(error='Не найдено'), 404
    collection = '' if kind == 'collection' else request.args.get('collection', '')[:160]
    if kind == 'model' and not collection:
        return jsonify([])
    with catalog() as connection:
        rows = connection.execute(f'''
            WITH ranked AS (
                SELECT l.{field} AS value, i.id AS image_id, i.motion,
                    l.image_kind, a.image_id AS model_art,
                    ROW_NUMBER() OVER (PARTITION BY l.{field}
                        ORDER BY l.image_kind DESC, (i.motion <> '') DESC, l.position) AS rank
                FROM listing l
                LEFT JOIN model_art a ON :kind = 'model' AND a.collection = l.name AND a.model = l.model
                LEFT JOIN image i ON i.id = COALESCE(a.image_id, l.image_id)
                WHERE l.{field} <> '' AND (:collection = '' OR l.name = :collection)
            )
            SELECT * FROM ranked WHERE rank = 1 ORDER BY value COLLATE NOCASE
        ''', {'collection': collection, 'kind': kind}).fetchall()
        palettes = {}
        if kind == 'backdrop':
            palettes = {row['name']: [row['center'], row['edge'], row['pattern']]
                        for row in connection.execute('SELECT * FROM backdrop_palette')}

    result = {}
    for row in rows:
        value = row['value']
        base = re.sub(r'\s*\([\d.]+%\)$', '', value)
        name = base.casefold()
        if kind == 'symbol':
            value = base
        fallback = kind == 'model' and row['image_kind'] < 2 and not row['model_art']
        key = re.sub(r'[^a-z0-9]', '', name)
        symbol = key if kind == 'symbol' and key in symbol_art() else None
        image_id = None if kind == 'symbol' and row['image_kind'] < 3 else row['image_id']
        item = {'value': value, 'image_id': image_id, 'palette': palettes.get(name), 'symbol': symbol,
                'animated': bool(ARCHIVE and row['motion'] and not fallback and kind in ('model', 'collection')),
                'fallback': fallback}
        if value not in result or (image_id and not result[value]['image_id']):
            result[value] = item
    return jsonify(list(result.values()))


@market.get('/api/market/listings/<int(signed=True):gift_id>')
def gift(gift_id):
    with catalog() as connection:
        row = connection.execute("SELECT listing.*, COALESCE(image.motion, '') AS motion FROM listing LEFT JOIN image ON image.id=listing.image_id WHERE listing.id=?",
                                 (str(gift_id),)).fetchone()
    if row is None:
        return jsonify(error='Не найдено'), 404
    item = listing(row)
    item.update({key: row[key] for key in ('model', 'backdrop', 'symbol', 'source_price', 'source_currency')})
    rate_params = {f'rate_{currency}': float(RATES['TON'] / rate) for currency, rate in RATES.items()}
    item['trait_prices'] = {}
    with catalog() as connection:
        for field in ('model', 'backdrop', 'symbol'):
            if not row[field]:
                continue
            conditions = f'{field}=:trait' + (' AND name=:collection' if field == 'model' else '')
            value = connection.execute(f'SELECT MIN({TON_VALUE}) FROM listing WHERE {conditions}',
                                       {**rate_params, 'trait': row[field], 'collection': row['name']}).fetchone()[0]
            if value is not None:
                item['trait_prices'][field] = equivalent(str(value), 'TON', item['currency'])
        average = connection.execute(f'SELECT AVG({TON_VALUE}) FROM listing WHERE name=:collection',
                                     {**rate_params, 'collection': row['name']}).fetchone()[0]
        if average is not None:
            item['average_price'] = equivalent(str(average), 'TON', item['currency'])
    return jsonify(item)


@lru_cache(maxsize=1)
def media_archive(path):
    return zipfile.ZipFile(MultipartArchive(path) if str(path).endswith('.json') else path)


def archive_member(path, limit):
    if not ARCHIVE or not MEDIA_MEMBER.fullmatch(path):
        return None
    try:
        archive = media_archive(ARCHIVE)
        member = archive.getinfo(path)
        if member.file_size <= limit:
            return archive.read(member)
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


@lru_cache(maxsize=1)
def symbol_art():
    if not ARCHIVE:
        return {}
    try:
        archive = media_archive(ARCHIVE)
        references = json.loads(archive.read('dist/media-map.json'))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return {}
    result = {}
    for url, path in references.items():
        match = re.fullmatch(r'https://raw\.githubusercontent\.com/Nxrix/telegram-gifts/refs/heads/main/data/symbols/webp/128/([a-zA-Z0-9_%.-]+)\.webp', url)
        if match and MEDIA_MEMBER.fullmatch('dist/' + path) and 'dist/' + path in archive.NameToInfo:
            key = re.sub(r'[^a-z0-9]', '', unquote(match[1]).casefold())
            result[key] = 'dist/' + path
    return result


@market.get('/static/market-symbol/<key>.webp')
def symbol_image(key):
    content = archive_member(symbol_art().get(key, ''), 500_000)
    if content and content[:4] == b'RIFF' and content[8:12] == b'WEBP':
        return Response(content, mimetype='image/webp')
    return jsonify(error='Не найдено'), 404


@market.get('/static/market-art/<int(signed=True):image_id>')
def image(image_id):
    with catalog() as connection:
        row = connection.execute('SELECT content,original FROM image WHERE id=?', (str(image_id),)).fetchone()
    if row is None:
        return jsonify(error='Не найдено'), 404
    content = archive_member(row['original'], 1_000_000)
    if content and content.startswith(b'\xff\xd8\xff'):
        return Response(content, mimetype='image/jpeg')
    if content and content.startswith(b'\x89PNG\r\n\x1a\n'):
        return Response(content, mimetype='image/png')
    return Response(row['content'], mimetype='image/webp')


@lru_cache(maxsize=32)
def animation(path):
    content = archive_member(path, 1_500_000)
    if not content:
        return None
    try:
        if path.endswith('.tgs'):
            with gzip.GzipFile(fileobj=io.BytesIO(content)) as compressed:
                content = compressed.read(1_500_001)
            if len(content) > 1_500_000:
                return None
        data = json.loads(content)
    except (ValueError, RecursionError, OSError, EOFError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get('layers'), list):
        return None
    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if isinstance(item.get('x'), str):
                del item['x']
            if item.get('ty') == 4 and not isinstance(item.get('shapes'), list):
                item['shapes'] = []
            if item.get('ty') == 'gr' and not isinstance(item.get('it'), list):
                item['it'] = []
            if item.get('p') and isinstance(item['p'], str) and 'u' in item:
                if not item['p'].startswith(('data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/webp;base64,')):
                    return None
                item['u'] = ''
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return json.dumps(data, separators=(',', ':')).encode('utf-8')


@market.get('/static/market-motion/<int(signed=True):image_id>.json')
def motion(image_id):
    with catalog() as connection:
        row = connection.execute('SELECT motion FROM image WHERE id=?', (str(image_id),)).fetchone()
    data = animation(row['motion']) if row and row['motion'] else None
    if not data:
        return jsonify(error='Не найдено'), 404
    response = Response(data, mimetype='application/json')
    response.vary.add('Accept-Encoding')
    if request.accept_encodings['gzip'] > 0:
        response.set_data(gzip.compress(data, compresslevel=3))
        response.headers['Content-Encoding'] = 'gzip'
    return response

"""Build the local gift catalog from the data and images in a catalog ZIP."""
import argparse
import io
import json
import re
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageOps

import config

CATALOG = Path(__file__).resolve().parent / 'static' / 'market-catalog.sqlite3'
CURRENCIES = ('STARS', 'TON', *config.FIAT_CURRENCIES, 'USDT', 'BTC')
MEDIA_URL = re.compile(r'https://nft\.fragment\.com/gift/([^/]+)-(\d+)\.(small\.jpg|lottie\.json)')
MEDIA_PATH = re.compile(r'media/[a-zA-Z0-9_.-]+')


def slug(name):
    return re.sub(r'[^a-z0-9]', '', name.lower())


def trait_name(value):
    return re.sub(r'\s*\([\d.]+%\)$', '', value).casefold()


def thumbnail(archive, filename):
    try:
        data = archive.read(filename)
        with Image.open(io.BytesIO(data)) as image:
            if max(image.size) > 2048:
                return None
            mode = 'RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB'
            square = ImageOps.fit(image.convert(mode), (160, 160), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            square.save(output, format='WEBP', quality=74, method=2)
            return output.getvalue()
    except (KeyError, OSError):
        return None


def build(zip_path, destination):
    with zipfile.ZipFile(zip_path) as archive, closing(sqlite3.connect(destination)) as connection:
        source = json.loads(archive.read('catalog/listings.json'), parse_float=Decimal)
        media = json.loads(archive.read('dist/media-map.json'))
        originals = {slug(item['name']): item['id'] for item in json.loads(archive.read('catalog/original-gifts.json'))}
        names = set(archive.namelist())
        image_files, motion_files, model_files = {}, {}, {}
        for url, path in media.items():
            if not MEDIA_PATH.fullmatch(path) or ('dist/' + path) not in names:
                continue
            match = MEDIA_URL.fullmatch(url)
            if match:
                targets = image_files if match[3] == 'small.jpg' else motion_files
                targets[slug(match[1]), int(match[2])] = 'dist/' + path
            elif url.startswith('https://gifts.coffin.meme/'):
                collection, _, filename = urlsplit(url).path.strip('/').partition('/')
                model, _, extension = unquote(filename).rpartition('.')
                if extension in ('png', 'tgs'):
                    key = trait_name(unquote(collection)), trait_name(model)
                    model_files.setdefault(key, {})[extension] = 'dist/' + path

        connection.executescript('''
            CREATE TABLE listing (
                id INTEGER PRIMARY KEY, position INTEGER NOT NULL, name TEXT NOT NULL, number INTEGER NOT NULL,
                model TEXT NOT NULL, backdrop TEXT NOT NULL, symbol TEXT NOT NULL, search TEXT NOT NULL,
                source_price TEXT NOT NULL, source_currency TEXT NOT NULL, currency TEXT NOT NULL,
                image_id INTEGER, image_kind INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE image (id INTEGER PRIMARY KEY, content BLOB NOT NULL, original TEXT NOT NULL, motion TEXT NOT NULL);
            CREATE TABLE model_art (collection TEXT NOT NULL, model TEXT NOT NULL, image_id INTEGER NOT NULL,
                PRIMARY KEY(collection, model));
            CREATE TABLE backdrop_palette (name TEXT PRIMARY KEY, center TEXT NOT NULL, edge TEXT NOT NULL, pattern TEXT NOT NULL);
        ''')
        rows, exact_images, model_images, collection_images = [], {}, {}, {}
        for item in source:
            asset = item.get('asset')
            price = item.get('price')
            if item.get('status') != 'forsale' or asset not in ('TON', 'USDT') or not isinstance(price, (int, Decimal)) or price <= 0:
                continue
            gift_id, name, number = item['gift_id'], item['name'], item['gift_num']
            model, backdrop, symbol = (item.get(key) or '' for key in ('model', 'backdrop', 'symbol'))
            key = slug(name), number
            if key in image_files and key not in exact_images:
                art = thumbnail(archive, image_files[key])
                if art:
                    connection.execute('INSERT INTO image VALUES (?,?,?,?)', (gift_id, art, image_files[key], motion_files.get(key, '')))
                    exact_images[key] = gift_id
                    model_images.setdefault((name, model), gift_id)
                    collection_images.setdefault(name, gift_id)
            rows.append((gift_id, len(rows), name, number, model, backdrop, symbol,
                         f'{name} #{number} {model} {backdrop} {symbol}'.casefold(), format(Decimal(price), 'f'), asset))

        for gift_id, _, name, *_ in rows:
            if name in collection_images:
                continue
            original_id = originals.get(slug(name), '')
            if not re.fullmatch(r'[0-9]+', original_id):
                continue
            original = f'dist/gifts/originals/{original_id}/Original.png'
            art = thumbnail(archive, original)
            if art:
                connection.execute('INSERT INTO image VALUES (?,?,?,?)', (gift_id, art, original, ''))
                collection_images[name] = gift_id

        image_id = max(row[0] for row in rows)
        all_models = {(row[2], row[4]) for row in rows}
        for collection, model in sorted(all_models):
            art_files = model_files.get((trait_name(collection), trait_name(model)), {})
            if 'png' not in art_files:
                continue
            art = thumbnail(archive, art_files['png'])
            if art:
                image_id += 1
                connection.execute('INSERT INTO image VALUES (?,?,?,?)', (image_id, art, art_files['png'], art_files.get('tgs', '')))
                connection.execute('INSERT INTO model_art VALUES (?,?,?)', (collection, model, image_id))

        for backdrop in json.loads(archive.read('catalog/backdrops.json')):
            palette = backdrop.get('color') or {}
            name = backdrop.get('backdrop') or palette.get('name')
            colors = [palette.get(key) for key in ('centerColor', 'edgeColor', 'patternColor')]
            if name and all(isinstance(color, int) and 0 <= color <= 0xffffff for color in colors):
                connection.execute('INSERT OR IGNORE INTO backdrop_palette VALUES (?,?,?,?)',
                                   (trait_name(name), *(f'#{color:06x}' for color in colors)))

        def image_reference(row):
            _, _, name, number, model, *_ = row
            if image_id := exact_images.get((slug(name), number)):
                return image_id, 3
            if image_id := model_images.get((name, model)):
                return image_id, 2
            return collection_images.get(name), 1 if name in collection_images else 0

        with_images = [(row, image_reference(row)) for row in rows]
        with_images.sort(key=lambda entry: -entry[1][1])
        priced = []
        count_ton = 0
        for row, reference in with_images:
            currency = row[-1]
            if currency == 'TON':
                currency = CURRENCIES[count_ton % len(CURRENCIES)]
                count_ton += 1
            priced.append(row + (currency,) + reference)
        connection.executemany('INSERT INTO listing VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', priced)
        connection.executescript('''
            CREATE INDEX listing_position ON listing (image_kind DESC, position);
            CREATE INDEX listing_name ON listing (name);
            CREATE INDEX listing_currency ON listing (currency);
        ''')
        connection.commit()
        print(f'{len(rows)} listings, {len(exact_images)} exact images, {count_ton} source prices in TON')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    with tempfile.NamedTemporaryFile(dir=CATALOG.parent, prefix='market-build-', suffix='.sqlite3', delete=False) as target:
        compiled = Path(target.name)
    try:
        build(args.archive, compiled)
        compiled.replace(CATALOG)
    finally:
        compiled.unlink(missing_ok=True)


if __name__ == '__main__':
    main()

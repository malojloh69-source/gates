"""Catalogue coverage and imported gift regressions; no real purchases."""
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

temporary = tempfile.TemporaryDirectory()
os.environ['DEV_MODE'] = '1'
os.environ['PAYMENT_MODE'] = 'sandbox'
os.environ['DB_PATH'] = str(Path(temporary.name) / 'catalog-test.db')
from main import app
import config
import database as db
import market


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.enterContext(patch.object(config, 'PAYMENT_MODE', 'sandbox'))

    def test_all_gifts_are_reachable_by_pagination(self):
        first = self.client.get('/api/market/listings').get_json()
        self.assertEqual(first['total'], 45362)
        self.assertEqual(first['pages'], 1891)
        last = self.client.get('/api/market/listings?page=1891').get_json()
        self.assertEqual(len(last['items']), 2)
        self.assertTrue(any(item['id'] < 0 for item in last['items']))

    def test_imported_negative_id_has_details_and_image(self):
        response = self.client.get('/api/market/listings/-999502916')
        self.assertEqual(response.status_code, 200)
        item = response.get_json()
        self.assertEqual(item['name'], 'Hypno Lollipop')
        self.assertTrue(item['model'])
        self.assertIn('model', item['trait_prices'])
        self.assertIn('average_price', item)
        image = self.client.get('/static/market-art/' + str(item['image_id']))
        self.assertEqual(image.status_code, 200)
        self.assertIn(image.mimetype, ('image/png', 'image/jpeg', 'image/webp'))

    def test_catalog_negative_id_passes_purchase_validation(self):
        db.grant_admin(1)
        with patch.object(market, 'purchase') as purchase:
            response = self.client.post('/api/market/buy', json={'ids': [-999502916]}, headers={'X-Dev-User': '1'})
            self.assertEqual(response.status_code, 200, response.get_json())
            purchase.assert_called_once_with(1, [-999502916])
        response = self.client.post('/api/market/buy', json={'ids': [0]}, headers={'X-Dev-User': '1'})
        self.assertEqual(response.status_code, 400)

    def test_catalog_gift_sells_once_and_can_be_resold(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(config, 'DB_PATH', str(Path(directory) / 'market.db')):
            db.init()
            gift_id = -999502916
            listing_url = f'/api/market/listings?ids={gift_id}'
            gift = self.client.get(listing_url).get_json()['items'][0]
            currency = gift['currency']
            start = 1_000_000 * db.SCALE
            with db.tx() as connection:
                for user in (1, 2):
                    db.move(connection, user, currency, start)

            def buy(user):
                with app.test_client() as client:
                    result = client.post('/api/market/buy', json={'ids': [gift_id]}, headers={'X-Dev-User': str(user)})
                    return result.status_code, user

            with ThreadPoolExecutor(max_workers=2) as pool:
                (success, buyer), (conflict, other) = sorted(pool.map(buy, (1, 2)))
            self.assertEqual((success, conflict), (200, 409))
            buyer_headers = {'X-Dev-User': str(buyer)}
            self.assertEqual(self.client.get(listing_url).get_json()['total'], 0)
            self.assertEqual(self.client.get(f'/api/market/listings/{gift_id}').status_code, 404)
            self.assertEqual([item['id'] for item in self.client.get('/api/market/mine', headers=buyer_headers).get_json()], [gift_id])
            self.assertEqual(db.row('SELECT amount FROM balances WHERE user_id=? AND currency=?', (other, currency))['amount'], start)

            offer = self.client.put(f'/api/market/mine/{gift_id}/offer', json={'currency': currency, 'price': gift['price']}, headers=buyer_headers)
            self.assertEqual(offer.status_code, 200)
            self.assertEqual(self.client.get(listing_url + '&source=offers').get_json()['total'], 1)
            response = self.client.post(f'/api/market/offers/{offer.get_json()["id"]}/buy', headers={'X-Dev-User': str(other)})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.client.get(listing_url + '&source=offers').get_json()['total'], 0)
            self.assertEqual(self.client.get(listing_url).get_json()['total'], 0)

    def test_original_loot_bag_is_available(self):
        response = self.client.get('/static/assets/animations/loot-bag.json')
        self.assertEqual(response.status_code, 200)
        animation = json.loads(response.data)
        response.close()
        self.assertEqual(animation['nm'], 'ton')
        self.assertEqual(animation['w'], 512)
        self.assertTrue(animation['layers'])

    def test_motion_is_served_with_optional_gzip(self):
        import gzip
        item = self.client.get('/api/market/listings/10571146').get_json()
        self.assertTrue(item['animated'])
        url = '/static/market-motion/' + str(item['image_id']) + '.json'
        raw = self.client.get(url)
        compressed = self.client.get(url, headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(compressed.headers['Content-Encoding'], 'gzip')
        self.assertEqual(gzip.decompress(compressed.data), raw.data)
        data = json.loads(raw.data)
        self.assertGreater(data['op'], data['ip'])
        self.assertTrue(data['layers'])

    def test_originally_static_flag_does_not_hide_available_motion(self):
        with market.catalog() as connection:
            row = connection.execute("SELECT l.id FROM listing l JOIN image i ON i.id=l.image_id WHERE l.image_kind=1 AND i.motion<>'' LIMIT 1").fetchone()
        item = self.client.get('/api/market/listings/' + str(row['id'])).get_json()
        self.assertEqual(item['image_kind'], 1)
        self.assertTrue(item['animated'])
        self.assertEqual(self.client.get('/static/market-motion/' + str(item['image_id']) + '.json').status_code, 200)


if __name__ == '__main__':
    unittest.main()

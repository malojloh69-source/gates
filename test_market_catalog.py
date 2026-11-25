"""Catalogue coverage and imported gift regressions; no real purchases."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

temporary = tempfile.TemporaryDirectory()
os.environ['DEV_MODE'] = '1'
os.environ['PAYMENT_MODE'] = 'sandbox'
os.environ['DB_PATH'] = str(Path(temporary.name) / 'catalog-test.db')
from main import app
import database as db
import market


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

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

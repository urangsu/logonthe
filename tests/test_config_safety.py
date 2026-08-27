import json
import tempfile
import unittest
from pathlib import Path
from services.config import ConfigService, ConfigConflictError


class ConfigSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'config.json'

    def test_load_has_no_write_and_preserves_legacy_keys(self):
        original = b'{"max_pages": 3, "legacy_custom": "keep"}'
        self.path.write_bytes(original)
        config = ConfigService(str(self.path))
        self.assertEqual(config.get('max_feed_items'), 30)
        self.assertEqual(self.path.read_bytes(), original)
        config.save({'my_blog_id': 'owner'})
        self.assertEqual(json.loads(self.path.read_text())['legacy_custom'], 'keep')
        backups = list((self.path.parent / 'config_backups').glob('*.bak'))
        self.assertEqual(backups[0].read_bytes(), original)

    def test_conflicting_process_never_overwrites(self):
        self.path.write_text('{"schema_version":2,"my_blog_id":"first"}')
        first, second = ConfigService(str(self.path)), ConfigService(str(self.path))
        first.save({'my_blog_id': 'updated'})
        with self.assertRaises(ConfigConflictError):
            second.save({'my_blog_id': 'lost update'})
        self.assertEqual(json.loads(self.path.read_text())['my_blog_id'], 'updated')

    def test_corrupt_config_is_not_reset(self):
        self.path.write_text('{broken')
        with self.assertRaises(ValueError):
            ConfigService(str(self.path))
        self.assertEqual(self.path.read_text(), '{broken')

    def test_new_config_is_not_created_by_read(self):
        config = ConfigService(str(self.path))
        self.assertFalse(self.path.exists())
        self.assertTrue(config.get('assistant_mode'))
        self.assertFalse(config.get('gemini_web_enabled'))

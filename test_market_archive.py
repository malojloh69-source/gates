import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from market_archive import MultipartArchive


class ArchiveTests(unittest.TestCase):
    def test_zip_reads_across_parts_and_seeks(self):
        stream = io.BytesIO()
        payload = bytes(range(256)) * 20
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('animation.json', payload)
        content = stream.getvalue()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parts = []
            for start in range(0, len(content), 47):
                name = f'part-{start}.bin'
                value = content[start:start+47]
                (root/name).write_bytes(value)
                parts.append({'name': name, 'size': len(value)})
            manifest = root/'manifest.json'
            manifest.write_text(json.dumps({'size':len(content),'parts':parts}))
            with MultipartArchive(manifest) as split:
                self.assertEqual(split.read(53),content[:53])
                split.seek(-51,io.SEEK_END)
                self.assertEqual(split.read(),content[-51:])
                split.seek(100000)
                self.assertEqual(split.read(),b'')
                with self.assertRaises(ValueError):split.seek(-1)
                with zipfile.ZipFile(split) as archive:
                    self.assertEqual(archive.read('animation.json'),payload)

    def test_invalid_manifest_cannot_read_outside_its_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            manifest=root/'manifest.json'
            manifest.write_text(json.dumps({'size':1,'parts':[{'name':'../secret','size':1}]}))
            with self.assertRaises(ValueError):MultipartArchive(manifest)


if __name__=='__main__':unittest.main()

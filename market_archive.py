"""Seekable media ZIP stored in small Git-friendly parts, without extraction."""
import bisect
import io
import json
from pathlib import Path


class MultipartArchive(io.RawIOBase):
    def __init__(self, manifest):
        super().__init__()
        root = Path(manifest).resolve().parent
        metadata = json.loads(Path(manifest).read_text(encoding='utf-8'))
        self.parts, self.ends, self.position = [], [], 0
        total = 0
        for part in metadata['parts']:
            path = (root / part['name']).resolve()
            size = part['size']
            if path.parent != root or not isinstance(size, int) or size <= 0 or path.stat().st_size != size:
                raise ValueError('Invalid market media part')
            self.parts.append(path)
            total += size
            self.ends.append(total)
        if not self.parts or total != metadata['size']:
            raise ValueError('Incomplete market media archive')
        self.size = total

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        self._checkClosed()
        if whence == io.SEEK_CUR:
            offset += self.position
        elif whence == io.SEEK_END:
            offset += self.size
        elif whence != io.SEEK_SET:
            raise ValueError('Invalid seek mode')
        if offset < 0:
            raise ValueError('Negative seek position')
        self.position = offset
        return offset

    def read(self, size=-1):
        self._checkClosed()
        remaining = max(0, self.size - self.position)
        if size >= 0:
            remaining = min(remaining, size)
        output = []
        while remaining:
            part = bisect.bisect_right(self.ends, self.position)
            start = self.ends[part - 1] if part else 0
            length = min(remaining, self.ends[part] - self.position)
            with self.parts[part].open('rb') as stream:
                stream.seek(self.position - start)
                content = stream.read(length)
            if len(content) != length:
                raise OSError('Truncated market media part')
            output.append(content)
            self.position += length
            remaining -= length
        return b''.join(output)

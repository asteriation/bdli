from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
import struct
import zlib

hashes = {}

@dataclass
class LBLILabel:
    hash: int
    name: Optional[str] = None

    @classmethod
    def from_hash(cls, hash_: int) -> LBLILabel:
        if hash_ in hashes:
            return LBLILabel(hash=hash_, name=hashes[hash_])
        return LBLILabel(hash=hash_)

    def __str__(self):
        return self.name if self.name else f'_{self.hash:08X}'

@dataclass
class LBLIChar:
    value: int
    volume: int
    pitch: float

    SIZE = 0x14

    @classmethod
    def load(cls, blob: bytes, offset: int = 0) -> LBLIChar:
        assert len(blob) >= offset + cls.SIZE
        value, volume, x08, pitch, b10 = struct.unpack_from('<IIIfB', blob, offset)

        # these are actually bools loaded as u32/u8 but never used
        assert x08 == 0
        assert b10 == 0

        return LBLIChar(value=value, volume=volume, pitch=pitch)

    def dump(self) -> bytes:
        return struct.pack('<IIIfI', self.value, self.volume, 0, self.pitch, 0)

    def get_char(self) -> str:
        if 0x01 <= self.value <= 0x56:
            return chr(0x3040 + self.value)
        elif 0x57 <= self.value <= 0x60:
            return chr(0x30 + self.value - 0x57)
        elif 0x61 <= self.value <= 0x7a:
            return chr(self.value)
        LOOKUP = {
            0x9d: chr(0x30fc),
            0x9e: chr(0x2026),
            0x9f: '[Monology]',
            0xa0: chr(0xff1f),
            0xa1: chr(0xff01),
            0xa2: chr(0x3001),
            0xa3: chr(0x3002),
            0xa4: chr(0x3000),
            0xa5: chr(0x30fb),
            0xa6: '\n',
            # 0xa7: '[NP]\n',
            0xa7: '\n\n',
            0xda: '',
            0xde: '',
            0x10d: '',
            # 0xda: '[string]',
            # 0xde: '[delay]',
            # 0x10d: '[stop]',
        }
        return LOOKUP.get(self.value, f'[{self.value:03X}]')

@dataclass
class LBLI:
    label: LBLILabel
    chars: List[LBLIChar]

    MAGIC = b'LBLI'
    SIZE = 0x10

    @classmethod
    def load(cls, blob: bytes, offset: int = 0) -> LBLI:
        assert len(blob) >= offset + cls.SIZE
        magic = blob[offset : offset + 4]
        char_count, char_offset, label_hash = struct.unpack_from('<III', blob, offset + 4)

        assert magic == cls.MAGIC

        label = LBLILabel.from_hash(label_hash)
        chars: List[LBLIChar] = []
        for i in range(char_count):
            chars.append(LBLIChar.load(blob, offset + char_offset + LBLIChar.SIZE * i))
        return LBLI(label=label, chars=chars)

    def dump(self, chars_offset: int) -> bytes:
        return LBLI.MAGIC + struct.pack('<III', len(self.chars), chars_offset, self.label.hash)

@dataclass
class BDLI:
    lbli: List[LBLI]

    MAGIC = b'BDLI'
    SIZE = 0x14

    @classmethod
    def load(cls, blob: bytes, offset: int = 0) -> BDLI:
        assert len(blob) >= offset + cls.SIZE
        magic = blob[offset : offset + 4]
        version, lbli_count, x8, lbli_offset, char_offset = struct.unpack_from('<HHIII', blob, offset + 4)

        assert magic == cls.MAGIC
        assert version == 2
        assert x8 == 0 # unused

        lbli: List[LBLI] = []
        for i in range(lbli_count):
            lbli.append(LBLI.load(blob, offset + lbli_offset + LBLI.SIZE * i))

        return BDLI(lbli=lbli)

    def dump(self) -> bytes:
        num_lbli = len(self.lbli)
        lbli_offset = BDLI.SIZE
        char_offset = lbli_offset + num_lbli * LBLI.SIZE

        header = [BDLI.MAGIC, struct.pack('<HHIII', 2, num_lbli, 0, lbli_offset, char_offset)]
        lblis = []
        chars = []

        for lbli in self.lbli:
            lblis.append(lbli.dump(char_offset - lbli_offset))
            for char in lbli.chars:
                chars.append(char.dump())

            lbli_offset += LBLI.SIZE
            char_offset += len(lbli.chars) * LBLIChar.SIZE

        return b''.join(header + lblis + chars)

if __name__ == '__main__':
    import sys, os, zstandard
    import json, codecs
    import re

    # python3 bdli.py file.msbt.json file.bdli[.zs]
   
    j = json.load(codecs.open(sys.argv[1], 'r', 'utf-8-sig'))
    data = {}
    for x in j:
        h = zlib.crc32(x['label'].encode('utf-8')) & 0xffffffff
        hashes[h] = x['label']
        data[x['label']] = { 'text': re.sub(r'\{\{[^\}]+\}\}', r'', x['text'].replace('{{pageBreak}}', '\n\n')) }
   
    for x in data.keys():
        h = zlib.crc32(x.encode('utf-8')) & 0xffffffff
        hashes[h] = x

    n = sys.argv[2]
    with open(n, 'rb') as f:
        b = f.read()
        name = os.path.basename(n)[:-5]
        if n.endswith('.zs'):
            name = name[:-3]
            b = zstandard.ZstdDecompressor().decompress(b)
        bdli = BDLI.load(b)
        for lbli in sorted(bdli.lbli, key=lambda x: str(x.label)):
            s = ''
            vol = 100
            pitch = 1.0
            for c in lbli.chars:
                if c.volume != vol:
                    if vol != 100:
                        s += '{/volume}'
                    if c.volume != 100:
                        s += '{volume=' + str(c.volume) + '}'
                    vol = c.volume
                if c.pitch != pitch and c.pitch != 0:
                    s += f'[{c.pitch}]'
                    if pitch < c.pitch:
                        s += '↑'
                    if pitch > c.pitch:
                        s += '↓'
                pitch = c.pitch
                s += c.get_char()

            if lbli.label.name in data:
                data[lbli.label.name]['bdli_text'] = s
                print(data[lbli.label.name]['text'])
            else:
                print('missing label in msbt', hex(lbli.hash), file=sys.stderr)
            print(s)
            print()
    # print(json.dumps(data))

"""QR codes as inline SVG (standard library only, rendered on the server).

A compact QR Code Model 2 encoder written for this project from ISO/IEC 18004:
byte mode (UTF-8), error correction level M, versions 1-40, automatic mask
choice. It covers what the console needs (URLs, auth keys, short commands).

The SVG is black on white with the 4-module quiet zone included, so it scans
the same in the light and dark themes. Pages work without JavaScript, and the
Content-Security-Policy is untouched (no scripts, no inline styles).
"""

from __future__ import annotations

from i18n import _
from ui import esc

# Level M, indexed by version (1-40): EC codewords per block, number of blocks.
_ECC_PER_BLOCK = (10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
                  26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28)
_NUM_BLOCKS = (1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
               17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49)
_FORMAT_M = 0  # the 2 error-correction bits of level M in the format information


def _raw_modules(ver: int) -> int:
    """Modules left for data and EC codewords once function patterns are placed."""
    n = (16 * ver + 128) * ver + 64
    if ver >= 2:
        align = ver // 7 + 2
        n -= (25 * align - 10) * align - 55
        if ver >= 7:
            n -= 36
    return n


def _data_codewords(ver: int) -> int:
    return _raw_modules(ver) // 8 - _ECC_PER_BLOCK[ver - 1] * _NUM_BLOCKS[ver - 1]


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _i in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 2)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _codewords(data: bytes) -> tuple[int, list[int]]:
    """Pick the smallest version and return it with the final codeword sequence."""
    for ver in range(1, 41):
        count_bits = 8 if ver <= 9 else 16
        capacity = _data_codewords(ver) * 8
        if 4 + count_bits + len(data) * 8 <= capacity:
            break
    else:
        raise ValueError("text too long for a QR code")

    bits: list[int] = []

    def put(value: int, n: int) -> None:
        bits.extend((value >> i) & 1 for i in reversed(range(n)))

    put(0b0100, 4)  # byte mode
    put(len(data), count_bits)
    for b in data:
        put(b, 8)
    put(0, min(4, capacity - len(bits)))  # terminator
    put(0, -len(bits) % 8)
    codewords = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(codewords) < capacity // 8:
        codewords.append(pad)
        pad ^= 0xEC ^ 0x11

    # Split into blocks, add Reed-Solomon EC to each and interleave.
    blocks_n, ecc_len = _NUM_BLOCKS[ver - 1], _ECC_PER_BLOCK[ver - 1]
    raw = _raw_modules(ver) // 8
    short_n = blocks_n - raw % blocks_n
    short_len = raw // blocks_n
    divisor = _rs_divisor(ecc_len)
    blocks, k = [], 0
    for i in range(blocks_n):
        dat = codewords[k:k + short_len - ecc_len + (0 if i < short_n else 1)]
        k += len(dat)
        ecc = _rs_remainder(dat, divisor)
        if i < short_n:
            dat = dat + [0]  # placeholder so all blocks have the same length
        blocks.append(dat + ecc)
    out = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != short_len - ecc_len or j >= short_n:
                out.append(blk[i])
    return ver, out


def _alignment_positions(ver: int, size: int) -> list[int]:
    if ver == 1:
        return []
    align = ver // 7 + 2
    step = (ver * 8 + align * 3 + 5) // (align * 4 - 4) * 2
    return [6] + sorted(size - 7 - i * step for i in range(align - 1))


_MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


class _Symbol:
    def __init__(self, ver: int):
        self.ver = ver
        self.size = ver * 4 + 17
        self.dark = [[False] * self.size for _r in range(self.size)]
        self.func = [[False] * self.size for _r in range(self.size)]

    def set_func(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x] = dark
        self.func[y][x] = True

    def draw_function_patterns(self) -> None:
        size = self.size
        for i in range(size):  # timing patterns
            self.set_func(6, i, i % 2 == 0)
            self.set_func(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):  # finders + separators
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self.set_func(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _alignment_positions(self.ver, size)
        last = len(pos) - 1
        for i, ay in enumerate(pos):
            for j, ax in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue  # overlaps a finder pattern
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_func(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)  # reserve the area; redrawn with the real mask
        if self.ver >= 7:
            rem = self.ver
            for _i in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.ver << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = size - 11 + i % 3, i // 3
                self.set_func(a, b, bit)
                self.set_func(b, a, bit)

    def draw_format(self, mask: int) -> None:
        data = _FORMAT_M << 3 | mask
        rem = data
        for _i in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412

        def bit(i: int) -> bool:
            return (bits >> i) & 1 == 1

        size = self.size
        for i in range(6):
            self.set_func(8, i, bit(i))
        self.set_func(8, 7, bit(6))
        self.set_func(8, 8, bit(7))
        self.set_func(7, 8, bit(8))
        for i in range(9, 15):
            self.set_func(14 - i, 8, bit(i))
        for i in range(8):
            self.set_func(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_func(8, size - 15 + i, bit(i))
        self.set_func(8, size - 8, True)  # the dark module

    def draw_codewords(self, codewords: list[int]) -> None:
        size, i, total = self.size, 0, len(codewords) * 8
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5  # skip the vertical timing pattern
            upward = (right + 1) & 2 == 0
            for vert in range(size):
                y = size - 1 - vert if upward else vert
                for x in (right, right - 1):
                    if not self.func[y][x] and i < total:
                        self.dark[y][x] = (codewords[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        fn = _MASKS[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.func[y][x] and fn(x, y):
                    self.dark[y][x] = not self.dark[y][x]

    def penalty(self) -> int:
        size, grid, score = self.size, self.dark, 0
        lines = grid + [list(col) for col in zip(*grid)]
        finder_like = ([True, False, True, True, True, False, True, False, False, False, False],
                       [False, False, False, False, True, False, True, True, True, False, True])
        for line in lines:
            run = 1
            for i in range(1, size + 1):
                if i < size and line[i] == line[i - 1]:
                    run += 1
                    continue
                if run >= 5:
                    score += run - 2
                run = 1
            for i in range(size - 10):
                if line[i:i + 11] in finder_like:
                    score += 40
        for y in range(size - 1):
            for x in range(size - 1):
                if grid[y][x] == grid[y][x + 1] == grid[y + 1][x] == grid[y + 1][x + 1]:
                    score += 3
        dark = sum(map(sum, grid))
        total = size * size
        score += ((abs(dark * 20 - total * 10) + total - 1) // total - 1) * 10
        return score


def qr_matrix(text: str) -> list[list[bool]]:
    """Encode text and return the module grid (True = dark), without quiet zone."""
    ver, codewords = _codewords(text.encode("utf-8"))
    best, best_score = None, None
    for mask in range(8):
        sym = _Symbol(ver)
        sym.draw_function_patterns()
        sym.draw_codewords(codewords)
        sym.apply_mask(mask)
        sym.draw_format(mask)
        score = sym.penalty()
        if best_score is None or score < best_score:
            best, best_score = sym, score
    return best.dark


def qr_svg(text: str, label: str) -> str:
    """Inline SVG of the QR code for text; label is its accessible name."""
    grid = qr_matrix(text)
    size, quiet = len(grid), 4
    path = []
    for y, row in enumerate(grid):
        x = 0
        while x < size:
            if row[x]:
                start = x
                while x < size and row[x]:
                    x += 1
                path.append(f"M{start + quiet} {y + quiet}h{x - start}v1h{start - x}z")
            x += 1
    full = size + 2 * quiet
    return (f'<svg class="qr-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {full} {full}" '
            f'shape-rendering="crispEdges" role="img" aria-label="{esc(label)}">'
            f'<rect width="{full}" height="{full}" fill="#fff"/><path fill="#000" d="{"".join(path)}"/></svg>')


def qr_figure(text: str, label: str, caption: str = "") -> str:
    """QR code with a caption. The text itself must be shown elsewhere on the page."""
    alt = _("QR code: {label}", label=label)
    cap = f'<figcaption class="muted small">{esc(caption)}</figcaption>' if caption else ""
    return f'<figure class="qr">{qr_svg(text, alt)}{cap}</figure>'

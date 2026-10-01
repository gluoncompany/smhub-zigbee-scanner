# Zigbee channel scanner for Silicon Labs EmberZNet (EZSP) radios. Python stdlib only.
import os, time, select, struct, socket

MASK = 0x07FFF800
PREF = (11, 15, 20, 25)
RES = set([0x7E, 0x7D, 0x11, 0x13, 0x18, 0x1A])


def crc16(d):
    c = 0xFFFF
    for b in d:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) if c & 0x8000 else (c << 1)
            c &= 0xFFFF
    return c


def rnd(d):
    r = 0x42
    o = bytearray()
    for b in d:
        o.append(b ^ r)
        r = ((r >> 1) ^ 0xB8) if r & 1 else (r >> 1)
    return bytes(o)


def stuff(d):
    o = bytearray()
    for b in d:
        if b in RES:
            o += bytes([0x7D, b ^ 0x20])
        else:
            o.append(b)
    return bytes(o)


def frame(body):
    return stuff(body + struct.pack(">H", crc16(body))) + b"\x7e"


class ScanError(Exception):
    """Error with a translation key and optional arguments."""
    def __init__(self, key, *args):
        super().__init__(key)
        self.key = key
        self.args_ = [str(a) for a in args]


class Radio:
    def __init__(self, port, baud=115200):
        self.buf = bytearray()
        self.rx_ack = 0
        self.tx_seq = 0
        self.ezseq = 0
        self.ver = 4
        self.callbacks = []
        if port.startswith("tcp://"):
            host, p = port[6:].rsplit(":", 1)
            self.sock = socket.create_connection((host, int(p)), timeout=5)
            self.sock.setblocking(False)
            self.fd = self.sock
        else:
            import termios
            self.sock = None
            self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY)
            a = termios.tcgetattr(self.fd)
            a[0] = termios.IXON
            a[1] = 0
            a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            a[3] = 0
            a[4] = a[5] = getattr(termios, "B%d" % baud)
            a[6][termios.VMIN] = 0
            a[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, a)
            termios.tcflush(self.fd, termios.TCIOFLUSH)

    def wr(self, d):
        if self.sock:
            self.sock.sendall(d)
        else:
            os.write(self.fd, d)

    def rd(self):
        return self.sock.recv(512) if self.sock else os.read(self.fd, 512)

    def close(self):
        try:
            self.wr(frame(b"\xc0"))
        except OSError:
            pass
        if self.sock:
            self.sock.close()
        else:
            os.close(self.fd)

    def read_frames(self, timeout):
        out = []
        end = time.time() + timeout
        while time.time() < end and not out:
            r, _, _ = select.select([self.fd], [], [], 0.05)
            if r:
                self.buf += self.rd()
            while b"\x7e" in self.buf:
                i = self.buf.index(b"\x7e")
                raw = bytes(self.buf[:i])
                self.buf = self.buf[i + 1:]
                if b"\x1a" in raw:
                    raw = raw[raw.rindex(b"\x1a") + 1:]
                d = bytearray()
                esc = False
                for b in raw:
                    if b in (0x11, 0x13):
                        continue
                    if b == 0x7D:
                        esc = True
                        continue
                    d.append(b ^ 0x20 if esc else b)
                    esc = False
                if len(d) >= 3 and crc16(bytes(d[:-2])) == struct.unpack(">H", bytes(d[-2:]))[0]:
                    out.append(bytes(d[:-2]))
        return out

    def pump(self, t):
        got = []
        for fr in self.read_frames(t):
            c = fr[0]
            if c & 0x80 == 0:
                if (c >> 4) & 7 == self.rx_ack:
                    self.rx_ack = (self.rx_ack + 1) & 7
                    got.append(rnd(fr[1:]))
                self.wr(frame(bytes([0x80 | self.rx_ack])))
            elif c == 0xC1:
                got.append(b"RSTACK")
        return got

    def parse(self, e):
        if self.ver >= 8 and len(e) >= 5:
            return struct.unpack("<H", e[3:5])[0], e[5:]
        return e[2], e[3:]

    def send(self, fid, params, want):
        self.ezseq = (self.ezseq + 1) & 0xFF
        if self.ver >= 8:
            ez = bytes([self.ezseq, 0x00, 0x01]) + struct.pack("<H", fid) + params
        else:
            ez = bytes([self.ezseq, 0x00, fid]) + params
        body = bytes([(self.tx_seq << 4) | self.rx_ack]) + rnd(ez)
        self.tx_seq = (self.tx_seq + 1) & 7
        for _ in range(3):
            self.wr(frame(body))
            end = time.time() + 2
            while time.time() < end:
                result = None
                for e in self.pump(0.2):
                    if e == b"RSTACK":
                        continue
                    f, p = self.parse(e)
                    if f == want and result is None:
                        result = p
                    else:
                        self.callbacks.append((f, p))
                if result is not None:
                    return result
            body = bytes([body[0] | 0x08]) + body[1:]
        raise ScanError("err_no_response", "0x%04x" % fid)

    def connect(self):
        self.wr(b"\x1a" * 32 + frame(b"\xc0"))
        end = time.time() + 5
        while time.time() < end:
            if b"RSTACK" in self.pump(0.3):
                break
        else:
            raise ScanError("err_no_rstack")
        p = self.send(0x00, bytes([4]), 0x00)
        self.ver = p[0]
        if self.ver >= 8:
            p = self.send(0x00, bytes([self.ver]), 0x00)
        return {"ezsp": p[0], "stack_type": p[1], "stack_version": "0x%04x" % struct.unpack("<H", p[2:4])[0]}

    def scan(self, stype, dur, on_cb):
        self.callbacks.clear()
        st = self.send(0x1A, struct.pack("<BIB", stype, MASK, dur), 0x1A)
        if st and st[0] != 0:
            raise ScanError("err_scan_rejected", "0x%02x" % st[0])
        end = time.time() + 60
        while time.time() < end:
            for e in self.pump(0.3):
                if e != b"RSTACK":
                    self.callbacks.append(self.parse(e))
            while self.callbacks:
                f, q = self.callbacks.pop(0)
                if f == 0x1C:
                    if len(q) < 2 or q[1] == 0:
                        return True
                else:
                    on_cb(f, q)
        return False


def run_scan(port, baud=115200, passes=5, progress=lambda key, pct, args=(): None):
    radio = Radio(port, baud)
    try:
        progress("p_connecting", 5)
        info = radio.connect()

        nets = {}

        def on_net(f, q):
            if f != 0x1B or len(q) < 16:
                return
            ch, pan = q[0], struct.unpack("<H", q[1:3])[0]
            epan = struct.unpack("<Q", q[3:11])[0]
            join, lqi, rssi = q[11], q[14], struct.unpack("b", q[15:16])[0]
            n = nets.setdefault((ch, epan), {"channel": ch, "pan_id": "0x%04x" % pan,
                                             "ext_pan_id": "0x%016x" % epan, "rssi": rssi,
                                             "lqi": lqi, "permit_join": False, "beacons": 0})
            n["beacons"] += 1
            n["permit_join"] = n["permit_join"] or bool(join)
            if rssi > n["rssi"]:
                n["rssi"], n["lqi"] = rssi, lqi

        for i in range(2):
            progress("p_networks", 10 + i * 10, (i + 1, 2))
            radio.scan(1, 3, on_net)

        samples = {ch: [] for ch in range(11, 27)}

        def on_energy(f, q):
            if f == 0x48 and q[0] in samples:
                samples[q[0]].append(struct.unpack("b", q[1:2])[0])

        for i in range(passes):
            progress("p_energy", 30 + int(65 * i / passes), (i + 1, passes))
            radio.scan(0, 4, on_energy)
    finally:
        radio.close()

    netcount = {}
    for (ch, _e) in nets:
        netcount[ch] = netcount.get(ch, 0) + 1

    channels = []
    for ch in range(11, 27):
        v = samples[ch]
        if not v:
            channels.append({"channel": ch, "samples": 0})
            continue
        med = sum(v) / len(v)
        mx = max(v)
        nn = netcount.get(ch, 0)
        score = med + 0.25 * (mx - med) + 8 * nn + (0 if ch in PREF else 4) + (2 if ch == 26 else 0)
        notes = []
        if nn:
            notes.append("n_networks")
        if ch not in PREF:
            notes.append("n_nonstandard")
        if ch == 26:
            notes.append("n_ch26")
        channels.append({"channel": ch, "samples": len(v), "min": min(v), "avg": round(med, 1),
                         "max": mx, "networks": nn, "score": round(score, 1), "notes": notes,
                         "preferred": ch in PREF})

    ranked = sorted([c for c in channels if c["samples"]], key=lambda c: c["score"])
    best = ranked[0]["channel"] if ranked else None
    best_pref = next((c["channel"] for c in ranked if c["preferred"] and not c["networks"]), None)
    progress("p_done", 100)
    return {"timestamp": int(time.time()), "radio": info, "passes": passes,
            "networks": sorted(nets.values(), key=lambda n: (n["channel"], -n["rssi"])),
            "channels": channels, "ranking": [c["channel"] for c in ranked],
            "best": best, "best_preferred": best_pref}

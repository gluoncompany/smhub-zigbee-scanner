#!/usr/bin/env python3
# Builds zigbee-scanner .ipk and a local opkg feed. Run from the folder containing app/ and control/.
import gzip, hashlib, io, os, tarfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "feed")
EXEC = {"postinst", "prerm", "postrm", "openrc", "server.py"}

def targz(entries):
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w:gz", format=tarfile.GNU_FORMAT) as tf:
        dirs = set()
        for arc, src in entries:
            parts = arc.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                d = "/".join(parts[:i])
                if d not in dirs and d != ".":
                    dirs.add(d)
                    ti = tarfile.TarInfo(d + "/"); ti.type = tarfile.DIRTYPE; ti.mode = 0o755; ti.mtime = int(time.time())
                    tf.addfile(ti)
            data = open(src, "rb").read()
            ti = tarfile.TarInfo(arc); ti.size = len(data); ti.mtime = int(time.time())
            ti.mode = 0o755 if os.path.basename(arc) in EXEC else 0o644
            tf.addfile(ti, io.BytesIO(data))
    return bio.getvalue()

def ar(members):
    out = b"!<arch>\n"
    for name, data in members:
        hdr = "%-16s%-12d%-6d%-6d%-8s%-10d`\n" % (name + "/", int(time.time()), 0, 0, "100644", len(data))
        out += hdr.encode() + data + (b"\n" if len(data) % 2 else b"")
    return out

ctrl_dir = os.path.join(HERE, "control"); app_dir = os.path.join(HERE, "app")
control = targz([("./" + f, os.path.join(ctrl_dir, f)) for f in sorted(os.listdir(ctrl_dir))])
data = targz([("./opt/zigbee-scanner/" + f, os.path.join(app_dir, f)) for f in ("server.py", "scanner.py", "index.html")])
ipk = ar([("debian-binary", b"2.0\n"), ("control.tar.gz", control), ("data.tar.gz", data)])

fields = open(os.path.join(ctrl_dir, "control")).read().strip()
ver = [l.split(":", 1)[1].strip() for l in fields.splitlines() if l.startswith("Version:")][0]
name = "zigbee-scanner_%s_all.ipk" % ver
os.makedirs(OUT, exist_ok=True)
open(os.path.join(OUT, name), "wb").write(ipk)
entry = "%s\nFilename: %s\nSize: %d\nSHA256sum: %s\n" % (fields, name, len(ipk), hashlib.sha256(ipk).hexdigest())
open(os.path.join(OUT, "Packages"), "w").write(entry + "\n")
with gzip.open(os.path.join(OUT, "Packages.gz"), "wb") as g:
    g.write((entry + "\n").encode())
print("OK", name, len(ipk))

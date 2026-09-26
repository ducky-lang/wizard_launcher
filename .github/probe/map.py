import urllib.request, zipfile, io, re, collections, zlib, struct
URL = "https://huggingface.co/datasets/Foxybeo/wz_launcher/resolve/main/Witchcraft%20and%20Wizardry.zip?download=true"
data = urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "probe"}), timeout=600).read()
z = zipfile.ZipFile(io.BytesIO(data))
names = [n for n in z.namelist() if not n.endswith("/")]
print("size", len(data), "files", len(names))
tops = collections.Counter("/".join(n.split("/")[:3]) for n in names)
for k, v in sorted(tops.items())[:80]:
    print(f"{v:6d} {k}")
funcs = [n for n in names if n.endswith(".mcfunction")]
print("functions", len(funcs))
pat = {k: re.compile(k) for k in ["tellraw", "clickEvent", "SelectedItemSlot", "bossbar", "title .* actionbar", "title .* title", "setdisplay", "NoGravity", "Motion", r"tp @e", "teleport", "trigger", "Rotation", "playsound", "scoreboard players", "ArmorStand|armor_stand", "villager"]}
counts = collections.Counter()
samples = collections.defaultdict(list)
for n in funcs:
    for line in z.read(n).decode("utf-8", "replace").splitlines():
        for k, r in pat.items():
            if r.search(line):
                counts[k] += 1
                if len(samples[k]) < 6:
                    samples[k].append(n + ": " + line.strip()[:400])
for k in pat:
    print(f"\n## {k}: {counts[k]}")
    for s in samples[k]:
        print("   ", s)
regions = [n for n in names if n.endswith(".mca") and "/region/" in n and "DIM" not in n]
print("\nregions", len(regions))
cmd = collections.Counter()
cmdsamples = []
for n in regions:
    b = z.read(n)
    for i in range(1024):
        off = struct.unpack(">I", b"\0" + b[i*4:i*4+3])[0] * 4096
        if not off:
            continue
        ln = struct.unpack(">I", b[off:off+4])[0]
        try:
            raw = zlib.decompress(b[off+5:off+4+ln])
        except Exception:
            continue
        for m in re.finditer(rb"Command\x08\x00\x07Command.{0,0}", raw):
            pass
        for m in re.finditer(rb"\x07Command([\x00-\xff]{2})", raw):
            l = struct.unpack(">H", m.group(1))[0]
            s = raw[m.end():m.end()+l].decode("utf-8", "replace")
            head = s.split(" ")[0].lstrip("/")
            cmd[head] += 1
            if any(w in s for w in ["tellraw", "SelectedItemSlot", "title", "bossbar", "tp ", "teleport", "Motion"]) and len(cmdsamples) < 60:
                cmdsamples.append(s[:300])
print("command block heads", cmd.most_common(30))
for s in cmdsamples:
    print("  CB", s)

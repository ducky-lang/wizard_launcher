import urllib.request, zipfile, io, re, collections, zlib, struct, json
def get(u):
    return urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "probe"}), timeout=600).read()
z = zipfile.ZipFile(io.BytesIO(get("https://huggingface.co/datasets/Foxybeo/wz_launcher/resolve/main/Witchcraft%20and%20Wizardry.zip?download=true")))
names = [n for n in z.namelist() if not n.endswith("/")]
funcs = [n for n in names if n.endswith(".mcfunction")]
parts = collections.Counter(); psamp = collections.defaultdict(list); pfiles = collections.Counter()
other = collections.Counter(); osamp = collections.defaultdict(list)
for n in funcs:
    for line in z.read(n).decode("utf-8", "replace").splitlines():
        m = re.search(r"\bparticle\s+(minecraft:)?([a-z_]+)", line)
        if m:
            parts[m.group(2)] += 1; pfiles[n.split("/functions/")[-1].split("/")[0]] += 1
            if len(psamp[m.group(2)]) < 4: psamp[m.group(2)].append(n.split("/functions/")[-1] + ": " + line.strip()[:300])
        for k in ["CustomModelData", "advancement ", "area_effect_cloud", "Particle:", "falling_block", "item_frame", "firework", "fireworks", "end_rod", "\"Glowing\"", "Invisible:1b", "summon ", "Fire:", "HandItems"]:
            if k in line:
                other[k] += 1
                if len(osamp[k]) < 4: osamp[k].append(n.split("/functions/")[-1] + ": " + line.strip()[:300])
print("particle types", parts.most_common())
for k, v in psamp.items():
    print("##", k); [print("   ", s) for s in v]
print("particle by folder", pfiles.most_common(20))
for k, v in other.most_common():
    print("## other", k, v); [print("   ", s) for s in osamp[k]]
adv = [n for n in names if "/advancements/" in n and n.endswith(".json")]
print("\nadvancements", len(adv))
trig = collections.Counter(); disp = 0
for n in adv:
    try: o = json.loads(z.read(n).decode("utf-8", "replace"))
    except Exception as e: print("bad", n, e); continue
    for c in (o.get("criteria") or {}).values(): trig[c.get("trigger")] += 1
    if "display" in o: disp += 1
print("with display", disp, "triggers", trig.most_common())
shown = 0
for n in adv:
    o = json.loads(z.read(n).decode("utf-8", "replace"))
    if "display" in o and shown < 6:
        shown += 1; print("ADV", n, json.dumps(o)[:900])
for n in adv[:3]:
    print("ADVRAW", n, z.read(n).decode()[:600])
print([n for n in names if n.endswith("pack.mcmeta")][:10])
lvl = [n for n in names if n.endswith("level.dat")]
print(lvl)
print([n for n in names if "/advancements/" in n and not n.endswith(".json")][:10])
print("playerdata adv", [n for n in names if "/advancements/" in n and n.count("/") <= 3][:10])

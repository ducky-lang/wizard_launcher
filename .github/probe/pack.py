import urllib.request, zipfile, io, json, struct, collections
URL = "https://huggingface.co/datasets/Foxybeo/wz_launcher/resolve/main/Resource%20Pack.zip?download=true"
data = urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "probe"}), timeout=300).read()
z = zipfile.ZipFile(io.BytesIO(data))
names = [n for n in z.namelist() if not n.endswith("/")]
root = ""
if "pack.mcmeta" not in names:
    root = next(n for n in names if n.endswith("pack.mcmeta")).rsplit("pack.mcmeta", 1)[0]
print("size", len(data), "files", len(names), "root", repr(root))
print(z.read(root + "pack.mcmeta").decode("utf-8", "replace")[:600])
rel = [n[len(root):] for n in names if n.startswith(root)]
def dims(n):
    b = z.read(root + n)[:24]
    return struct.unpack(">II", b[16:24]) if b[:8] == b"\x89PNG\r\n\x1a\n" else None
tops = collections.Counter("/".join(n.split("/")[:4]) for n in rel)
for k, v in sorted(tops.items()):
    print(f"{v:6d} {k}")
print("\n--- gui")
for n in sorted(rel):
    if "/textures/gui/" in n:
        print(n, dims(n) if n.endswith(".png") else "")
print("\n--- optifine (first 200)")
for n in sorted(x for x in rel if "/optifine/" in x or "/mcpatcher/" in x)[:200]:
    print(n)
print("\n--- font")
for n in sorted(x for x in rel if "/font/" in x):
    print(n, dims(n) if n.endswith(".png") else "")
    if n.endswith(".json"):
        print(z.read(root + n).decode("utf-8", "replace")[:3000])
print("\n--- shaders")
for n in sorted(x for x in rel if "/shaders/" in x):
    print(n)
print("\n--- models/entity, textures/entity armor_stand/villager, cem")
for n in sorted(x for x in rel if "cem/" in x or "armor_stand" in x or "/entity/villager" in x)[:120]:
    print(n)
print("\n--- lang keys sample")
for n in sorted(x for x in rel if "/lang/" in x):
    print(n)

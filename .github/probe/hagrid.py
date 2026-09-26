import urllib.request, zipfile, io, re, collections
URL = "https://huggingface.co/datasets/Foxybeo/wz_launcher/resolve/main/Witchcraft%20and%20Wizardry.zip?download=true"
z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "probe"}), timeout=600).read()))
funcs = {n: z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith(".mcfunction")}
hits = [(n, l) for n, s in funcs.items() for l in s.splitlines() if "agrid" in l and ("summon" in l or "Passengers" in l or "ride" in l or "tp " in l)]
print("hagrid lines", len(hits))
for n, l in hits[:40]:
    print(n.split("functions/")[-1], "::", l.strip()[:1500])
riders = collections.Counter()
for n, s in funcs.items():
    for l in s.splitlines():
        if "Passengers:[" in l and "summon" in l:
            riders[re.search(r"summon (\S+)", l).group(1)] += 1
print("\nsummons with Passengers by type", riders.most_common(20))
names = [n for n in funcs if "agrid" in n.lower()]
print("\nhagrid functions", names[:40])
for n in names[:6]:
    print("@@", n)
    print(funcs[n][:3000])

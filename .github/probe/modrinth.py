import json, urllib.request, urllib.parse, zipfile, io
UA = {"User-Agent": "wizard-launcher-probe"}
def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
p = json.loads(get("https://api.modrinth.com/v2/project/fps"))
print("PROJECT", p["id"], p["slug"], p["title"], "|", p["description"])
print("loaders", p["loaders"], "versions", p["game_versions"][-15:])
for mc in ["1.20.1", "1.21.1"]:
    q = urllib.parse.urlencode({"loaders": json.dumps(["fabric"]), "game_versions": json.dumps([mc])})
    vs = json.loads(get(f"https://api.modrinth.com/v2/project/fps/version?{q}"))
    print(f"\n=== {mc}: {len(vs)} versions")
    for v in vs[:5]:
        print(" ", v["id"], v["version_number"], v["version_type"], v["date_published"], v["game_versions"], v["loaders"])
    if not vs:
        continue
    v = vs[0]
    f = next(x for x in v["files"] if x.get("primary")) if any(x.get("primary") for x in v["files"]) else v["files"][0]
    print("FILE", f["filename"], f["url"], f["size"], "sha512", f["hashes"]["sha512"])
    data = get(f["url"])
    z = zipfile.ZipFile(io.BytesIO(data))
    idx = json.loads(z.read("modrinth.index.json"))
    print("deps", idx["dependencies"], "name", idx.get("name"), idx.get("versionId"))
    for e in idx["files"]:
        hosts = sorted({urllib.parse.urlparse(u).hostname for u in e["downloads"]})
        print("  F", e["path"], e.get("env"), hosts, "sha512" in e["hashes"])
    ov = [n for n in z.namelist() if not n.endswith("/") and n != "modrinth.index.json"]
    print("overrides", len(ov))
    for n in ov[:80]:
        print("  O", n)

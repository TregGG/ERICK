from backend.config import settings


async def test_assetlinks_empty_until_fingerprint_configured(client):
    r = await client.get("/.well-known/assetlinks.json")
    assert r.status_code == 200
    assert r.json() == []


async def test_assetlinks_lists_passenger_app(client, monkeypatch):
    monkeypatch.setattr(settings, "ANDROID_TWA_SHA256", " aa:bb:cc , DD:EE:FF ")
    r = await client.get("/.well-known/assetlinks.json")
    assert r.headers["content-type"].startswith("application/json")
    [statement] = r.json()
    assert statement["relation"] == ["delegate_permission/common.handle_all_urls"]
    assert statement["target"] == {"namespace": "android_app", "package_name": "in.ac.iitr.campusrick.passenger",
                                   "sha256_cert_fingerprints": ["AA:BB:CC", "DD:EE:FF"]}


async def test_passenger_app_is_installable(client):
    manifest = (await client.get("/passenger/manifest.json")).json()
    assert {i["sizes"] for i in manifest["icons"] if i["type"] == "image/png"} >= {"192x192", "512x512"}
    assert any(i.get("purpose") == "maskable" for i in manifest["icons"])
    for icon in manifest["icons"]:
        assert (await client.get(icon["src"])).status_code == 200
    sw = await client.get("/passenger/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]

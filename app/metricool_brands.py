"""Listet Metricool-Brands mit blogId + verbundenen Netzwerken (für config.yaml -> posting.brands).

Aufruf auf dem Server:  docker compose run --rm web python -m app.metricool_brands
"""
import os, sys, json
import requests

NETWORK_KEYS = {
    "tiktok": ("tiktok", "tiktokAccount", "tiktokBusiness"),
    "instagram": ("instagram", "instagramConnectionType", "instagramBusiness"),
    "youtube": ("youtube", "youtubeChannelName", "youtubeChannel"),
    "facebook": ("facebook", "facebookPageId"),
    "twitter": ("twitter",),
    "linkedin": ("linkedin", "linkedinCompany"),
    "threads": ("threads",),
}


def main():
    from .common import setting
    token, user = setting("METRICOOL_TOKEN"), setting("METRICOOL_USER_ID")
    if not token or not user:
        sys.exit("METRICOOL_TOKEN und METRICOOL_USER_ID in .env setzen.")
    r = requests.get("https://app.metricool.com/api/admin/simpleProfiles", params={"userId": user},
                     headers={"X-Mc-Auth": token, "Content-Type": "application/json"}, timeout=30)
    if r.status_code >= 400:
        sys.exit(f"Fehler {r.status_code}: {r.text[:300]}")
    data = r.json()
    brands = data if isinstance(data, list) else data.get("data", [])
    if "--json" in sys.argv:
        print(json.dumps(brands, indent=2, ensure_ascii=False))
        return
    print(f"{'blogId':<12} {'Brand':<30} Netzwerke")
    for b in brands:
        nets = [n for n, keys in NETWORK_KEYS.items() if any(b.get(k) for k in keys)]
        print(f"{str(b.get('id') or b.get('blogId')):<12} {str(b.get('label') or b.get('title') or '')[:30]:<30} {', '.join(nets) or '?'}")
    print("\nblogId je Sprache in config.yaml unter posting.brands eintragen (en/de/ur).")
    print("Netzwerke unsicher? Mit --json die Rohdaten ansehen.")


if __name__ == "__main__":
    main()

import sys, datetime as dt, types, json
import pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import morgenzeitung as m
now = dt.datetime.now(dt.timezone.utc)
def rss(n, src):
    items = "".join(f"<item><title>Meldung {src} {i} &amp; Test - Quelle{i}</title><link>https://example.org/{src}/{i}</link><pubDate>{(now-dt.timedelta(hours=i)).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate><description>&lt;a href='x'&gt;Kurztext {src} {i} mit Inhalt über Dinge&lt;/a&gt;</description><source url='x'>Quelle{i}</source></item>" for i in range(n))
    return f"<?xml version='1.0'?><rss version='2.0'><channel><title>{src}</title>{items}</channel></rss>".encode()
class R:
    def __init__(s, content=b"", js=None, text=""): s.content=content; s._js=js; s.text=text
    def raise_for_status(s): pass
    def json(s): return s._js
art = "<html><body><article>" + "".join(f"<p>{'Wort '*40} Absatz {k}.</p>" for k in range(30)) + "</article></body></html>"
def get(url, headers=None, params=None, timeout=None):
    if "/api/states/" in url: return R(js={"state":"partlycloudy","attributes":{"temperature":7.5,"temperature_unit":"°C","humidity":80,"wind_speed":12,"wind_speed_unit":"km/h"}})
    if "/api/calendars/calendar.google_luka" in url: return R(js=[])
    if "/api/calendars/calendar.claude" in url: return R(js=[{"summary":"Zahnarzt","start":{"dateTime":(dt.datetime.now(m.TZ).replace(hour=15,minute=0)).isoformat()},"location":"Mörfelden"}])
    if "/api/calendars/calendar.abfall" in url: return R(js=[{"summary":"Gelbe Tonne","start":{"date":(dt.date.today()+dt.timedelta(days=1)).isoformat()}}])
    if "app_deutschland_he" in url: return R(js=[{"summary":"Erster Weihnachtstag","start":{"date":(dt.date.today()+dt.timedelta(days=5)).isoformat()}},{"summary":"Heute-Feiertag","start":{"date":dt.date.today().isoformat()}}])
    if "example.org" in url: return R(text=art)
    return R(content=rss(8, url[-12:].replace("&","").replace("<","")))
def post(url, headers=None, json=None, timeout=None):
    if "anilist" in url:
        if "MediaListCollection" in json["query"]:
            return R(js={"data":{"MediaListCollection":{"lists":[{"entries":[{"media":{"id":1,"title":{"romaji":"Sousou no Frieren","english":"Frieren"}}},{"media":{"id":2,"title":{"romaji":"Kusuriya no Hitorigoto","english":None}}}]}]}}})
        v=json["variables"]; t0=v["von"]+1
        return R(js={"data":{"Page":{"airingSchedules":[{"airingAt":t0+3*3600,"episode":7,"mediaId":2},{"airingAt":t0+17*3600+30*60,"episode":12,"mediaId":1}]}}})
    fc=[{"datetime":(now+dt.timedelta(days=d)).isoformat(),"condition":"rainy","temperature":9,"templow":3,"precipitation_probability":60} for d in range(5)]
    return R(js={"service_response":{"weather.forecast_home":{"forecast":fc}}})
m.requests.get = get; m.requests.post = post
m.gnewsdecoder = lambda u, interval=None: {'status': True, 'decoded_url': 'https://example.org/dec/'+u[-5:]} if 'Eceid' not in u[-5:] else {'status':False,'message':'x'}
import tomllib
cfg = tomllib.load(open(pathlib.Path(__file__).resolve().parents[1] / "config.toml","rb"))
cfg["zeitung"]["ausgabe_ordner"]=str(pathlib.Path(__file__).resolve().parent / "out"); cfg["homeassistant"]["token"]="abc"; cfg["anime"]["anilist_nutzer"]="test"
import logging; logging.basicConfig(level=logging.INFO)
p = m.zeitung_bauen(cfg); print(p)

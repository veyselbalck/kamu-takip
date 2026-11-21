"""Rapor sayfalarının sunum katmanı: stil, küçük betik, ikonlar ve ortak HTML parçaları.

Veri/analiz mantığından bağımsızdır; scraper.py yalnızca buradaki yardımcıları çağırır.
Sayfalar tek dosyadır (harici font/kütüphane yok), açık/koyu tema ve mobil uyumludur.
Tasarım dili: günlük taranan bir araç (Operate): sakin nötr zemin, tek vurgu rengi, renk yalnızca anlam taşır
(kırmızı = acil/uyarı, turuncu = yaklaşıyor, yeşil = yeni/uygun).
"""
import html as htmllib

_LIGHT = {
    "bg": "#f4f5fa", "surface": "#ffffff", "surface2": "#f6f7fc", "text": "#171a2b", "muted": "#586079",
    "line": "#e1e5f0", "brand": "#3a56d4", "brand-soft": "#e8ecfd", "brand-ink": "#2b43a8", "on-brand": "#ffffff",
    "ok": "#0e7a58", "ok-soft": "#dcf4ea", "warn": "#8a5a00", "warn-soft": "#fff1cc",
    "bad": "#b3202a", "bad-soft": "#fde7e8",
    "shadow": "0 1px 2px rgba(23,26,43,.05)",
}
_DARK = {
    "bg": "#0e1120", "surface": "#161a2e", "surface2": "#1b2038", "text": "#e9ecf8", "muted": "#9ba4c2",
    "line": "#272d4a", "brand": "#8199ff", "brand-soft": "#242c58", "brand-ink": "#b4c0ff", "on-brand": "#0e1120",
    "ok": "#52d3a3", "ok-soft": "#12342c", "warn": "#f3c553", "warn-soft": "#3a2f12",
    "bad": "#ff8087", "bad-soft": "#3e1b21",
    "shadow": "0 1px 2px rgba(0,0,0,.35)",
}


def _vars(d):
    return "".join(f"--{k}:{v};" for k, v in d.items())


# Tek çizgi ağırlığı ve aynı ızgarada çizilmiş ikonlar (24x24, stroke).
_ICONS = {
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    "pin": '<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0z"/><circle cx="12" cy="10" r="3"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "building": '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 22v-4h6v4M8 6h.01M12 6h.01M16 6h.01M8 10h.01M12 10h.01M16 10h.01M8 14h.01M12 14h.01M16 14h.01"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "alert": '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
    "moon": '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    "up": '<path d="M12 19V5M5 12l7-7 7 7"/>',
    "external": '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "star": '<path d="m12 2 3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>',
    "zap": '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>',
    "briefcase": '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
    "access": '<circle cx="12" cy="4.5" r="2"/><path d="M5 9h14M12 9v6M9 21l3-6 3 6"/>',
    "heart": '<path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>',
    "target": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    "cap": '<path d="m22 10-10-5-10 5 10 5 10-5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/>',
    "ban": '<circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/>',
    "hourglass": '<path d="M5 22h14M5 2h14M17 22v-4.17a2 2 0 0 0-.59-1.42L12 12l-4.41 4.41A2 2 0 0 0 7 17.83V22M7 2v4.17a2 2 0 0 0 .59 1.42L12 12l4.41-4.41A2 2 0 0 0 17 6.17V2"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "sliders": '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    "activity": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "flask": '<path d="M10 2v7.31L4.5 18a2 2 0 0 0 1.7 3h11.6a2 2 0 0 0 1.7-3L14 9.31V2M8.5 2h7"/>',
    "eye": '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "doc": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8"/>',
    "wrench": '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    "bot": '<rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4M8 16h.01M16 16h.01"/>',
}


def _sprite():
    sim = "".join(f'<symbol id="i-{k}" viewBox="0 0 24 24">{v}</symbol>' for k, v in _ICONS.items())
    return f'<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>{sim}</defs></svg>'


STYLE = ("""
:root{""" + _vars(_LIGHT) + """color-scheme:light;--top:57px}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){""" + _vars(_DARK) + """color-scheme:dark}}
:root[data-theme="dark"]{""" + _vars(_DARK) + """color-scheme:dark}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:calc(var(--top) + 16px)}
body{margin:0;background:var(--bg);color:var(--text);
 font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
 -webkit-font-smoothing:antialiased;accent-color:var(--brand);scrollbar-color:var(--line) transparent}
h1,h2,h3{text-wrap:balance}
a{color:var(--brand);text-decoration:none;text-underline-offset:3px}
a:hover{text-decoration:underline}
::selection{background:var(--brand-soft);color:var(--text)}
:focus-visible{outline:2px solid var(--brand);outline-offset:2px;border-radius:6px}
.ic{width:1.05em;height:1.05em;stroke:currentColor;fill:none;stroke-width:1.75;stroke-linecap:round;
 stroke-linejoin:round;vertical-align:-.17em;flex:none}
.wrap{max-width:1000px;margin:0 auto;padding:0 16px 64px}

/* üst çubuk */
.topbar{position:sticky;top:0;z-index:20;background:var(--surface);border-bottom:1px solid var(--line)}
.topbar .in{max-width:1000px;margin:0 auto;padding:0 16px;height:var(--top);display:flex;align-items:center;gap:12px}
.brand{display:flex;align-items:center;gap:9px;font-weight:700;letter-spacing:-.01em;white-space:nowrap;color:var(--text)}
.brand .mark{width:26px;height:26px;border-radius:7px;background:var(--brand);color:var(--on-brand);display:grid;place-items:center}
.brand .mark .ic{width:15px;height:15px;stroke-width:2.2;vertical-align:0}
.tabs{display:flex;gap:2px;flex:1;overflow-x:auto;scrollbar-width:none;height:100%}
.tabs::-webkit-scrollbar{display:none}
.tabs a{display:flex;align-items:center;gap:7px;padding:0 12px;color:var(--muted);font-weight:600;font-size:14px;
 white-space:nowrap;border-bottom:2px solid transparent;margin-bottom:-1px}
.tabs a:hover{color:var(--text);text-decoration:none}
.tabs a.on{color:var(--brand-ink);border-bottom-color:var(--brand)}
.iconbtn{border:1px solid var(--line);background:var(--surface);color:var(--text);width:38px;height:38px;
 border-radius:10px;cursor:pointer;display:grid;place-items:center}
.iconbtn:hover{background:var(--brand-soft)}
.iconbtn .ic{width:18px;height:18px;vertical-align:0}
#tema .i-sun{display:none}
#tema[data-dark="true"] .i-sun{display:inline}
#tema[data-dark="true"] .i-moon{display:none}

/* sayfa başlığı */
.pagehead{padding:26px 0 14px}
.pagehead h1{margin:0;font-size:clamp(22px,4vw,28px);letter-spacing:-.02em;line-height:1.2}
.pagehead .meta{margin:6px 0 0;color:var(--muted);font-size:14px;font-variant-numeric:tabular-nums}
.pagehead .alert{margin:10px 0 0;font-size:13.5px;color:var(--warn);background:var(--warn-soft);padding:8px 12px;border-radius:10px}

/* arama ve filtre */
.toolbar{position:sticky;top:var(--top);z-index:10;background:var(--bg);padding:10px 0 10px}
.toolbar .toc{margin:0 0 10px}
.search{position:relative;display:flex;gap:10px;align-items:center}
.search .ic{position:absolute;left:13px;top:50%;margin-top:-.5em;color:var(--muted);pointer-events:none}
.search input{flex:1;min-width:0;padding:10px 14px 10px 38px;border-radius:12px;border:1px solid var(--line);
 background:var(--surface);color:var(--text);font:inherit;min-height:42px}
.search input::placeholder{color:var(--muted)}
.search input:focus{outline:2px solid var(--brand);outline-offset:0;border-color:transparent}
.shown{font-size:13px;color:var(--muted);white-space:nowrap;font-variant-numeric:tabular-nums}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--surface);color:var(--text);
 padding:0 12px;min-height:34px;border-radius:999px;font-family:inherit;font-weight:600;font-size:13px;cursor:pointer}
.chip:hover{border-color:var(--brand)}
.chip .n{font-weight:500;color:var(--muted);font-variant-numeric:tabular-nums}
.chip[aria-pressed="true"]{background:var(--brand);border-color:var(--brand);color:var(--on-brand)}
.chip[aria-pressed="true"] .n{color:inherit;opacity:.85}
.toc{display:flex;gap:4px;flex-wrap:wrap;margin:0 0 10px;padding:4px;background:var(--surface2);border-radius:12px;width:fit-content;max-width:100%}
.toc button{font:inherit;font-size:14px;font-weight:600;padding:0 14px;min-height:38px;border:0;border-radius:9px;background:transparent;
 color:var(--muted);cursor:pointer;display:inline-flex;align-items:center;gap:7px}
.toc button:hover{color:var(--text)}
.toc button[aria-pressed="true"]{background:var(--surface);color:var(--brand-ink);box-shadow:0 1px 2px rgba(0,0,0,.12)}
.toc .n{font-weight:500;font-size:12.5px;color:var(--muted);font-variant-numeric:tabular-nums}

/* bölümler */
.sec{margin-top:28px}
.sec-h{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.sec-h h2{margin:0;font-size:19px;letter-spacing:-.01em;display:flex;align-items:center;gap:9px}
.sec-h h2 .ic{color:var(--muted)}
.count{background:var(--brand-soft);color:var(--brand-ink);font-weight:700;font-size:12.5px;border-radius:999px;padding:1px 10px;
 font-variant-numeric:tabular-nums}
.empty{color:var(--muted);background:var(--surface);border:1px dashed var(--line);border-radius:14px;padding:18px;text-align:center}
.note{font-size:13px;color:var(--muted)}
p{text-wrap:pretty}
p.note{max-width:62ch}

/* ilan kartı */
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px 18px 14px;margin:10px 0;
 box-shadow:var(--shadow)}
.card[data-urg="today"],.card[data-urg="soon"]{border-color:color-mix(in srgb,var(--bad) 55%,var(--line))}
.card .top{display:flex;gap:14px;align-items:flex-start;justify-content:space-between}
.card .main{min-width:0}
.flags{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px}
.flag{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:700;border-radius:6px;padding:2px 8px}
.flag.fit{background:var(--brand-soft);color:var(--brand-ink)}
.flag.new{background:var(--ok-soft);color:var(--ok)}
.ttl{display:block;font-weight:700;font-size:18px;line-height:1.35;letter-spacing:-.005em;color:var(--text);overflow-wrap:anywhere}
.ttl:hover{color:var(--brand);text-decoration:none}
.kurum{color:var(--text);margin-top:4px;font-size:15px;display:flex;gap:7px;align-items:baseline}
.kurum .ic,.yer .ic{transform:translateY(2px);color:var(--muted)}
.yer{margin-top:3px;font-size:15px;display:flex;gap:7px;align-items:baseline;color:var(--brand-ink)}
.yer b{font-weight:700;letter-spacing:.005em}
.yer.yok{font-size:13px;color:var(--muted)}
.due{flex-shrink:0;text-align:right;font-size:13px;color:var(--muted);line-height:1.35;white-space:nowrap}
.due b{display:inline-flex;align-items:center;gap:5px;font-size:14px;color:var(--muted)}
.due small{display:block;font-variant-numeric:tabular-nums}
.due[data-urg="today"] b,.due[data-urg="soon"] b{color:var(--bad)}
.due[data-urg="week"] b{color:var(--warn)}
.due[data-urg="later"] b{color:var(--ok)}
.facts{display:grid;grid-template-columns:repeat(3,1fr);gap:12px 18px;margin:14px 0 2px;padding-top:12px;border-top:1px solid var(--line)}
.facts div{min-width:0}
.facts dt{font-size:12.5px;font-weight:600;color:var(--muted)}
.facts dd{margin:2px 0 0;font-size:14.5px;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
.facts dd.bad{color:var(--bad);font-weight:600}
.meta-tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.tag{display:inline-flex;align-items:center;gap:5px;background:var(--surface2);border:1px solid var(--line);color:var(--muted);
 padding:2px 10px;border-radius:999px;font-size:12.5px}
.tag.red{background:var(--bad-soft);border-color:transparent;color:var(--bad)}
.tag.src{background:var(--ok-soft);border-color:transparent;color:var(--ok)}
.warn{display:flex;gap:8px;background:var(--warn-soft);color:var(--warn);padding:8px 12px;border-radius:10px;font-size:13.5px;margin-top:10px}
.warn .ic{margin-top:3px}
.card p.sum{margin:10px 0 0;font-size:15px;line-height:1.6;max-width:72ch}
details.ev{margin-top:8px;font-size:13px;color:var(--muted)}
details.ev summary{cursor:pointer;display:inline-block;color:var(--muted);font-weight:600}
details.ev blockquote{margin:6px 0 0;padding:4px 12px;border-left:2px solid var(--line);font-style:italic;max-width:72ch}
.actions{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-top:12px}
.actions .note{margin:0}
.btn{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--brand);color:var(--brand-ink);font-weight:700;
 font-size:13.5px;min-height:36px;padding:0 14px;border-radius:10px;background:transparent}
.btn:hover{background:var(--brand);color:var(--on-brand);text-decoration:none}

/* katlanır paneller */
details.panel{background:var(--surface);border:1px solid var(--line);border-radius:14px;margin-top:12px}
details.panel>summary{cursor:pointer;padding:13px 16px;list-style:none;display:flex;gap:10px;align-items:center;min-height:48px}
details.panel>summary::-webkit-details-marker{display:none}
details.panel>summary::after{content:"";margin-left:auto;width:8px;height:8px;border-right:2px solid var(--muted);
 border-bottom:2px solid var(--muted);transform:rotate(45deg) translateY(-2px);transition:transform .15s}
details.panel[open]>summary::after{transform:rotate(-135deg) translateY(-2px)}
details.panel>summary .ic{color:var(--muted)}
details.panel>summary small{color:var(--muted);font-weight:400}
.panel-b{padding:2px 16px 16px}
.panel-b ul{margin:6px 0;padding-left:20px}
.panel-b li{margin:6px 0}
.panel-b h3{display:flex;align-items:center;gap:8px;font-size:15px;margin:20px 0 6px}
.panel-b .card{border:0;border-top:1px solid var(--line);border-radius:0;box-shadow:none;background:transparent;margin:0;padding:14px 0}
.panel-b .card[data-urg]{border-color:var(--line)}
.runstats{display:flex;flex-wrap:wrap;gap:6px 22px;list-style:none;margin:0;padding:0;font-size:13.5px;color:var(--muted)}
.runstats b{color:var(--text);font-size:15px;font-variant-numeric:tabular-nums}
.runstats .bad b{color:var(--bad)} .runstats .ok b{color:var(--ok)}

/* tablolar */
.tbl{overflow-x:auto;border:1px solid var(--line);border-radius:12px;margin:10px 0;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:13px}
th{background:var(--surface2);color:var(--muted);text-align:left;font-size:12px;font-weight:600}
td,th{padding:9px 14px;border-bottom:1px solid var(--line);vertical-align:top;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:0}
tr.bad td{background:var(--bad-soft)}
tbody tr:hover td{background:var(--surface2)}
tr.bad:hover td{background:var(--bad-soft)}
.pill{display:inline-block;font-size:12px;font-weight:700;border-radius:6px;padding:1px 8px;white-space:nowrap;
 background:var(--brand-soft);color:var(--brand-ink)}
.pill.red{background:var(--bad-soft);color:var(--bad)}
.pill.green{background:var(--ok-soft);color:var(--ok)}
.new{background:var(--ok-soft);color:var(--ok);border-radius:6px;padding:1px 7px;font-size:11.5px;margin-left:6px;font-weight:700}

/* tanı sayfası */
img{max-width:100%;border:1px solid var(--line);border-radius:10px}
pre{white-space:pre-wrap;background:var(--surface2);padding:10px;border:1px solid var(--line);border-radius:10px;font-size:12px;overflow-x:auto}

footer{margin-top:36px;color:var(--muted);font-size:12.5px;max-width:60ch}
#up{position:fixed;right:16px;bottom:16px;display:none;box-shadow:0 2px 8px rgba(0,0,0,.2)}
.hide{display:none!important}

@media (max-width:640px){
 .pagehead{padding-top:18px}
 .card{padding:14px 14px 12px}
 .card .top{flex-direction:column;gap:8px}
 .due{text-align:left;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
 .due small{display:inline}
 .brand span:not(.mark){display:none}
 .toolbar{position:static;padding-top:4px}
 .toc{width:100%}
 .toc button{flex:1;justify-content:center;padding:0 8px}
 .facts{grid-template-columns:1fr 1fr}
}
@media print{
 .topbar,.toolbar,#up,.actions{display:none!important}
 body{background:#fff}
 .card,.panel{box-shadow:none;break-inside:avoid}
 details.panel:not([open])>.panel-b{display:block}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important;scroll-behavior:auto!important}}
""")


SCRIPT = """
(function(){
  var root=document.documentElement;
  function store(k,v){try{if(v===undefined)return localStorage.getItem(k);localStorage.setItem(k,v)}catch(e){return null}}
  var saved=store('tema');
  if(saved){root.setAttribute('data-theme',saved)}
  var tb=document.getElementById('tema');
  function isDark(){var t=root.getAttribute('data-theme');
    return t?t==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches}
  function paint(){if(tb)tb.setAttribute('data-dark',isDark()?'true':'false')}
  if(tb)tb.addEventListener('click',function(){var n=isDark()?'light':'dark';root.setAttribute('data-theme',n);store('tema',n);paint()});
  paint();

  var q=document.getElementById('q'),cards=[].slice.call(document.querySelectorAll('.card')),
      secs=[].slice.call(document.querySelectorAll('[data-sec]')),shown=document.getElementById('shown'),
      chips=[].slice.call(document.querySelectorAll('.chip')),mode='all',sel='all',
      tabs=[].slice.call(document.querySelectorAll('[data-sec-btn]')),
      listed=cards.filter(function(c){return c.closest('[data-sec]')});
  function inSel(c){var s=c.closest('[data-sec]');return !!s&&(sel==='all'||s.id===sel)}
  function norm(s){return (s||'').toLocaleLowerCase('tr')}
  function match(c,m){
    var d=c.getAttribute('data-days'),days=d===''||d===null?null:+d;
    if(m==='new')return c.getAttribute('data-new')==='1';
    if(m==='fit')return c.getAttribute('data-fit')==='1';
    if(m==='soon')return days!==null&&days<=7;
    if(m==='warn')return c.getAttribute('data-warn')==='1';
    return true}
  function ok(c){
    if(!match(c,mode))return false;
    var t=norm(q&&q.value.trim());
    return !t||c.getAttribute('data-q').indexOf(t)>-1}
  function chipCounts(){chips.forEach(function(b){var n=b.querySelector('.n'),m=b.getAttribute('data-mode');
    if(n)n.textContent=listed.filter(function(c){return inSel(c)&&match(c,m)}).length})}
  tabs.forEach(function(b){var id=b.getAttribute('data-sec-btn'),n=b.querySelector('.n');
    if(n)n.textContent=listed.filter(function(c){var s=c.closest('[data-sec]');return id==='all'||s.id===id}).length});
  function apply(){
    var n=0,total=0,active=mode!=='all'||(q&&q.value.trim());
    cards.forEach(function(c){var v=ok(c);c.classList.toggle('hide',!v);
      if(inSel(c)){total++;if(v)n++}});
    secs.forEach(function(s){
      var all=s.querySelectorAll('.card').length,pick=sel==='all'||s.id===sel;
      var vis=s.querySelectorAll('.card:not(.hide)').length,k=s.querySelector('.count');
      if(k&&all)k.textContent=active?vis+' / '+all:all;
      s.classList.toggle('hide',!pick||(all>0&&active&&!vis))});
    chipCounts();
    if(shown)shown.textContent=active?n+' / '+total+' ilan':total+' ilan'}
  tabs.forEach(function(b){b.addEventListener('click',function(){
    sel=b.getAttribute('data-sec-btn');
    tabs.forEach(function(x){x.setAttribute('aria-pressed',x===b?'true':'false')});apply()})});
  if(q)q.addEventListener('input',apply);
  chips.forEach(function(b){b.addEventListener('click',function(){
    mode=b.getAttribute('data-mode');
    chips.forEach(function(x){x.setAttribute('aria-pressed',x===b?'true':'false')});apply()})});
  document.addEventListener('keydown',function(e){
    if(e.key==='/'&&q&&document.activeElement!==q&&!/input|textarea/i.test(document.activeElement.tagName)){e.preventDefault();q.focus()}
    if(e.key==='Escape'&&q&&document.activeElement===q){q.value='';apply();q.blur()}});
  apply();

  var up=document.getElementById('up');
  if(up){window.addEventListener('scroll',function(){up.style.display=window.scrollY>600?'grid':'none'},{passive:true});
    up.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'})})}
})();
"""


def esc(x):
    return htmllib.escape(str(x)) if x not in (None, "") else ""


def ic(name, cls=""):
    """Satır içi ikon (sayfa başındaki sprite'a başvurur)."""
    return f'<svg class="ic{(" " + cls) if cls else ""}" aria-hidden="true"><use href="#i-{name}"/></svg>'


def stats(items, small=False):
    """Kısa sayaç satırı: [(değer, etiket, ton)] — ton: ok | bad | ''. (Büyük sayı kutusu değil, satır içi liste.)"""
    li = "".join(f'<li class="{esc(t)}"><b>{esc(v)}</b> {esc(l)}</li>' for v, l, t in items)
    return f'<ul class="runstats">{li}</ul>'


def panel(summary, inner, open_=False, icon=None):
    """Katlanır panel. summary ve inner hazır HTML'dir (çağıran kaçışlar)."""
    return (f'<details class="panel"{" open" if open_ else ""}><summary>{ic(icon) if icon else ""}{summary}</summary>'
            f'<div class="panel-b">{inner}</div></details>')


def table(head, rows_html):
    th = "".join(f"<th>{h}</th>" for h in head)
    return f'<div class="tbl"><table><thead><tr>{th}</tr></thead><tbody>{rows_html}</tbody></table></div>'


_CHIPS = (("all", "Tümü"), ("fit", "Güçlü uyum"), ("new", "Bugün eklenen"),
          ("soon", "7 gün içinde bitenler"), ("warn", "Dikkat gerekenler"))


def toolbar(sections):
    """Bölüm sekmeleri (sayfa kaymaz, yalnız seçilen bölüm görünür) + arama ve filtre çubuğu. sections: [(id, etiket)]."""
    toc = ('<button type="button" data-sec-btn="all" aria-pressed="true">Tümü <span class="n"></span></button>'
           + "".join(f'<button type="button" data-sec-btn="{esc(i)}" aria-pressed="false">{esc(t)} <span class="n"></span></button>'
                     for i, t in sections))
    chips = "".join(
        f'<button class="chip" data-mode="{m}" aria-pressed="{"true" if m == "all" else "false"}">{l} <span class="n"></span></button>'
        for m, l in _CHIPS)
    return ('<div class="toolbar" role="search">'
            + (f'<div class="toc" role="group" aria-label="Bölüm">{toc}</div>' if len(sections) > 1 else "")
            + '<div class="search">' + ic("search")
            + '<input id="q" type="search" placeholder="Kurum, başlık veya şehir ara   ( / )" autocomplete="off" '
              'aria-label="İlanlarda ara"><span class="shown" id="shown" aria-live="polite"></span></div>'
            + f'<div class="chips" role="group" aria-label="Filtre">{chips}</div></div>')


def section(sec_id, title, n, inner, icon=None):
    return (f'<section class="sec" id="{esc(sec_id)}" data-sec><div class="sec-h"><h2>{ic(icon) if icon else ""}{title}</h2>'
            f'<span class="count">{n}</span></div>{inner}</section>')


def page(title, body, active="index", hero="", updated=""):
    """Tam sayfa kabuğu. active: index | onlisans | debug."""
    def tab(href, key, label):
        return f'<a href="{href}"{" class=on aria-current=page" if active == key else ""}>{label}</a>'
    tabs = tab("index.html", "index", f'{ic("target")}Ana rapor') + tab("onlisans.html", "onlisans", f'{ic("access")}Önlisans &amp; Engelli')
    if active == "debug":
        tabs += tab("debug.html", "debug", f'{ic("wrench")}Tanı')
    return (f'<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="color-scheme" content="light dark">'
            f'<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 32 32%27%3E'
            f'%3Crect width=%2732%27 height=%2732%27 rx=%277%27 fill=%27%233a56d4%27/%3E%3Cpath d=%27M9 10h14M9 16h14M9 22h9%27 '
            f'stroke=%27white%27 stroke-width=%273%27 stroke-linecap=%27round%27/%3E%3C/svg%3E">'
            f'<title>{esc(title)}</title><style>{STYLE}</style></head><body>{_sprite()}'
            f'<header class="topbar"><div class="in"><div class="brand"><span class="mark">{ic("list")}</span><span>Kamu İlan Takip</span></div>'
            f'<nav class="tabs" aria-label="Sayfalar">{tabs}</nav>'
            f'<button class="iconbtn" id="tema" aria-label="Tema değiştir" title="Açık/koyu tema">{ic("moon", "i-moon")}{ic("sun", "i-sun")}</button></div></header>'
            f'<main class="wrap">{hero}{body}'
            f'<footer>Son güncelleme: {esc(updated)}. Veriler resmî kaynaklardan otomatik toplanır; '
            f'başvurmadan önce ilanın aslını doğrulayın.</footer></main>'
            f'<button class="iconbtn" id="up" aria-label="Yukarı çık" title="Yukarı">{ic("up")}</button>'
            f'<script>{SCRIPT}</script></body></html>')


def hero(title, line="", sub=""):
    """Sade sayfa başlığı (başlık, tek satır özet, varsa uyarı notu)."""
    return (f'<header class="pagehead"><h1>{title}</h1>'
            + (f'<p class="meta">{line}</p>' if line else "")
            + (f'<p class="alert">{sub}</p>' if sub else "") + "</header>")

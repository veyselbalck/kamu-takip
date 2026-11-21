"""Rapor sayfalarının sunum katmanı: stil, küçük betik ve ortak HTML parçaları.

Veri/analiz mantığından bağımsızdır; scraper.py yalnızca buradaki yardımcıları çağırır.
Sayfalar tek dosyadır (harici font/kütüphane yok), açık/koyu tema ve mobil uyumludur.
"""
import html as htmllib

_LIGHT = {
    "bg": "#f3f5fb", "surface": "#ffffff", "surface2": "#f7f8fd", "text": "#161a2d", "muted": "#59627c",
    "line": "#e3e7f2", "brand": "#3b5bdb", "brand-soft": "#e8edff", "brand-ink": "#2742a8",
    "ok": "#0f7d5a", "ok-soft": "#dff5ec", "warn": "#8f5f00", "warn-soft": "#fff3d1",
    "bad": "#c0262d", "bad-soft": "#fde8e9", "shadow": "0 1px 2px rgba(22,26,45,.06),0 4px 14px rgba(22,26,45,.05)",
    "hero": "linear-gradient(135deg,#3b5bdb 0%,#6741d9 100%)", "on-brand": "#ffffff",
}
_DARK = {
    "bg": "#0c0f1d", "surface": "#151a2f", "surface2": "#1b2140", "text": "#e8ebf7", "muted": "#9aa3c0",
    "line": "#262d4d", "brand": "#7c93ff", "brand-soft": "#232b55", "brand-ink": "#aebbff",
    "ok": "#4fd1a0", "ok-soft": "#12332b", "warn": "#f2c14e", "warn-soft": "#3a2f10",
    "bad": "#ff7b82", "bad-soft": "#3d1a1f", "shadow": "0 1px 2px rgba(0,0,0,.4),0 6px 20px rgba(0,0,0,.25)",
    "hero": "linear-gradient(135deg,#2b3a8f 0%,#4a2f9c 100%)", "on-brand": "#0c0f1d",
}


def _vars(d):
    return "".join(f"--{k}:{v};" for k, v in d.items())


STYLE = ("""
:root{""" + _vars(_LIGHT) + """color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){""" + _vars(_DARK) + """color-scheme:dark}}
:root[data-theme="dark"]{""" + _vars(_DARK) + """color-scheme:dark}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:76px}
body{margin:0;background:var(--bg);color:var(--text);line-height:1.55;
 font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
 -webkit-font-smoothing:antialiased}
a{color:var(--brand);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:1040px;margin:0 auto;padding:0 16px 64px}

/* üst çubuk */
.topbar{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--surface) 88%,transparent);
 backdrop-filter:saturate(1.4) blur(10px);border-bottom:1px solid var(--line)}
.topbar .in{max-width:1040px;margin:0 auto;padding:10px 16px;display:flex;align-items:center;gap:14px}
.brand{font-weight:800;letter-spacing:-.01em;white-space:nowrap}
.tabs{display:flex;gap:4px;flex:1;overflow-x:auto;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tabs a{padding:6px 12px;border-radius:999px;color:var(--muted);font-weight:600;font-size:13.5px;white-space:nowrap}
.tabs a:hover{background:var(--brand-soft);text-decoration:none;color:var(--brand-ink)}
.tabs a.on{background:var(--brand);color:var(--on-brand)}
.iconbtn{border:1px solid var(--line);background:var(--surface);color:var(--text);width:36px;height:36px;
 border-radius:10px;cursor:pointer;font-size:16px;line-height:1}
.iconbtn:hover{background:var(--brand-soft)}

/* üst bölüm */
.hero{margin:20px 0 16px;border-radius:20px;padding:26px 26px 22px;color:#fff;background:var(--hero);
 box-shadow:var(--shadow)}
.hero h1{margin:0 0 4px;font-size:clamp(21px,4vw,28px);letter-spacing:-.02em;line-height:1.2}
.hero p{margin:0;opacity:.9;font-size:14px}
.hero .sub{margin-top:6px;opacity:.8;font-size:12.5px}
.hero a{color:#fff;text-decoration:underline}

.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:14px 0}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:12px 14px;box-shadow:var(--shadow)}
.stat b{display:block;font-size:24px;letter-spacing:-.02em;line-height:1.15}
.stat span{font-size:12px;color:var(--muted)}
.stat.ok b{color:var(--ok)} .stat.bad b{color:var(--bad)} .stat.warn b{color:var(--warn)} .stat.accent b{color:var(--brand)}
.stats.small{grid-template-columns:repeat(auto-fit,minmax(105px,1fr))}
.stats.small .stat{padding:9px 12px;box-shadow:none}
.stats.small b{font-size:18px}

/* araç çubuğu */
.toolbar{position:sticky;top:57px;z-index:10;background:var(--bg);padding:10px 0 8px;margin-bottom:4px}
.search{display:flex;gap:8px;align-items:center}
.search input{flex:1;min-width:0;padding:10px 14px;border-radius:12px;border:1px solid var(--line);
 background:var(--surface);color:var(--text);font:inherit;outline:none}
.search input:focus{border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-soft)}
.shown{font-size:12.5px;color:var(--muted);white-space:nowrap}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.chip{border:1px solid var(--line);background:var(--surface);color:var(--text);padding:5px 12px;border-radius:999px;
 font-family:inherit;font-weight:600;font-size:12.5px;line-height:1.4;cursor:pointer}
.chip:hover{border-color:var(--brand)}
.chip[aria-pressed="true"]{background:var(--brand);border-color:var(--brand);color:var(--on-brand)}
.toc{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 0}
.toc a{font-size:12.5px;padding:3px 10px;border-radius:8px;background:var(--surface2);color:var(--muted);border:1px solid var(--line)}
.toc a:hover{color:var(--brand-ink);text-decoration:none;border-color:var(--brand)}

/* bölüm başlıkları */
.sec{margin-top:26px}
.sec-h{display:flex;align-items:center;gap:10px;margin-bottom:6px}
.sec-h h2{margin:0;font-size:19px;letter-spacing:-.01em}
.count{background:var(--brand-soft);color:var(--brand-ink);font-weight:700;font-size:12.5px;border-radius:999px;padding:2px 10px}
.empty{color:var(--muted);background:var(--surface);border:1px dashed var(--line);border-radius:14px;padding:18px;text-align:center}
.note{font-size:13px;color:var(--muted)}

/* ilan kartı */
.card{position:relative;background:var(--surface);border:1px solid var(--line);border-radius:16px;
 padding:16px 18px 14px 22px;margin:10px 0;box-shadow:var(--shadow);overflow:hidden;
 transition:transform .15s ease,box-shadow .15s ease}
.card:hover{transform:translateY(-1px)}
.card::before{content:"";position:absolute;left:0;top:0;bottom:0;width:5px;background:var(--line)}
.card[data-urg="today"]::before,.card[data-urg="soon"]::before{background:var(--bad)}
.card[data-urg="week"]::before{background:#e8890c}
.card[data-urg="later"]::before{background:var(--ok)}
.card[data-new="1"]{border-color:color-mix(in srgb,var(--ok) 45%,var(--line))}
.card .top{display:flex;gap:10px;align-items:flex-start;justify-content:space-between}
.card .ttl{font-weight:750;font-size:16.5px;line-height:1.35;letter-spacing:-.005em;color:var(--text)}
.card .ttl:hover{color:var(--brand)}
.kurum{color:var(--muted);margin-top:2px;font-size:14px}
.pills{display:flex;gap:6px;flex-shrink:0;flex-wrap:wrap;justify-content:flex-end}
.pill{display:inline-block;font-size:12px;font-weight:700;border-radius:999px;padding:3px 10px;white-space:nowrap;
 background:var(--brand-soft);color:var(--brand-ink)}
.pill.new{background:var(--ok);color:#fff}
.pill.red{background:var(--bad-soft);color:var(--bad)}
.pill.orange{background:var(--warn-soft);color:var(--warn)}
.pill.green{background:var(--ok-soft);color:var(--ok)}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px 18px;margin:12px 0 4px}
.facts div{min-width:0}
.facts dt{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.facts dd{margin:1px 0 0;font-size:13.5px;overflow-wrap:anywhere}
.facts dd.bad{color:var(--bad);font-weight:600}
.meta{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.tag{background:var(--surface2);border:1px solid var(--line);color:var(--muted);padding:2px 10px;border-radius:999px;font-size:12px}
.tag.red{background:var(--bad-soft);border-color:transparent;color:var(--bad)}
.tag.src{background:var(--ok-soft);border-color:transparent;color:var(--ok)}
.warn{background:var(--warn-soft);color:var(--warn);padding:8px 12px;border-radius:10px;font-size:13px;margin-top:10px}
.card p.sum{margin:10px 0 0;font-size:14px}
details.ev{margin-top:8px;font-size:12.5px;color:var(--muted)}
details.ev summary{cursor:pointer;display:inline-block;color:var(--muted);font-weight:600}
details.ev blockquote{margin:6px 0 0;padding:6px 12px;border-left:3px solid var(--line);font-style:italic}
.kanit{font-size:12.5px;color:var(--muted);margin-top:6px}
.actions{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:12px}
.btn{display:inline-flex;align-items:center;gap:6px;background:var(--brand);color:var(--on-brand);font-weight:700;font-size:13px;
 padding:7px 14px;border-radius:10px}
.btn:hover{text-decoration:none;filter:brightness(1.08)}

/* katlanır paneller */
details.panel{background:var(--surface);border:1px solid var(--line);border-radius:14px;margin-top:14px;box-shadow:var(--shadow)}
details.panel>summary{cursor:pointer;padding:13px 16px;list-style:none;display:flex;gap:10px;align-items:center}
details.panel>summary::-webkit-details-marker{display:none}
details.panel>summary::before{content:"▸";color:var(--muted);transition:transform .15s}
details.panel[open]>summary::before{transform:rotate(90deg)}
details.panel>summary small{color:var(--muted);font-weight:400}
.panel-b{padding:2px 16px 16px}
.panel-b ul{margin:6px 0;padding-left:20px}
.panel-b li{margin:6px 0}
.panel-b .card{box-shadow:none}

/* tablolar */
.tbl{overflow-x:auto;border:1px solid var(--line);border-radius:12px;margin:10px 0;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th{background:var(--surface2);color:var(--muted);text-align:left;font-size:11.5px;text-transform:uppercase;letter-spacing:.04em}
td,th{padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
tr.bad td{background:var(--bad-soft)}
tbody tr:hover td{background:var(--surface2)}
.new{background:var(--ok);color:#fff;border-radius:6px;padding:1px 7px;font-size:11px;margin-left:6px;font-weight:700}

/* tanı sayfası */
img{max-width:100%;border:1px solid var(--line);border-radius:10px}
pre{white-space:pre-wrap;background:var(--surface);padding:10px;border:1px solid var(--line);border-radius:10px;font-size:12px;overflow-x:auto}

footer{margin-top:34px;text-align:center;color:var(--muted);font-size:12px}
#up{position:fixed;right:16px;bottom:16px;display:none;box-shadow:var(--shadow)}
.hide{display:none!important}

@media (max-width:640px){
 .hero{padding:20px 18px;border-radius:16px}
 .card{padding:14px 14px 12px 18px}
 .card .top{flex-direction:column;gap:6px}
 .pills{justify-content:flex-start}
 .brand span{display:none}
 .toolbar{position:static}
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
  function paint(){if(tb)tb.textContent=isDark()?'\\u2600':'\\u263E'}
  if(tb)tb.addEventListener('click',function(){var n=isDark()?'light':'dark';root.setAttribute('data-theme',n);store('tema',n);paint()});
  paint();

  var q=document.getElementById('q'),cards=[].slice.call(document.querySelectorAll('.card')),
      secs=[].slice.call(document.querySelectorAll('[data-sec]')),shown=document.getElementById('shown'),
      chips=[].slice.call(document.querySelectorAll('.chip')),mode='all',
      total=document.querySelectorAll('[data-sec] .card').length;
  function norm(s){return (s||'').toLocaleLowerCase('tr')}
  function ok(c){
    var d=c.getAttribute('data-days'),days=d===''||d===null?null:+d;
    if(mode==='new'&&c.getAttribute('data-new')!=='1')return false;
    if(mode==='soon'&&!(days!==null&&days<=7))return false;
    if(mode==='warn'&&c.getAttribute('data-warn')!=='1')return false;
    var t=norm(q&&q.value.trim());
    return !t||c.getAttribute('data-q').indexOf(t)>-1}
  function apply(){
    var n=0,active=mode!=='all'||(q&&q.value.trim());
    cards.forEach(function(c){var v=ok(c);c.classList.toggle('hide',!v);if(v&&c.closest('[data-sec]'))n++});
    secs.forEach(function(s){
      var all=s.querySelectorAll('.card').length;if(!all)return;
      var vis=s.querySelectorAll('.card:not(.hide)').length,k=s.querySelector('.count');
      if(k)k.textContent=active?vis+' / '+all:all;
      s.classList.toggle('hide',active&&!vis)});
    if(shown)shown.textContent=active?n+' / '+total+' ilan':total+' ilan'}
  if(q)q.addEventListener('input',apply);
  chips.forEach(function(b){b.addEventListener('click',function(){
    mode=b.getAttribute('data-mode');
    chips.forEach(function(x){x.setAttribute('aria-pressed',x===b?'true':'false')});apply()})});
  document.addEventListener('keydown',function(e){
    if(e.key==='/'&&q&&document.activeElement!==q&&!/input|textarea/i.test(document.activeElement.tagName)){e.preventDefault();q.focus()}
    if(e.key==='Escape'&&q&&document.activeElement===q){q.value='';apply();q.blur()}});
  apply();

  var up=document.getElementById('up');
  if(up){window.addEventListener('scroll',function(){up.style.display=window.scrollY>600?'block':'none'},{passive:true});
    up.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'})})}
})();
"""


def esc(x):
    return htmllib.escape(str(x)) if x not in (None, "") else ""


def stat(value, label, tone=""):
    return f'<div class="stat {tone}"><b>{esc(value)}</b><span>{esc(label)}</span></div>'


def stats(items, small=False):
    """items: [(değer, etiket, ton)] — ton: ok | bad | warn | brand | ''."""
    return f'<div class="stats{" small" if small else ""}">' + "".join(stat(*i) for i in items) + "</div>"


def panel(summary, inner, open_=False):
    """Katlanır panel. summary ve inner hazır HTML'dir (çağıran kaçışlar)."""
    return (f'<details class="panel"{" open" if open_ else ""}><summary>{summary}</summary>'
            f'<div class="panel-b">{inner}</div></details>')


def table(head, rows_html):
    th = "".join(f"<th>{h}</th>" for h in head)
    return f'<div class="tbl"><table><thead><tr>{th}</tr></thead><tbody>{rows_html}</tbody></table></div>'


def toolbar(sections):
    """Arama kutusu, filtre düğmeleri ve bölüm içindekiler. sections: [(id, etiket)]."""
    toc = "".join(f'<a href="#{esc(i)}">{esc(t)}</a>' for i, t in sections)
    return ('<div class="toolbar"><div class="search">'
            '<input id="q" type="search" placeholder="Ara: kurum, başlık, şehir…  ( / )" autocomplete="off" '
            'aria-label="İlanlarda ara"><span class="shown" id="shown"></span></div>'
            '<div class="chips" role="group" aria-label="Filtre">'
            '<button class="chip" data-mode="all" aria-pressed="true">Tümü</button>'
            '<button class="chip" data-mode="new" aria-pressed="false">🆕 Bugün eklenen</button>'
            '<button class="chip" data-mode="soon" aria-pressed="false">⏰ 7 gün içinde bitenler</button>'
            '<button class="chip" data-mode="warn" aria-pressed="false">⚠ Dikkat gerekenler</button></div>'
            + (f'<div class="toc">{toc}</div>' if toc else "") + '</div>')


def section(sec_id, title, n, inner):
    return (f'<section class="sec" id="{esc(sec_id)}" data-sec><div class="sec-h"><h2>{title}</h2>'
            f'<span class="count">{n}</span></div>{inner}</section>')


def page(title, body, active="index", hero="", updated=""):
    """Tam sayfa kabuğu. active: index | onlisans | debug."""
    def tab(href, key, label):
        return f'<a href="{href}"{" class=on" if active == key else ""}>{label}</a>'
    tabs = tab("index.html", "index", "🎯 Ana rapor") + tab("onlisans.html", "onlisans", "♿ Önlisans &amp; Engelli")
    if active == "debug":
        tabs += tab("debug.html", "debug", "🛠 Tanı")
    return (f'<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="color-scheme" content="light dark">'
            f'<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 32 32%27%3E'
            f'%3Crect width=%2732%27 height=%2732%27 rx=%277%27 fill=%27%233b5bdb%27/%3E%3Cpath d=%27M9 10h14M9 16h14M9 22h9%27 '
            f'stroke=%27white%27 stroke-width=%273%27 stroke-linecap=%27round%27/%3E%3C/svg%3E">'
            f'<title>{esc(title)}</title><style>{STYLE}</style></head><body>'
            f'<header class="topbar"><div class="in"><div class="brand">📋<span> Kamu İlan Takip</span></div>'
            f'<nav class="tabs">{tabs}</nav>'
            f'<button class="iconbtn" id="tema" aria-label="Tema değiştir" title="Açık/koyu tema"></button></div></header>'
            f'<main class="wrap">{hero}{body}'
            f'<footer>Son güncelleme: {esc(updated)} · veriler resmî kaynaklardan otomatik toplanır; '
            f'başvurmadan önce ilanın aslını doğrulayın.</footer></main>'
            f'<button class="iconbtn" id="up" aria-label="Yukarı çık" title="Yukarı">↑</button>'
            f'<script>{SCRIPT}</script></body></html>')


def hero(title, line="", sub=""):
    return (f'<section class="hero"><h1>{title}</h1>'
            + (f'<p>{line}</p>' if line else "") + (f'<p class="sub">{sub}</p>' if sub else "") + "</section>")

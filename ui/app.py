"""Chakravyuh analyst console.   streamlit run ui/app.py"""
import glob, json, os, re, sys, html
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Chakravyuh · fraud investigator", page_icon="🌀", layout="wide", initial_sidebar_state="collapsed")

# ------------------------------------------------------------------ palette
SAFFRON, MARIGOLD, KUMKUM, TULSI, INK, SAND, CREAMY = "#FF7A00", "#FFC21A", "#D7263D", "#1FA75A", "#3B0D22", "#F3E3C3", "#FFF4DC"
STATE = {"fraud": KUMKUM, "legitimate": TULSI, "uncertain": MARIGOLD}

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Yatra+One&family=Hind:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap');
html, body, [class*="css"], .stMarkdown, p, li, label {{ font-family: 'Hind', system-ui, sans-serif; color: {INK}; }}
.stApp {{ background: #FFFFFF; }}
.block-container {{ padding-top: 2rem; max-width: 1180px; }}
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"] {{ display:none; }}
a, a:visited, .stMarkdown a, .foot a {{ color: #B34700 !important; }}
h1, h2, h3 {{ font-family: 'Hind', sans-serif; font-weight: 700; color: {INK}; letter-spacing: -0.01em; }}
.mono {{ font-family: 'JetBrains Mono', monospace; font-size: 0.86em; }}
.brand {{ display:flex; align-items:center; gap:0.7rem; margin-bottom:0.2rem; }}
.brand .name {{ font-family:'Yatra One', 'Hind', sans-serif; font-size:2.1rem; color:{INK}; line-height:1; }}
.brand .tag {{ font-size:0.98rem; color:#7A4A2A; }}
.nav a {{ display:inline-block; margin-right:0.4rem; padding:0.35rem 0.9rem; border-radius:999px; text-decoration:none; font-weight:600;
          background:{CREAMY}; color:{INK}; border:1.5px solid transparent; }}
.nav a.on {{ background:{SAFFRON}; color:#fff; }}
.nav a:focus-visible, .tile:focus-visible {{ outline:3px solid {MARIGOLD}; outline-offset:2px; }}
.trigger {{ font-size:1.15rem; line-height:1.5; max-width:70ch; margin:0.2rem 0 1rem; }}
.verdict {{ border-radius:18px; padding:1.1rem 1.3rem; color:#fff; }}
.verdict .big {{ font-family:'Yatra One', 'Hind', sans-serif; font-size:2.4rem; line-height:1.05; }}
.verdict .line {{ font-size:1.12rem; margin-top:0.3rem; opacity:0.97; }}
.facts {{ display:grid; grid-template-columns: repeat(3, 1fr); gap:0.6rem; margin-top:0.9rem; }}
.fact {{ background:{CREAMY}; border-radius:12px; padding:0.6rem 0.8rem; }}
.fact .v {{ font-size:1.35rem; font-weight:700; }}
.fact .k {{ font-size:0.85rem; color:#7A4A2A; }}
.bar {{ position:relative; height:14px; border-radius:999px; margin:1.1rem 0 0.3rem;
        background: linear-gradient(90deg, {TULSI} 0%, {MARIGOLD} 50%, {KUMKUM} 100%); }}
.pin {{ position:absolute; top:-5px; width:24px; height:24px; border-radius:50%; transform:translateX(-50%); border:3px solid {INK}; }}
.pin.before {{ background:#fff; }} .pin.after {{ background:{INK}; }}
.barlabels {{ display:flex; justify-content:space-between; font-size:0.82rem; color:#7A4A2A; }}
.barnote {{ font-size:0.95rem; margin-top:0.2rem; }}
.ring-legend div {{ display:flex; gap:0.6rem; align-items:flex-start; margin:0.45rem 0; }}
.dot {{ flex:0 0 14px; height:14px; border-radius:50%; margin-top:0.25rem; }}
.step {{ border-left:5px solid {SAND}; padding:0.5rem 0.9rem; margin:0.4rem 0; background:#FFFCF5; border-radius:0 12px 12px 0; }}
.act {{ display:flex; justify-content:space-between; gap:0.8rem; align-items:center; padding:0.55rem 0.9rem; margin:0.35rem 0;
        border-radius:12px; background:{CREAMY}; }}
.act .what {{ font-weight:600; font-size:1.02rem; }}
.act .why {{ font-size:0.86rem; color:#7A4A2A; }}
.pill {{ white-space:nowrap; font-size:0.8rem; font-weight:700; padding:0.2rem 0.65rem; border-radius:999px; }}
.pill.auto {{ background:#DDF5E6; color:#11643A; }} .pill.L1 {{ background:#FFE9B0; color:#7A4A00; }} .pill.L2 {{ background:#FBD3D8; color:#8A1022; }}
.ev {{ padding:0.55rem 0.9rem; margin:0.35rem 0; border-radius:12px; background:#FFFCF5; border-left:6px solid {SAND}; }}
.ev.f {{ border-left-color:{KUMKUM}; }} .ev.l {{ border-left-color:{TULSI}; }}
.ev .src {{ font-size:0.8rem; color:#8A6A55; margin-top:0.2rem; }}
.grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap:0.8rem; }}
.tile {{ display:block; text-decoration:none !important; color:{INK} !important; background:#FFFCF5; border-radius:14px;
         padding:1.05rem 0.95rem 0.8rem; box-shadow: inset 0 7px 0 var(--band); }}
.tile:hover {{ background:{CREAMY}; }}
.tile .id {{ font-weight:700; font-size:1.05rem; }} .tile .v {{ font-family:'Yatra One', 'Hind', sans-serif; font-size:1.35rem; }}
.tile .d {{ font-size:0.88rem; color:#7A4A2A; }}
.count {{ font-family:'Yatra One', 'Hind', sans-serif; font-size:2.6rem; line-height:1; }}
.muted {{ color:#7A4A2A; font-size:0.9rem; }}
.tl {{ display:grid; grid-template-columns: 150px 1fr; gap:0.2rem 0.9rem; margin-top:0.4rem; }}
.tl .when {{ font-size:0.82rem; color:#8A6A55; padding-top:0.15rem; }}
.tl .what {{ border-left:3px solid {SAND}; padding:0 0 0.7rem 0.9rem; position:relative; }}
.tl .what:before {{ content:""; position:absolute; left:-8px; top:0.3rem; width:13px; height:13px; border-radius:50%; background:var(--c); }}
.chip {{ display:inline-block; font-size:0.78rem; font-weight:600; padding:0.08rem 0.5rem; border-radius:999px; margin:0.15rem 0.25rem 0 0; }}
.chip.done {{ background:#DDF5E6; color:#11643A; }} .chip.wait {{ background:#FFE9B0; color:#7A4A00; }}
.flow {{ display:grid; grid-template-columns: 1fr 0.8fr 1fr; gap:0.8rem; }}
@media (max-width: 760px) {{ .fact .v {{ font-size:1.05rem; }} .ringwrap svg {{ max-width:250px !important; }} .flow {{ grid-template-columns: 1fr; }} .verdict .big {{ font-size:1.9rem; }} }}
@media (prefers-reduced-motion: reduce) {{ * {{ animation:none !important; transition:none !important; }} }}
[data-testid="stSidebar"] {{ background:{CREAMY}; }}
/* ---------- hero ---------- */
.hero {{ position:relative; overflow:hidden; border-radius:26px; padding:2.4rem 2.4rem 2.2rem; margin:0.2rem 0 1.6rem;
         background: radial-gradient(circle at 82% 38%, rgba(255,122,0,0.38), transparent 42%),
                     radial-gradient(circle at 10% 110%, rgba(215,38,61,0.45), transparent 45%),
                     linear-gradient(135deg, #2A0A1C 0%, #1A0510 100%); color:#FFF4DC; }}
.hero .wrap {{ display:grid; grid-template-columns: 1.25fr 1fr; gap:1.5rem; align-items:center; }}
.hero .eyebrow {{ font-size:0.82rem; letter-spacing:0.08em; color:{MARIGOLD}; font-weight:700; }}
.hero h1.title {{ font-family:'Yatra One', 'Hind', sans-serif; font-size:4.1rem; line-height:1; margin:0.35rem 0 0.6rem; color:#FFF4DC; font-weight:400; }}
.hero .lede {{ font-size:1.22rem; line-height:1.5; max-width:34ch; color:#FFE9C7; }}
.hero .chips {{ margin-top:1rem; }}
.hero .chips span {{ display:inline-block; margin:0 0.35rem 0.4rem 0; padding:0.22rem 0.7rem; border-radius:999px; font-size:0.84rem;
                     border:1px solid rgba(255,194,26,0.45); color:#FFE9B0; background:rgba(255,194,26,0.08); }}
.hero .cta {{ margin-top:1.3rem; display:flex; gap:0.7rem; flex-wrap:wrap; }}
.hero .cta a {{ text-decoration:none; font-weight:700; padding:0.62rem 1.15rem; border-radius:999px; }}
.hero .cta a.primary {{ background:{SAFFRON}; color:#fff !important; box-shadow:0 8px 24px rgba(255,122,0,0.35); }}
.hero .cta a.ghost {{ border:1.5px solid rgba(255,244,220,0.5); color:#FFF4DC !important; }}
.hero .nav a {{ background:rgba(255,244,220,0.1); color:#FFF4DC; }}
.hero .nav a.on {{ background:{SAFFRON}; color:#fff; }}
.hero .toprow {{ display:flex; justify-content:space-between; align-items:center; gap:1rem; margin-bottom:1.6rem; flex-wrap:wrap; }}
.hero .mark {{ display:flex; align-items:center; gap:0.55rem; font-family:'Yatra One','Hind',sans-serif; font-size:1.35rem; color:#FFF4DC; }}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
@keyframes spinr {{ to {{ transform: rotate(-360deg); }} }}
@keyframes pulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:0.45; }} }}
@keyframes draw {{ from {{ stroke-dashoffset: 900; opacity:0; }} to {{ stroke-dashoffset: 0; opacity:1; }} }}
.spin-a {{ transform-box:view-box; transform-origin:50% 50%; animation: spin 70s linear infinite; }}
.spin-b {{ transform-box:view-box; transform-origin:50% 50%; animation: spinr 50s linear infinite; }}
.spin-c {{ transform-box:view-box; transform-origin:50% 50%; animation: spin 36s linear infinite; }}
.spin-d {{ transform-box:view-box; transform-origin:50% 50%; animation: spinr 28s linear infinite; }}
.pulse {{ animation: pulse 2.6s ease-in-out infinite; }}
.drawn circle.ring {{ animation: draw 1.4s ease-out both; }}
.drawn circle.ring:nth-child(2) {{ animation-delay:0.15s; }} .drawn circle.ring:nth-child(3) {{ animation-delay:0.3s; }}
.drawn circle.ring:nth-child(4) {{ animation-delay:0.45s; }}
/* ---------- stats + highlights ---------- */
.stats {{ display:grid; grid-template-columns: repeat(4, 1fr); gap:0.9rem; margin-bottom:1.4rem; }}
.stat {{ border-radius:18px; padding:1rem 1.1rem; background:#FFFCF5; border:1.5px solid {SAND}; }}
.stat .n {{ font-family:'Yatra One','Hind',sans-serif; font-size:2.7rem; line-height:1; }}
.stat .l {{ font-weight:600; margin-top:0.2rem; }} .stat .s {{ font-size:0.85rem; color:#7A4A2A; }}
.hl {{ display:grid; grid-template-columns: repeat(3, 1fr); gap:0.9rem; margin:0.4rem 0 1.8rem; }}
.hl a {{ display:block; text-decoration:none !important; color:{INK} !important; border-radius:18px; padding:1.1rem 1.2rem;
         background:linear-gradient(160deg, #FFF4DC 0%, #FFFFFF 70%); border:1.5px solid #F6D9A8; transition: transform .15s ease, box-shadow .15s ease; }}
.hl a:hover {{ transform: translateY(-3px); box-shadow:0 12px 28px rgba(122,74,42,0.14); }}
.hl .big {{ font-family:'Yatra One','Hind',sans-serif; font-size:2rem; color:{SAFFRON}; line-height:1.1; }}
.hl .h {{ font-weight:700; font-size:1.08rem; margin-top:0.25rem; }} .hl .t {{ font-size:0.93rem; color:#7A4A2A; margin-top:0.2rem; }}
.sechead {{ display:flex; justify-content:space-between; align-items:flex-end; gap:1rem; flex-wrap:wrap; margin:0.4rem 0 0.8rem; }}
.sechead h2 {{ margin:0; }}
.filters a {{ display:inline-block; margin-left:0.35rem; padding:0.3rem 0.85rem; border-radius:999px; text-decoration:none; font-weight:600; font-size:0.9rem;
              background:{CREAMY}; color:{INK}; }}
.filters a.on {{ color:#fff; }}
/* ---------- richer tiles ---------- */
.tile {{ transition: transform .15s ease, box-shadow .15s ease; position:relative; }}
.tile:hover {{ transform: translateY(-3px); box-shadow:0 12px 26px rgba(59,13,34,0.12); background:#FFFCF5; }}
.tile .glyph {{ position:absolute; right:0.8rem; top:1rem; }}
.tile .src {{ font-size:0.84rem; color:#8A6A55; margin-top:-0.1rem; }}
.stButton button {{ border-radius:999px; border:1.5px solid {SAFFRON}; color:#B34700; background:#fff; font-weight:700; }}
.stButton button:hover {{ background:{SAFFRON}; color:#fff; border-color:{SAFFRON}; }}
.stButton button:focus:not(:active) {{ border-color:{SAFFRON}; color:#B34700; box-shadow:0 0 0 3px rgba(255,194,26,0.4); }}
.tile .mini {{ height:6px; border-radius:999px; margin-top:0.6rem; position:relative;
               background: linear-gradient(90deg, {TULSI}, {MARIGOLD}, {KUMKUM}); opacity:0.9; }}
.tile .mini i {{ position:absolute; top:-4px; width:14px; height:14px; border-radius:50%; background:#fff; border:3px solid {INK}; transform:translateX(-50%); }}
.foot {{ margin-top:2.4rem; padding:1.2rem 0 0.4rem; border-top:1.5px solid {SAND}; display:flex; justify-content:space-between; gap:1rem; flex-wrap:wrap; font-size:0.9rem; color:#7A4A2A; }}
/* ---------- case hero ---------- */
.casehero {{ border-radius:22px; padding:1.3rem 1.5rem; margin-bottom:1rem; color:#FFF4DC;
             background: radial-gradient(circle at 95% 10%, rgba(255,122,0,0.3), transparent 40%), linear-gradient(135deg, #2A0A1C, #1A0510); }}
.casehero .cid {{ font-family:'Yatra One','Hind',sans-serif; font-size:2.4rem; line-height:1; }}
.casehero .meta {{ color:#FFE9B0; font-size:0.92rem; margin-top:0.35rem; }}
.casehero .trigger {{ color:#FFF4DC; margin:0.6rem 0 0; font-size:1.12rem; }}
.brandbar {{ display:flex; justify-content:space-between; align-items:center; gap:1rem; flex-wrap:wrap; margin-bottom:1rem; }}
@media (max-width: 760px) {{ .hero .wrap {{ grid-template-columns: 1fr; }} .hero h1.title {{ font-size:2.9rem; }} .hero {{ padding:1.6rem 1.3rem; }}
   .stats {{ grid-template-columns: 1fr 1fr; }} .hl {{ grid-template-columns: 1fr; }} .hero svg {{ max-width:230px !important; }} }}

</style>""", unsafe_allow_html=True)

ACTION = {"ALLOW_TRANSACTION": "Let the payment through", "DECLINE_TRANSACTION": "Decline the payment", "BLOCK_CARD": "Block the card",
          "BLOCK_ALL_CARDS": "Block all of the customer's cards", "VERIFY_WITH_CUSTOMER": "Check with the customer",
          "STEP_UP_AUTH": "Ask for a one-time passcode", "CREATE_CASE": "Open a case", "FILE_REPORT": "File a suspicious activity report",
          "MONITOR_CARD": "Keep watching the card", "MONITOR_CONNECTED_CARDS": "Watch the linked cards", "WARN_CUSTOMER": "Warn the customer",
          "ESCALATE_TO_ANALYST": "Hand over to a fraud analyst", "GENERATE_REPORT": "Generate a report", "CLOSE_NO_FRAUD": "Close it: not fraud"}
ROUTE = {"auto": "agent can do it", "L1": "team lead approves", "L2": "fraud manager approves"}
PATTERN = {"card_testing": "Card testing", "card_not_present_fraud": "Online use of a stolen card number", "card_not_present_new_device": "Stolen card number used from a new device",
           "out_of_region_use": "Card used far from home", "account_takeover": "Account takeover", "undocumented": "A new scheme, not in the playbook", "none": "No fraud"}
TRIGGER = {"risk_score": "The bank's model flagged a payment", "customer_report": "A customer disputed a payment", "analyst_request": "An analyst asked for a review"}
REQUEST = {"customer_validation": "Asked the customer", "step_up_auth": "Sent a one-time passcode", "analyst_info": "Asked a fraud analyst"}
RINGS = [("The payment itself", {"amount", "model_score", "card_testing", "structuring", "replay", "burst"}),
         ("The cardholder's own habits", {"habit", "recurrence", "region"}),
         ("Device and other cards", {"device", "shared_origin", "structuring_network", "proxy", "email", "graph_algorithm"}),
         ("The bank's past cases", {"fingerprint_history", "device_memory"})]

def esc(x): return html.escape(str(x))
def money(x): return f"${x:,.2f}"

@st.cache_data(ttl=30)
def load_all():
    out = {}
    for f in sorted(glob.glob("cases/HHG-*.json")):
        a = json.load(open(f)); t = f"traces/{a['case_id']}.json"
        out[a["case_id"]] = (a, json.load(open(t)) if os.path.exists(t) else {"trace": []})
    return out

pack = pd.read_csv("data/local/case_pack.csv").set_index("case_id")
data = load_all()
qp = st.query_params
view = qp.get("view", "overview")
cid = qp.get("case", "HHG-001")
if cid not in pack.index: cid = "HHG-001"

SPIRAL = f"""<svg width="46" height="46" viewBox="0 0 46 46" aria-hidden="true">
<circle cx="23" cy="23" r="20" fill="none" stroke="{SAFFRON}" stroke-width="4" stroke-dasharray="26 6"/>
<circle cx="23" cy="23" r="13" fill="none" stroke="{KUMKUM}" stroke-width="4" stroke-dasharray="16 5" transform="rotate(40 23 23)"/>
<circle cx="23" cy="23" r="6" fill="none" stroke="{MARIGOLD}" stroke-width="4" stroke-dasharray="8 4" transform="rotate(80 23 23)"/>
<circle cx="23" cy="23" r="2.2" fill="{INK}"/></svg>"""

def navlinks(active):
    links = [("overview", "All 20 cases"), ("case", "Case file"), ("how", "How it works")]
    return "".join(f'<a class="{"on" if k == active else ""}" href="?view={k}&case={cid}" target="_self">{t}</a>' for k, t in links)

def header(active):
    st.markdown(f'<div class="brandbar"><div class="brand">{SPIRAL}<div><div class="name">Chakravyuh</div>'
                f'<div class="tag">An AI fraud investigator on TigerGraph, built for Hacker House Goa 2026</div></div></div>'
                f'<div class="nav">{navlinks(active)}</div></div>', unsafe_allow_html=True)

def hero_spiral(size=340):
    rings = [(150, SAFFRON, "70 22", 16, "spin-a"), (118, MARIGOLD, "52 18", 14, "spin-b"), (86, KUMKUM, "40 14", 14, "spin-c"),
             (56, "#FFE9C7", "26 12", 12, "spin-d")]
    out = [f'<svg viewBox="0 0 340 340" width="100%" style="max-width:{size}px" aria-hidden="true">']
    for r, c, d, w, cls in rings:
        out.append(f'<g class="{cls}"><circle cx="170" cy="170" r="{r}" fill="none" stroke="{c}" stroke-width="{w}" stroke-linecap="round" stroke-dasharray="{d}"/></g>')
    out.append(f'<circle class="pulse" cx="170" cy="170" r="22" fill="{KUMKUM}"/><circle cx="170" cy="170" r="9" fill="#FFF4DC"/></svg>')
    return "".join(out)

def mini_glyph(states):
    col = {"fraud": KUMKUM, "legit": TULSI, "mixed": MARIGOLD, "consulted": "#E9B97A", "none": SAND}
    out = ['<svg class="glyph" width="46" height="46" viewBox="0 0 46 46" aria-hidden="true">']
    for i, ((_, stt, _), r) in enumerate(zip(states, [6, 11, 16, 21])):
        out.append(f'<circle cx="23" cy="23" r="{r}" fill="none" stroke="{col[stt]}" stroke-width="3.6" stroke-linecap="round" '
                   f'stroke-dasharray="{r*1.4:.0f} {r*0.5:.0f}" transform="rotate({i*40} 23 23)"/>')
    out.append('</svg>')
    return "".join(out)

def ring_states(trace, case):
    ev = [x for x in trace if x.get("kind") == "step" and x.get("step") == "evidence"]
    out = []
    for name, fams in RINGS:
        items = [x for x in ev if x.get("family") in fams]
        ga = [x for x in items if x.get("family") == "graph_algorithm"]      # weight 0: shown, never scored
        items = [x for x in items if x.get("family") != "graph_algorithm"]
        if ga and not items: out.append((name, "consulted", ga_short(ga[0]["msg"]))); continue
        score = sum(x.get("weight", 0) for x in items)
        if name == "The bank's past cases" and not items and case["similar_prior_cases"]:
            out.append((name, "consulted", f"Checked {len(case['similar_prior_cases'])} similar past case(s); nothing decisive")); continue
        if not items: out.append((name, "none", "Nothing unusual here")); continue
        if {x.get("family") for x in items} == {"model_score"}:
            out.append((name, "mixed", "Only the model's score, which is a reason to look, not proof")); continue
        top = max(items, key=lambda x: abs(x.get("weight", 0)) if x.get("family") != "model_score" else 0)["msg"].split(" — bank memory")[0]
        top = re.sub(r" \([^()]*\|[^()]*\)", "", top)
        top = top if len(top) < 170 else top[:167].rsplit(" ", 1)[0] + "…"
        out.append((name, "fraud" if score > 0.4 else "legit" if score < -0.4 else "mixed", top))
    return out

def ga_short(msg):
    """One line for the tg_wcc evidence, e.g. 'TigerGraph WCC: a connected component of 28 cards on 1 rare device'."""
    m = re.search(r"component of (\d+) cards \((\d+) card fingerprints, (\d+) device", msg)
    if m: return f"TigerGraph WCC (tg_wcc): a connected component of {m.group(1)} cards sharing {m.group(3)} rare device profile{'s' if m.group(3) != '1' else ''}"
    m = re.search(r"cluster \(([\d,]+) card fingerprints across ([\d,]+)", msg)
    if m: return f"TigerGraph WCC (tg_wcc): part of a large, loose cluster ({m.group(1)} card fingerprints, {m.group(2)} devices), too big to be one ring"
    return "TigerGraph WCC (tg_wcc) result"

def ring_svg(states, amount):
    col = {"fraud": KUMKUM, "legit": TULSI, "mixed": MARIGOLD, "consulted": "#E9B97A", "none": SAND}
    radii = [52, 84, 116, 148]; dash = ["44 10", "64 12", "84 14", "104 16"]
    parts = ['<svg class="drawn" viewBox="0 0 320 320" width="100%" style="max-width:340px" role="img" aria-label="Investigation rings">']
    for i, ((_, stt, _), r) in enumerate(zip(states, radii)):
        parts.append(f'<circle class="ring" cx="160" cy="160" r="{r}" fill="none" stroke="{col[stt]}" stroke-width="18" stroke-linecap="round" '
                     f'stroke-dasharray="{dash[i]}" transform="rotate({i*37} 160 160)"/>')
    parts.append(f'<circle cx="160" cy="160" r="30" fill="{INK}"/><text x="160" y="166" text-anchor="middle" fill="#fff" '
                 f'font-family="Hind, sans-serif" font-weight="700" font-size="15">{esc(money(amount)[:-3])}</text></svg>')
    return "".join(parts)

# ================================================================== OVERVIEW
REPO = "https://github.com/Tanmay2006-Tech/chakravyuh-fraud-agent"

if view == "overview":
    df = pd.DataFrame([{"id": k, **{"verdict": a["case"]["verdict"], "p": a["case"]["fraud_probability"], "exp": a["case"]["exposure_usd"],
                                    "sar": a["sar"]["file"], "pattern": a["case"]["pattern"], "changed": a["next_best_actions"]["what_changed"] != "nothing",
                                    "asked": bool(a["evidence_requests"])}}
                       for k, (a, _) in data.items()])
    nf, nl, nu, ns = (df.verdict == "fraud").sum(), (df.verdict == "legitimate").sum(), (df.verdict == "uncertain").sum(), int(df.sar.sum())

    # ---- hero ----
    st.markdown(
        f'<div class="hero"><div class="toprow"><div class="mark">{SPIRAL} Chakravyuh</div><div class="nav">{navlinks("overview")}</div></div>'
        f'<div class="wrap"><div>'
        f'<div class="eyebrow">HACKER HOUSE GOA 2026 · TIGERGRAPH AGENTIC FRAUD INVESTIGATION</div>'
        f'<h1 class="title">Chakravyuh</h1>'
        f'<div class="lede">An AI fraud investigator that walks the graph ring by ring, traps coordinated fraud, and knows when <b>not</b> to block a genuine customer.</div>'
        f'<div class="chips"><span>TigerGraph Savanna</span><span>MCP</span><span>GraphRAG · TigerVector</span><span>LangGraph agent</span><span>Policy-bound actions</span></div>'
        f'<div class="cta"><a class="primary" href="?view=case&case=HHG-006" target="_self">See it catch a hidden scheme →</a>'
        f'<a class="ghost" href="?view=how&case={cid}" target="_self">How it works</a></div>'
        f'</div><div style="text-align:center">{hero_spiral(360)}</div></div></div>', unsafe_allow_html=True)

    # ---- stats ----
    exp_total = df[df.verdict == "fraud"].exp.sum()
    st.markdown(
        '<div class="stats">'
        f'<div class="stat"><div class="n" style="color:{KUMKUM}">{nf}</div><div class="l">fraud caught</div><div class="s">{money(exp_total)} at risk, blocked or held</div></div>'
        f'<div class="stat"><div class="n" style="color:{TULSI}">{nl}</div><div class="l">genuine customers cleared</div><div class="s">no card blocked on a hunch</div></div>'
        f'<div class="stat"><div class="n" style="color:{MARIGOLD}">{nu}</div><div class="l">honestly unsure</div><div class="s">card watched, customer asked</div></div>'
        f'<div class="stat"><div class="n" style="color:{SAFFRON}">{ns}</div><div class="l">regulator reports</div><div class="s">only where policy requires one</div></div>'
        '</div>', unsafe_allow_html=True)

    # ---- highlights ----
    acc = ""
    if os.path.exists("docs/backtest.json") and os.path.exists("docs/backtest_uncalibrated.json"):
        b1, b0 = json.load(open("docs/backtest.json")), json.load(open("docs/backtest_uncalibrated.json"))
        acc = f"{b0['accuracy_on_decided']*100:.0f}% → {b1['accuracy_on_decided']*100:.0f}%"
    st.markdown(
        '<div class="hl">'
        '<a href="?view=case&case=HHG-006" target="_self"><div class="big">2 hidden schemes</div><div class="h">Found fraud nobody had documented</div>'
        '<div class="t">Purchases just under $500 on 12 cards, and one proxy device behind 50+ customers. Named, reported, linked cards watched.</div></a>'
        f'<a href="?view=how&case={cid}" target="_self"><div class="big">{acc or "Learns"}</div><div class="h">Learned from the bank\'s own history</div>'
        '<div class="t">Replaying past cases taught it that most "new device" alerts are just new phones. Accuracy on unseen cases rose.</div></a>'
        f'<a href="?view=case&case=HHG-010" target="_self"><div class="big">{int(df.asked.sum())} of 20</div><div class="h">Asked before it acted</div>'
        '<div class="t">When the evidence was thin it sent a passcode or asked the customer, and planned its move for every possible answer.</div></a>'
        '</div>', unsafe_allow_html=True)

    # ---- filters + tiles ----
    f = qp.get("f", "all")
    FILT = [("all", "All", INK), ("fraud", "Fraud", KUMKUM), ("legitimate", "Legitimate", TULSI), ("uncertain", "Not sure", MARIGOLD)]
    chips = "".join(f'<a class="{"on" if k == f else ""}" href="?view=overview&f={k}&case={cid}" target="_self" '
                    f'style="{"background:" + c + ";" if k == f else ""}">{t}</a>' for k, t, c in FILT)
    st.markdown(f'<div class="sechead"><h2>The twenty investigations</h2><div class="filters">{chips}</div></div>', unsafe_allow_html=True)
    tiles = []
    for r in df.itertuples():
        if f != "all" and r.verdict != f: continue
        a, tr = data[r.id]; trig = pack.loc[r.id, "trigger_type"]
        word = {"fraud": "Fraud", "legitimate": "Legitimate", "uncertain": "Not sure yet"}[r.verdict]
        source = {"risk_score": "Model alert", "customer_report": "Customer dispute", "analyst_request": "Analyst request"}[trig]
        if r.verdict == "fraud":
            line, extra = PATTERN[r.pattern], f"{money(r.exp)} at risk" + (", report filed" if r.sar else "")
        elif r.verdict == "legitimate":
            asked = a["evidence_requests"][0]["type"] if a["evidence_requests"] else None
            line = {"customer_validation": "Customer confirmed it", "step_up_auth": "Passcode confirmed it"}.get(asked, "Evidence clears it")
            extra = "No action against the customer"
        else:
            line, extra = "No reply yet from the customer", "Card under watch"
        glyph = mini_glyph(ring_states(tr["trace"], a["case"]))
        tiles.append(f'<a class="tile" href="?view=case&case={r.id}" target="_self" style="--band:{STATE[r.verdict]}">{glyph}'
                     f'<div class="id">{r.id}</div><div class="src">{source}</div><div class="v" style="color:{STATE[r.verdict]}">{word}</div>'
                     f'<div class="d">{esc(line)}</div><div class="d">{esc(extra)}</div>'
                     f'<div class="mini" title="chance of fraud {r.p:.0%}"><i style="left:{r.p*100:.0f}%"></i></div></a>')
    st.markdown(f'<div class="grid">{"".join(tiles)}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="foot"><span>Chakravyuh · built for Hacker House Goa 2026 on TigerGraph Savanna, MCP and GraphRAG</span>'
                f'<a href="{REPO}" target="_blank">Source code on GitHub</a></div>', unsafe_allow_html=True)
    st.stop()

# ================================================================== HOW IT WORKS
if view == "how":
    header("how")
    st.markdown("## How Chakravyuh investigates")
    steps = [("Start from the alert", "A model score, a customer complaint or an analyst request names one payment."),
             ("Walk the graph through TigerGraph", "Installed GSQL queries, called through the TigerGraph MCP server, pull the card's habits, the real cardholder behind it, the device and every other card that device touched."),
             ("Run a graph algorithm", "TigerGraph's weakly connected components (tg_wcc) links each card-in-use to the rare devices it used in November and December, so a ring of cards sharing devices shows up as one component. It is shown as context and never moves the score."),
             ("Remember", "Past cases on the same cardholder, device or card are pulled from the graph, and similar case notes are found by vector search."),
             ("Weigh the evidence", "Each finding moves a fraud probability up or down. The weights were checked against the bank's own history."),
             ("Ask when unsure", "If one weak signal is all there is, the agent checks with the customer or sends a one-time passcode before blocking anyone."),
             ("Act within policy", "Every action carries the rule behind it and who must approve it. The agent only does the safe ones itself."),
             ("Write it down", "The case, and a suspicious activity report when policy requires one, is written back into the graph so the next investigation can find it.")]
    for i, (t, d) in enumerate(steps, 1):
        st.markdown(f'<div class="step"><b>{i}. {t}</b><br>{d}</div>', unsafe_allow_html=True)
    st.markdown("### What the bank's history taught it")
    st.markdown('<p style="max-width:70ch">Replaying the closed cases showed that when the model fires on a payment from a new device, it is almost always a customer with a new phone. '
                'Learning from that history made the agent clearly better at telling real fraud from false alarms on cases it had never seen.</p>', unsafe_allow_html=True)
    got = [(n, json.load(open(p))) for n, p in [("Before learning from history", "docs/backtest_uncalibrated.json"), ("After learning from history", "docs/backtest.json")] if os.path.exists(p)]
    if got:
        c = st.columns(len(got))
        for col, (n, d) in zip(c, got):
            col.markdown(f'<div class="count" style="color:{SAFFRON}">{d["accuracy_on_decided"]*100:.0f}%</div><div class="muted">{n}: correct on {d["n"]} past cases '
                         f'(ranking quality {d["auc_p0"]:.2f})</div>', unsafe_allow_html=True)
    if os.path.exists("agent/calibration.json"):
        cal = json.load(open("agent/calibration.json"))
        names = {"device+": "New device", "email+": "Anonymous email domain", "amount+": "Unusually large amount", "proxy+": "Behind a proxy",
                 "device_memory-": "Device's past alerts were cleared", "fingerprint_history+": "Cardholder has confirmed fraud before",
                 "device-": "Known device", "region-": "Routine region", "region+": "New region"}
        rows = [{"Signal": names.get(k, k), "Past alerts with it": v["n_with"], "Fraud rate with it": f'{v["fraud_rate_with"]*100:.0f}%',
                 "Fraud rate without it": f'{v["fraud_rate_without"]*100:.0f}%'} for k, v in cal["evidence"].items()]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.stop()

# ================================================================== CASE FILE
header("case")
with st.sidebar:
    st.markdown("### Pick a case")
    pick = st.selectbox("Case", list(pack.index), index=list(pack.index).index(cid), label_visibility="collapsed")
    if pick != cid:
        st.query_params.update({"view": "case", "case": pick}); st.rerun()
if cid not in data:
    st.markdown("This case has not been run yet."); st.stop()
a, tr = data[cid]; k = a["case"]; row = pack.loc[cid]; n = a["next_best_actions"]
verdict = k["verdict"]; color = STATE[verdict]
opened = pd.Timestamp(row.opened_at)
st.markdown(f'<div class="casehero"><div class="cid">{cid}</div>'
            f'<div class="meta">{TRIGGER[row.trigger_type]} on card <span class="mono">{row.card_id}</span>, {opened:%d %b %Y}, payment <span class="mono">{row.flagged_txn_id}</span></div>'
            f'<div class="trigger">{esc(row.trigger_text)}</div></div>', unsafe_allow_html=True)

head = {"fraud": ("Fraud", "Block it and protect the customer."), "legitimate": ("Legitimate", "Let it through. No action against the customer."),
        "uncertain": ("Not sure yet", "Hold pending payments and keep watching the card.")}[verdict]
p0, p1 = tr.get("p0", k["fraud_probability"]), k["fraud_probability"]
left, right = st.columns([1.05, 1])
with left:
    st.markdown(f'<div class="verdict" style="background:{color}"><div class="big">{head[0]}</div><div class="line">{head[1]}</div>'
                f'<div class="line">{esc(PATTERN[k["pattern"]]) if verdict == "fraud" else ""}</div></div>', unsafe_allow_html=True)
    st.markdown(f'<div class="bar"><div class="pin before" style="left:{p0*100:.1f}%" title="before asking"></div>'
                f'<div class="pin after" style="left:{p1*100:.1f}%" title="final"></div></div>'
                f'<div class="barlabels"><span>clearly fine</span><span>clearly fraud</span></div>'
                f'<div class="barnote">Chance of fraud: <b>{p0:.0%}</b> after the first look (white dot), <b>{p1:.0%}</b> at the end (dark dot)</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="facts"><div class="fact"><div class="v">{money(k["exposure_usd"])}</div><div class="k">money at risk</div></div>'
                f'<div class="fact"><div class="v">{"Filed" if a["sar"]["file"] else "Not needed"}</div><div class="k">regulator report</div></div>'
                f'<div class="fact"><div class="v">{len(k["connected_card_ids"])}</div><div class="k">other cards linked</div></div></div>', unsafe_allow_html=True)
    st.markdown(f'<div class="step" style="border-left-color:{SAFFRON}; margin-top:0.9rem"><b>In short</b><br>{esc(k["summary"])}</div>', unsafe_allow_html=True)
with right:
    states = ring_states(tr["trace"], k)
    m = re.search(r"\$([\d,]+\.\d{2})", str(row.trigger_text))
    amt = float(m.group(1).replace(",", "")) if m else k["exposure_usd"]
    c1, c2 = st.columns([1, 1.15])
    c1.markdown('<div class="ringwrap">' + ring_svg(states, amt) + '</div>', unsafe_allow_html=True)
    col = {"fraud": KUMKUM, "legit": TULSI, "mixed": MARIGOLD, "consulted": "#E9B97A", "none": SAND}
    word = {"fraud": "points to fraud", "legit": "leans genuine", "mixed": "mixed signals", "consulted": "checked", "none": "nothing unusual"}
    c2.markdown('<div class="muted">The investigation rings, from the payment in the centre outward</div><div class="ring-legend">' + "".join(
        f'<div><span class="dot" style="background:{col[s]}"></span><span><b>{i}. {nm}</b>, {word[s]}<br><span class="muted">{esc(t)}</span></span></div>'
        for i, (nm, s, t) in enumerate(states, 1)) + '</div>', unsafe_allow_html=True)
    ga = [x["msg"] for x in tr["trace"] if x.get("kind") == "step" and x.get("family") == "graph_algorithm"]
    if ga:
        c2.markdown(f'<div class="muted" style="margin-top:0.4rem"><b>Graph algorithm.</b> {esc(ga_short(ga[0]))}. '
                    'Shown for context; it does not change the fraud chance.</div>', unsafe_allow_html=True)

st.markdown("### What to do now")
st.markdown('<p class="muted">The agent carries out the safe actions itself through the bank\'s systems. Anything that blocks a card or reports a customer waits for a person to approve it.</p>', unsafe_allow_html=True)
if "approvals" not in st.session_state: st.session_state.approvals = {}
for i, x in enumerate(n["final"]):
    key = f"{cid}:{x['action']}"
    state = st.session_state.approvals.get(key)
    pill = ('<span class="pill auto">done by the agent</span>' if x["route"] == "auto" else f'<span class="pill {x["route"]}">{ROUTE[x["route"]]}</span>') if x["route"] == "auto" or not state else \
           f'<span class="pill {"auto" if state == "approved" else "L2"}">{"approved" if state == "approved" else "rejected"}</span>'
    cols = st.columns([6, 1, 1]) if x["route"] != "auto" and not state else [st.container()]
    cols[0].markdown(f'<div class="act"><div><div class="what">{ACTION[x["action"]]}</div><div class="why">{esc(x["reason"])}</div></div>{pill}</div>', unsafe_allow_html=True)
    if len(cols) == 3:
        if cols[1].button("Approve", key=f"ap{key}"): st.session_state.approvals[key] = "approved"; st.rerun()
        if cols[2].button("Reject", key=f"rj{key}"): st.session_state.approvals[key] = "rejected"; st.rerun()

st.markdown("### How the decision was reached")
req = a["evidence_requests"]
first = "".join(f'<div>{ACTION[x["action"]]}</div>' for x in n["initial"])
ask = (f'<b>{REQUEST[req[0]["type"]]}</b><br>{esc(req[0]["assumed_response"])}' if req else "<b>No need to ask</b><br>The evidence was already clear enough to act.")
last = "".join(f'<div>{ACTION[x["action"]]}</div>' for x in n["final"])
st.markdown(f'<div class="flow"><div class="step"><b>First recommendation</b>{first}</div><div class="step" style="border-left-color:{MARIGOLD}">{ask}</div>'
            f'<div class="step" style="border-left-color:{color}"><b>Final recommendation</b>{last}</div></div>', unsafe_allow_html=True)
if n["what_changed"] != "nothing":
    st.markdown(f'<p style="max-width:80ch; margin-top:0.5rem"><b>What changed:</b> {esc(n["what_changed"])}</p>', unsafe_allow_html=True)
br = tr.get("branches") or []
if br:
    st.markdown("#### The plan for every possible answer")
    st.markdown('<p class="muted">Before asking, the agent already knows what it will do with each reply. The highlighted one is the reply assumed for this case.</p>', unsafe_allow_html=True)
    bc = st.columns(len(br))
    for col, b in zip(bc, br):
        vcol = STATE[b["verdict"]]
        acts = "".join(f'<div>{ACTION[x["action"]]}</div>' for x in b["actions"])
        col.markdown(f'<div class="step" style="border-left-color:{vcol};{"background:" + CREAMY + ";" if b["chosen"] else ""}"><b>If: {esc(b["if"].lower())}</b><br>'
                     f'<span style="color:{vcol};font-weight:700">{ {"fraud": "Fraud", "legitimate": "Legitimate", "uncertain": "Not sure yet"}[b["verdict"]] }</span>'
                     f'{" and a regulator report" if b["report"] else ""}{acts}</div>', unsafe_allow_html=True)
st.markdown(f'<p class="muted" style="max-width:80ch"><b>Why it stopped:</b> {esc(a["stop_reason"])}</p>', unsafe_allow_html=True)

tl = tr.get("timeline") or []
if tl:
    st.markdown("### Case record")
    st.markdown(f'<p class="muted">How investigation INV-{cid} progressed. The case is written to TigerGraph when it opens and updated when it closes.</p>', unsafe_allow_html=True)
    STC = {"open": SAFFRON, "investigating": MARIGOLD, "awaiting_evidence": MARIGOLD, "decided": color, "closed_fraud": KUMKUM,
           "closed_legitimate": TULSI, "escalated": SAFFRON}
    SLABEL = {"open": "opened", "investigating": "investigating", "awaiting_evidence": "waiting for evidence", "decided": "decided",
              "closed_fraud": "closed as fraud", "closed_legitimate": "closed, not fraud", "escalated": "escalated", }
    rows = []
    for e in tl:
        chips = "".join(f'<span class="chip done">{ACTION[x]}: done</span>' for x in e.get("executed", [])) + \
                "".join(f'<span class="chip wait">{ACTION[x]}: awaiting approval</span>' for x in e.get("queued", []))
        txt = re.sub(r"\b[A-Z_]{6,}\b", lambda m: ACTION.get(m.group(0), m.group(0)).lower(), e["text"])
        rows.append(f'<div class="when">{esc(SLABEL.get(e["status"], e["status"]))}</div><div class="what" style="--c:{STC.get(e["status"], SAND)}">'
                    f'{esc(txt)}{"<br>" + chips if chips else ""}</div>')
    st.markdown(f'<div class="tl">{"".join(rows)}</div>', unsafe_allow_html=True)

t1, t2, t3, t4 = st.tabs(["Evidence", "Step by step", "Regulator report", "Answer file"])
with t1:
    fam = {x["msg"]: x.get("weight", 0) for x in tr["trace"] if x.get("kind") == "step" and x.get("step") == "evidence"}
    for e in k["evidence"]:
        w = fam.get(e["claim"], 0)
        cls = "f" if w > 0.3 else "l" if w < -0.3 else ""
        ids = ", ".join(e["entity_ids"][:6])
        st.markdown(f'<div class="ev {cls}">{esc(e["claim"])}<div class="src">source: {e["source"]}, {esc(e["ref"])}{" | " + esc(ids) if ids else ""}</div></div>', unsafe_allow_html=True)
    if k["similar_prior_cases"]:
        st.markdown("Past cases used as memory: " + ", ".join(f'<span class="mono">{c}</span>' for c in k["similar_prior_cases"]), unsafe_allow_html=True)
with t2:
    nt = sum(1 for x in tr["trace"] if x.get("kind") == "tool"); nfb = sum(1 for x in tr["trace"] if x.get("backend") == "local-fallback")
    st.markdown(f'<p class="muted">{nt} graph and retrieval calls, {nfb} served by the offline fallback, {a["tokens"]} LLM tokens, {a["latency_s"]} s. Recorded with backend: {tr.get("backend", "?")}.</p>', unsafe_allow_html=True)
    for x in tr["trace"]:
        if x["kind"] == "tool":
            args = ", ".join(f"{kk}={vv}" for kk, vv in x["args"].items())
            st.markdown(f'<div class="mono" style="padding:0.15rem 0">⟳ {esc(x["tool"])}({esc(args[:140])}) → {esc(x["result"])} <span class="muted">[{esc(x["backend"])}]</span></div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div style="padding:0.2rem 0"><b>{esc(x["step"].replace("_", " "))}</b>: {esc(x["msg"])}</div>', unsafe_allow_html=True)
with t3:
    st.markdown(f"**Decision:** {a['sar']['reason']}")
    if a["sar"]["file"]:
        st.markdown(f"**Activity:** {a['sar']['activity_dates'][0]} to {a['sar']['activity_dates'][1]}, {money(a['sar']['total_amount_usd'])}")
        st.markdown(f'<div class="step" style="border-left-color:{KUMKUM}">{esc(a["sar"]["narrative"])}</div>', unsafe_allow_html=True)
        st.markdown("Subjects: " + ", ".join(f'<span class="mono">{s}</span>' for s in a["sar"]["subjects"]), unsafe_allow_html=True)
with t4:
    st.download_button("Download this answer file", json.dumps(a, indent=2), file_name=f"{cid}.json", mime="application/json")
    st.markdown(f'<pre class="mono" style="background:#FFFCF5;color:{INK};border-radius:12px;padding:0.9rem;max-height:460px;overflow:auto;white-space:pre-wrap">'
                f'{esc(json.dumps(a, indent=2))}</pre>', unsafe_allow_html=True)

# Re-running rewrites cases/ and traces/ and writes to the live graph, so it is off unless explicitly enabled
# (and case memory should be reset first: scripts/reset_case_memory.py).
if os.getenv("CHAKRAVYUH_ALLOW_RERUN") == "1":
    with st.expander("Re-run this investigation"):
        have_data = os.path.exists("data/local/transactions.parquet")
        backend = st.radio("Graph backend", ["tigergraph", "local"], horizontal=True,
                           format_func=lambda b: "TigerGraph (through MCP)" if b == "tigergraph" else "Offline copy of the graph")
        if not have_data:
            st.markdown('<p class="muted">Re-running needs the prepared dataset (data/local/*.parquet), which is not deployed. The case above is the recorded run.</p>', unsafe_allow_html=True)
        elif st.button("Run the investigation again"):
            from agent.runtime import build
            from agent.answer import build as build_answer
            @st.cache_resource
            def inv(b): return build(b, os.getenv("EMBEDDER", "fastembed"))
            case = {**pack.loc[cid].to_dict(), "case_id": cid, "flagged_txn_id": str(row.flagged_txn_id)}
            with st.spinner("Walking the rings…"):
                s = inv(backend).run(case)
            ans = build_answer(s)
            json.dump(ans, open(f"cases/{cid}.json", "w"), indent=2, default=str)
            served = s["tool_calls"] - s.get("fallback_calls", 0) if s.get("engine") == "tigergraph-mcp" else 0
            json.dump({"case_id": cid, "backend": backend, "writer": s["writer"], "p0": s["p0"], "tigergraph_calls": served,
                       "branches": s.get("branches", []), "timeline": s.get("timeline", []), "executions": s.get("executions", []), "trace": s["trace"]}, open(f"traces/{cid}.json", "w"), indent=2, default=str)
            st.cache_data.clear(); st.rerun()

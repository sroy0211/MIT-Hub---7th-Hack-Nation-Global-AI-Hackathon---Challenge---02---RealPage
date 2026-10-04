import spaces
# Lease Lookup: which laws cover this apartment, and why
# Hack-Nation x RealPage, Challenge 2 (Rental Housing Law Navigator), team Corpus Juris AI
# Needs rules_full.json, jurisdictions.json and sample_addresses.csv next to this file.
import json, html, datetime, re
import requests
import pandas as pd
import gradio as gr
import spaces

@spaces.GPU
def _dummy_gpu():
    pass


DEFAULT_AS_OF = '2026-10-01'
COMPARE_AS_OF = '2027-07-03'   # day after NJ FAIR Act takes effect
RULES = json.load(open('rules_full.json'))
if isinstance(RULES, dict):
    RULES = RULES.get('rules', [])
JUR = json.load(open('jurisdictions.json'))
ADDR = pd.read_csv('sample_addresses.csv', dtype={'address_id': str, 'zip': str})
A = ADDR.set_index('address_id')
BY = {r['team_rule_id']: r for r in RULES}
CITY_GOVERNS = {'rent_increase_limits', 'just_cause_eviction'}
COVERED_STATES = sorted({(r['jurisdiction'].split(',')[-1]).strip().upper() for r in RULES})
CENSUS = 'https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress'

# ---------------- engine (same logic as the notebook's Module C) ----------------
def eff_status(r, as_of):
    s = r.get('status')
    if s in ('pending', 'failed'):
        return s
    e = r.get('effective_date')
    if e:
        e = e if len(e) == 10 else (e + '-01-01')[:10] if len(e) == 4 else (e + '-01')[:10]
        return 'not_yet_effective' if e > as_of else 'in_force'
    return 'not_yet_effective' if s == 'not_yet_effective' else 'in_force'

def num(x):
    try:
        return None if x is None or pd.isna(x) or x == '' else float(x)
    except Exception:
        return None

def covered(r, b, as_of):
    c = r.get('coverage') or {}
    units, yb = num(b.get('units')), num(b.get('year_built'))
    for k, test in (('min_units', lambda u, v: u >= v), ('max_units', lambda u, v: u <= v)):
        if c.get(k) is not None:
            if units is None:
                return None, 'Unit count is not known for this building.'
            if not test(units, c[k]):
                return False, ''
    for k, before in (('built_on_or_before', True), ('built_after', False)):
        d = c.get(k)
        if d:
            if yb is None:
                return None, 'Year built is not known for this building.'
            cut = int(str(d)[:4])
            if c.get('cutoff_uses_certificate_of_occupancy') and int(yb) == cut:
                return None, f'Built in {cut}; the rule turns on the certificate-of-occupancy date, which is not in the data.'
            if not ((yb <= cut) if before else (yb > cut)):
                return False, ''
    if c.get('min_age_years'):
        if yb is None:
            return None, 'Year built is not known for this building.'
        age = int(as_of[:4]) - int(yb)
        if age < c['min_age_years']:
            return False, ''
        if age == c['min_age_years']:
            return None, 'Building is right at the age cutoff; exact certificate date not in data.'
    if c.get('depends_on_owner_type'):
        return None, 'Depends on who owns the building; owner data is deliberately excluded.'
    m = (c.get('other_missing_fact') or '').lower()
    if m:
        if 'family' in m and num(b.get('units')) is not None:
            if num(b.get('units')) <= 2:
                return None, c['other_missing_fact']
        elif any(k in m for k in ('owner', 'occup', 'certificate', 'subsidi', 'public housing')):
            return None, c['other_missing_fact']
    return True, 'Building facts meet the coverage conditions.'

def in_juris(r, j):
    st = (j.get('state') or '').upper()
    if r['level'] == 'state':
        return r['jurisdiction'].strip().upper() == st
    city, rst = (r['jurisdiction'].split(',') + [''])[:2]
    if rst.strip().upper() != st:
        return False
    if j.get('city') is None:
        return False if j.get('matched') else None
    return city.strip().lower() == j['city'].lower()

def lookup(b, j, as_of, if_enacted=False):
    rules = RULES
    if if_enacted:   # what-if: treat pending bills as enacted today
        rules = [dict(r, status='in_force', effective_date=None, _hypo=True) if r.get('status') == 'pending' else r for r in RULES]
    rows = []
    for r in rules:
        inj = in_juris(r, j)
        if inj is False:
            continue
        st = eff_status(r, as_of)
        if st == 'failed':
            continue
        if st == 'pending':
            rows.append([r, 'pending', 'A bill or proposal, not law. It would apply here only if enacted.'])
            continue
        if inj is None:
            rows.append([r, 'unknown', 'The legal city for this address could not be confirmed.'])
            continue
        cov, why = covered(r, b, as_of)
        if cov is False:
            continue
        if st == 'not_yet_effective':
            rows.append([r, 'not_yet_effective', f"Enacted, but takes effect {r.get('effective_date')}."])
            continue
        rows.append([r, 'applies' if cov else 'unknown', why])
    for cat in CITY_GOVERNS:
        local = [x for x in rows if x[0]['level'] == 'city' and x[0]['category'] == cat]
        for x in rows:
            if x[0]['level'] == 'state' and x[0]['category'] == cat and x[1] == 'applies':
                if any(l[1] == 'applies' for l in local):
                    x[1], x[2] = 'superseded', 'Covered, but the stricter local ordinance governs here.'
                elif any(l[1] == 'unknown' for l in local):
                    x[1], x[2] = 'unknown', 'Applies unless the local ordinance covers this building, which the data cannot confirm.'
    alg = [x for x in rows if x[0]['category'] == 'algorithmic_rent_setting' and x[1] != 'pending']
    conflict = any(x[0]['level'] == 'city' for x in alg) and any(x[0]['level'] == 'state' for x in alg)
    out = []
    for r, res, why in rows:
        if r.get('_hypo') and res == 'applies':
            res, why = 'if_enacted', 'Pending today. This is what changes if it passes as written.'
        out.append({'rule': BY[r['team_rule_id']], 'result': res, 'why': why,
                    'conflict': conflict and r['category'] == 'algorithmic_rent_setting' and res != 'pending',
                    'review': bool(r.get('conflict_flag'))})
    return out

# ---------------- presentation ----------------
BADGE = {'applies': ('Applies', '#15803d'), 'unknown': ('Unknown: data gap', '#b45309'),
         'superseded': ('Superseded by local law', '#6b7280'), 'not_yet_effective': ('Enacted, not yet in effect', '#1d4ed8'),
         'pending': ('Pending bill, not law', '#7c3aed'), 'if_enacted': ('Would apply if enacted', '#be185d')}
ORDER = ['applies', 'if_enacted', 'unknown', 'not_yet_effective', 'superseded', 'pending']
e = lambda s: html.escape(str(s if s is not None else ''))
DATE = re.compile(r'^\d{4}-\d{2}-\d{2}')

def retrieved(r):
    v = str(r.get('retrieved_at') or '')
    return f' · retrieved {e(v[:10])}' if DATE.match(v) else ''

def pretty(cat):
    return (cat or '').replace('_', ' ').capitalize()

def badge(res):
    t, c = BADGE[res]
    return f'<span style="background:{c};color:#fff;border-radius:999px;padding:2px 10px;font-size:12px;font-weight:600">{t}</span>'

def card(x):
    r = x['rule']
    self_saved = r.get('source_type') == 'self_saved_link_only'
    src = 'Official page we saved ourselves (confidence capped)' if self_saved else 'Supplied corpus'
    if x['conflict']:
        flag = '<span style="color:#b91c1c;font-weight:600">City and state rules conflict here: needs human review</span>'
    elif x.get('review'):
        flag = '<span style="color:#9ca3af;font-size:12px">flagged for review during extraction</span>'
    else:
        flag = ''
    url = r.get('source_url')
    link = f' · <a href="{e(url)}" target="_blank">source</a>' if url else ''
    quote = (r.get('quoted_span') or '')[:420]
    return (f'<div style="border:1px solid #e5e7eb;border-radius:12px;padding:12px 14px;margin:8px 0">'
            f'<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b>{e(pretty(r["category"]))}</b>{badge(x["result"])}'
            f'<span style="color:#6b7280;font-size:12px">{e(r["jurisdiction"])} · {e(r["team_rule_id"])}</span>{flag}</div>'
            f'<div style="margin-top:6px">{e(r.get("requirement"))}</div>'
            f'<div style="margin-top:4px;color:#374151;font-style:italic">{e(x["why"])}</div>'
            f'<blockquote style="margin:8px 0;padding:6px 10px;border-left:3px solid #c7d2fe;background:#f8fafc;color:#334155">"{e(quote)}"</blockquote>'
            f'<div style="font-size:12px;color:#6b7280">{e(r.get("citation"))}{retrieved(r)} · {src}{link}</div></div>')

def header(title, j, b, as_of, note=''):
    city = j.get('city') or 'not confirmed'
    postal = b.get('postal_city')
    diff = ''
    if postal and j.get('city') and postal.strip().lower() != j['city'].strip().lower():
        diff = f' <span style="color:#b45309">(mail says {e(postal)}, but the law that applies is {e(j["city"])}\'s)</span>'
    fmt = lambda v: str(int(num(v))) if num(v) is not None else 'unknown'
    facts = f'Units: {fmt(b.get("units"))} · Year built: {fmt(b.get("year_built"))}'
    return (f'<div style="padding:4px 0 8px"><div style="font-size:20px;font-weight:700">{e(title)}</div>'
            f'<div>Legal jurisdiction: {e(j.get("state"))} › {e(j.get("county") or "?")} › <b>{e(city)}</b>{diff}</div>'
            f'<div style="color:#6b7280">{facts} · Answer as of <b>{e(as_of)}</b>{note}</div>'
            f'<div style="margin-top:6px;font-size:12px;color:#991b1b">Not legal advice. Every answer quotes its source and says when the data cannot decide.</div></div>')

def render(rows):
    if not rows:
        return '<p>No rules in our dataset cover this address on that date.</p>'
    counts = {k: sum(1 for x in rows if x['result'] == k) for k in ORDER}
    summary = ' '.join(f'{badge(k)} <b>{v}</b>&nbsp;&nbsp;' for k, v in counts.items() if v)
    body = ''.join(card(x) for x in sorted(rows, key=lambda x: (ORDER.index(x['result']), x['rule']['category'])))
    return f'<div style="margin:6px 0 10px">{summary}</div>{body}'

def diff_table(a, b):
    ka = {x['rule']['team_rule_id']: x for x in a}
    kb = {x['rule']['team_rule_id']: x for x in b}
    rows = []
    for rid in sorted(set(ka) | set(kb)):
        ra = ka.get(rid, {}).get('result', 'does not apply')
        rb = kb.get(rid, {}).get('result', 'does not apply')
        if ra != rb:
            r = BY[rid]
            lab = lambda s: BADGE[s][0] if s in BADGE else s.capitalize()
            rows.append({'Rule': rid, 'Topic': pretty(r['category']), 'Jurisdiction': r['jurisdiction'],
                         'Before': lab(ra), 'After': lab(rb), 'Citation': r.get('citation')})
    return pd.DataFrame(rows, columns=['Rule', 'Topic', 'Jurisdiction', 'Before', 'After', 'Citation'])

def valid_date(s):
    try:
        return datetime.date.fromisoformat((s or '').strip()).isoformat()
    except ValueError:
        return None

def run(b, j, title, as_of, compare_to, if_enacted, note=''):
    a1, a2 = valid_date(as_of), valid_date(compare_to)
    if not a1 or not a2:
        return '<p>Dates need to look like 2026-10-01.</p>', gr.update(value=pd.DataFrame(), visible=False), ''
    now = lookup(b, j, a1)
    later = lookup(b, j, a2, if_enacted=if_enacted)
    d = diff_table(now, later)
    label = f'{a1} → {a2}' + (' with pending bills passed' if if_enacted else '')
    msg = (f'### What changes, {label}\n**{len(d)} result(s) change** for this building.' if len(d)
           else f'### What changes, {label}\nNothing changes for this building. Try the Jersey City or Hoboken example, or tick "What if pending bills pass?".')
    return header(title, j, b, a1, note) + render(now), gr.update(value=d, visible=len(d) > 0), msg

# ---------------- address sources ----------------
def label_for(aid):
    r = A.loc[aid]
    return f"{r['street_address']}, {r['postal_city']} {r['state']} ({aid})"

CHOICES = [(label_for(a), a) for a in ADDR['address_id']]

def sample(aid, as_of, compare_to, if_enacted):
    if not aid:
        return '<p>Pick an address.</p>', gr.update(visible=False), ''
    b = A.loc[aid].to_dict()
    j = JUR.get(aid, {'state': b.get('state'), 'city': None, 'matched': False})
    return run(b, j, f"{b['street_address']}, {b['postal_city']} {b['state']}", as_of, compare_to, if_enacted)

def geocode(address):
    p = dict(address=address, benchmark='Public_AR_Current', vintage='Current_Current',
             layers='Incorporated Places,Counties,States', format='json')
    m = requests.get(CENSUS, params=p, timeout=20).json()['result']['addressMatches']
    if not m:
        return None
    g = m[0]['geographies']
    return {'state': (g.get('States') or [{}])[0].get('STUSAB'),
            'city': (g.get('Incorporated Places') or [{}])[0].get('BASENAME'),
            'county': (g.get('Counties') or [{}])[0].get('NAME'),
            'matched': True, 'matched_address': m[0]['matchedAddress']}

def live(address, units, year_built, as_of, compare_to, if_enacted):
    if not (address or '').strip():
        return '<p>Type a full street address with city and state.</p>', gr.update(visible=False), ''
    try:
        j = geocode(address)
    except Exception:
        return '<p>The Census Geocoder did not answer. Try again in a moment, or use a sample address.</p>', gr.update(visible=False), ''
    if not j:
        return '<p>The Census Geocoder could not match that address. Check the spelling and include city, state and ZIP.</p>', gr.update(visible=False), ''
    if j['state'] not in COVERED_STATES:
        return f"<p>Matched {e(j['matched_address'])}, but this demo only covers {', '.join(COVERED_STATES)}.</p>", gr.update(visible=False), ''
    units = units if num(units) and num(units) > 0 else None          # empty box comes back as 0
    year_built = year_built if num(year_built) and num(year_built) >= 1700 else None
    b = {'units': units, 'year_built': year_built, 'postal_city': None}
    note = ' · Building facts entered by you; blanks stay unknown'
    return run(b, j, j['matched_address'], as_of, compare_to, if_enacted, note)

# ---------------- showcase picks, computed from the data ----------------
def find(pred):
    for aid in ADDR['address_id']:
        b, j = A.loc[aid], JUR.get(aid, {})
        try:
            if pred(b, j):
                return aid
        except Exception:
            pass
    return None

PICKS = [p for p in [
    ('Jersey City or Hoboken (FAIR Act conflict)', find(lambda b, j: (j.get('city') or '') in ('Jersey City', 'Hoboken'))),
    ('Postal city is not the legal city', find(lambda b, j: j.get('city') and str(b['postal_city']).lower() != j['city'].lower())),
    ('Legal city could not be confirmed', find(lambda b, j: j.get('matched') is False or (j.get('city') is None and j.get('method') != 'census_geocoder'))),
    ('Boston, MA', find(lambda b, j: (j.get('city') or '') == 'Boston')),
] if p[1]]

# ---------------- rule explorer ----------------
def rule_table(state, topic, src):
    rows = []
    for r in RULES:
        st = r['jurisdiction'].split(',')[-1].strip().upper()
        s = 'Self-saved page' if r.get('source_type') == 'self_saved_link_only' else 'Supplied corpus'
        if (state != 'All' and st != state) or (topic != 'All' and pretty(r['category']) != topic) or (src != 'All' and s != src):
            continue
        rows.append({'Rule': r['team_rule_id'], 'Jurisdiction': r['jurisdiction'], 'Level': r['level'],
                     'Topic': pretty(r['category']), 'Status': r.get('status'), 'Effective': r.get('effective_date'),
                     'Citation': r.get('citation'), 'Source': s, 'Requirement': r.get('requirement')})
    return pd.DataFrame(rows)

TOPICS = ['All'] + sorted({pretty(r['category']) for r in RULES})
n_self = sum(1 for r in RULES if r.get('source_type') == 'self_saved_link_only')
n_city_fix = sum(1 for aid in ADDR['address_id'] if JUR.get(aid, {}).get('city')
                 and str(A.loc[aid]['postal_city']).lower() != JUR[aid]['city'].lower())
STATS = (f'**{len(RULES)}** cited rules across **{len(COVERED_STATES)}** states ({", ".join(COVERED_STATES)}) · '
         f'**{len(RULES) - n_self}** from the supplied corpus, **{n_self}** from official pages we saved ourselves · '
         f'**{len(ADDR)}** sample addresses, **{n_city_fix}** of them in a different legal city than their mailing city')

METHOD = """
### How Lease Lookup works
1. **Find the real jurisdiction.** The U.S. Census Geocoder turns each address into its legal city and county. A mailing address that says Van Nuys or Dorchester is legally Los Angeles or Boston, and that changes which ordinances apply.
2. **Extract rules with quotes.** Claude reads each source document against the challenge schema. Every rule keeps a verbatim quote, and the quote is checked against the source text. A rule whose quote can't be found is snapped to the closest real passage or flagged.
3. **Collapse and dedupe.** 364 raw extractions collapse to one rule per jurisdiction and topic. Duplicate bills and news write-ups are dropped by explicit rules, so the result is the same on every rerun.
4. **Decide per building.** Unit counts, year built and effective dates decide each rule. Stricter local rent and eviction ordinances supersede state law. When the data can't decide (owner type, certificate-of-occupancy date), the answer is **Unknown** with the reason, never a guess.
5. **Show what changes.** Pick any date, or assume pending bills pass, and see exactly which results flip and why.

### Honest limits
Not legal advice. Owner data is deliberately left out, so owner-dependent exemptions come back as Unknown. Pages we fetched ourselves (not from the supplied corpus) are labeled, dated, and capped at 0.5 confidence. Coverage is CA, NJ and MA only.
"""

# ---------------- UI ----------------
with gr.Blocks(title='Lease Lookup') as demo:
    gr.Markdown('# Lease Lookup\n**Which laws cover this apartment, and why.** Which renter rules apply to this building today, why, and what changes next. Built for Hack-Nation × RealPage (Challenge 2) by Corpus Juris AI.')
    gr.Markdown(STATS)
    with gr.Row():
        as_of = gr.Textbox(value=DEFAULT_AS_OF, label='Answer as of (YYYY-MM-DD)')
        compare_to = gr.Textbox(value=COMPARE_AS_OF, label='Compare with date')
        if_enacted = gr.Checkbox(value=False, label='What if pending bills pass?')
    with gr.Tabs():
        with gr.Tab('Sample addresses'):
            with gr.Row():
                pick = gr.Dropdown(choices=CHOICES, value=PICKS[0][1] if PICKS else CHOICES[0][1],
                                   label='Search the 500 sample addresses', filterable=True, scale=4)
                go = gr.Button('Check this building', variant='primary', scale=1)
            if PICKS:
                with gr.Row():
                    pick_btns = [(gr.Button(t, size='sm'), aid) for t, aid in PICKS]
        with gr.Tab('Any address (live)'):
            with gr.Row():
                addr = gr.Textbox(label='Street address, city, state, ZIP', placeholder='1 City Hall Square, Boston, MA 02201', scale=4)
                units = gr.Number(label='Units (optional)', precision=0, scale=1)
                year = gr.Number(label='Year built (optional)', precision=0, scale=1)
            go_live = gr.Button('Look it up', variant='primary')
        with gr.Tab('All rules'):
            with gr.Row():
                f_state = gr.Dropdown(['All'] + COVERED_STATES, value='All', label='State')
                f_topic = gr.Dropdown(TOPICS, value='All', label='Topic')
                f_src = gr.Dropdown(['All', 'Supplied corpus', 'Self-saved page'], value='All', label='Source')
            rules_df = gr.Dataframe(value=rule_table('All', 'All', 'All'), wrap=True, interactive=False)
            for f in (f_state, f_topic, f_src):
                f.change(rule_table, [f_state, f_topic, f_src], rules_df)
        with gr.Tab('How it works'):
            gr.Markdown(METHOD)
    report = gr.HTML()
    changes_msg = gr.Markdown()
    changes = gr.Dataframe(label='Results that flip between the two dates', wrap=True, interactive=False, visible=False)

    outs = [report, changes, changes_msg]
    shared = [as_of, compare_to, if_enacted]
    go.click(lambda a, *s: sample(a, *s), [pick] + shared, outs)
    go_live.click(live, [addr, units, year] + shared, outs)
    if PICKS:
        for btn, aid in pick_btns:
            btn.click(lambda *s, aid=aid: (aid, *sample(aid, *s)), shared, [pick] + outs)
    demo.load(lambda a, *s: sample(a, *s), [pick] + shared, outs)

if __name__ == '__main__':
    demo.launch(theme=gr.themes.Soft()) if int(gr.__version__.split('.')[0]) >= 6 else demo.launch()

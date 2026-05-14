from flask import Flask, request, jsonify, render_template_string
import pandas as pd
import numpy as np
from datetime import date
import io, json

app = Flask(__name__)

# ── Category master ──────────────────────────────────────────────
THR_MAP = {
    'Industrial':1,'TBB':2,'TBR':2,'TRAC REAR':2,'LTB':2,'LTR - AS':2,
    'TRAC FRONT':2,'JEP':2,'SCV Radial':2,'ADV':3,'Pickup Radial':3,
    'PCR':4,'SCV Bias':4,'2/3W':5,'PCTR':5,'Pouch tube':5
}
CHAR_CAT = {
    'M':'Industrial','A':'TBB','B':'TBR','C':'TRAC REAR','D':'TRAC REAR',
    'K':'LTB','N':'TRAC FRONT','W':'LTR - AS','P':'JEP','2':'SCV Radial',
    'L':'ADV','F':'Pickup Radial','E':'LTB','G':'PCR','H':'SCV Bias',
    'J':'PCR','Q':'PCR','U':'2/3W','V':'2/3W','S':'2/3W','1':'2/3W',
    'T':'PCR','0':'PCR'
}

def get_cat(m):
    try:
        m = str(m)
        return CHAR_CAT.get(m[2].upper(), 'PCR') if len(m) >= 3 else 'PCR'
    except:
        return 'PCR'

def get_thr(cat):
    return THR_MAP.get(str(cat).strip(), 4)

def excel_serial(dt):
    if pd.isna(dt): return None
    if hasattr(dt, 'date'): d = dt.date()
    else: d = dt
    return (d - date(1899, 12, 30)).days

def calculate_adherence(apo_dfs, yvr_dfs, calc_date_str):
    """
    Core adherence calculation logic.
    
    Formula (from reference file Pivot_result_table.xlsx):
    -------------------------------------------------------
    Unique ID = To_Location + Excel_date_serial(From_Date) + Material
    
    DENOMINATOR (Check for inclusion = 1):
      APO rows where From_Date = calc_date AND actual_dispatch > 0
      AND Load_Qty >= Threshold (category-based)
    
    NUMERATOR (Result = 1):
      Denominator rows where actual_dispatch >= Load_Qty * 0.80
      (80% tolerance confirmed from reference data)
    
    Adherence per RDC = Numerator / Denominator * 100
    """
    calc_date = pd.to_datetime(calc_date_str).date()

    # Combine APO files, filter to calc_date only
    apo = pd.concat(apo_dfs, ignore_index=True)
    apo['From Date'] = pd.to_datetime(apo['From Date'])
    apo = apo[apo['From Date'].dt.date == calc_date].copy()

    # Combine YVR files
    yvr = pd.concat(yvr_dfs, ignore_index=True)
    yvr['Billing Dt'] = pd.to_datetime(yvr['Billing Dt'])

    if apo.empty:
        return {'error': f'No APO data found for date {calc_date_str}'}

    # APO enrichment
    apo['cat'] = apo.get('Category', pd.Series(dtype=str)).fillna('').apply(
        lambda c: c.strip() if c.strip() in THR_MAP else None
    )
    apo['cat'] = apo.apply(
        lambda r: r['cat'] if r['cat'] else get_cat(r['Material']), axis=1
    )
    apo['thr'] = apo['cat'].apply(get_thr)
    apo['serial'] = apo['From Date'].apply(excel_serial)
    apo['Unique_ID'] = (
        apo['To Location'].astype(str) +
        apo['serial'].astype('Int64').astype(str) +
        apo['Material'].astype(str)
    )

    # YVR enrichment
    yvr['ABU'] = yvr['R.Plnt / Cust Code'].str[2:]
    yvr['serial'] = yvr['Billing Dt'].apply(excel_serial)
    yvr['Unique_ID'] = (
        yvr['ABU'].astype(str) +
        yvr['serial'].astype('Int64').astype(str) +
        yvr['Mat.Code'].astype(str)
    )
    yvr_qty = yvr.groupby('Unique_ID')['Quantity'].sum().to_dict()

    # Group APO
    apo_grp = apo.groupby(
        ['From Location', 'To Location', 'serial', 'Material', 'cat', 'thr']
    ).agg(apo_qty=('Load Quantity', 'sum')).reset_index()

    apo_grp['Unique_ID'] = (
        apo_grp['To Location'].astype(str) +
        apo_grp['serial'].astype('Int64').astype(str) +
        apo_grp['Material'].astype(str)
    )
    apo_grp['actual'] = apo_grp['Unique_ID'].map(yvr_qty).fillna(0)

    # Flags
    apo_grp['dispatched']     = apo_grp['actual'] > 0
    apo_grp['check_incl']     = apo_grp['dispatched'] & (apo_grp['apo_qty'] >= apo_grp['thr'])
    apo_grp['result']         = apo_grp['check_incl'] & (apo_grp['actual'] >= apo_grp['apo_qty'] * 0.80)

    # RDC summary
    rdc_rows = []
    for rdc, grp in apo_grp.groupby('From Location'):
        den = int(grp['check_incl'].sum())
        num = int(grp['result'].sum())
        rdc_rows.append({
            'rdc': rdc,
            'denominator': den,
            'numerator': num,
            'adherence': round(num / den * 100, 1) if den else 0
        })

    rdc_summary = sorted(rdc_rows, key=lambda x: -x['adherence'])

    # Overall
    total_den = sum(r['denominator'] for r in rdc_rows)
    total_num = sum(r['numerator'] for r in rdc_rows)

    # Detail rows
    detail = []
    for _, r in apo_grp.iterrows():
        detail.append({
            'rdc': r['From Location'],
            'do': r['To Location'],
            'material': r['Material'],
            'category': r['cat'],
            'threshold': int(r['thr']),
            'apo_qty': int(r['apo_qty']),
            'actual': int(r['actual']),
            'dispatched': bool(r['dispatched']),
            'included': bool(r['check_incl']),
            'adhered': bool(r['result'])
        })

    return {
        'calc_date': calc_date_str,
        'overall': {
            'numerator': total_num,
            'denominator': total_den,
            'adherence': round(total_num / total_den * 100, 1) if total_den else 0
        },
        'rdc_summary': rdc_summary,
        'detail': detail
    }


@app.route('/')
def index():
    return render_template_string(open('/home/claude/dispatch_app/index.html').read())

@app.route('/calculate', methods=['POST'])
def calculate():
    try:
        files = request.files
        calc_date = request.form.get('calc_date', '')

        apo_dfs, yvr_dfs = [], []
        for key in files:
            f = files[key]
            df = pd.read_excel(io.BytesIO(f.read()))
            if 'apo' in key.lower():
                apo_dfs.append(df)
            elif 'yvr' in key.lower():
                yvr_dfs.append(df)

        if not apo_dfs or not yvr_dfs:
            return jsonify({'error': 'Upload at least 1 APO and 1 YVR file'}), 400

        result = calculate_adherence(apo_dfs, yvr_dfs, calc_date)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)

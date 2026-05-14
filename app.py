from flask import Flask, request, jsonify, render_template
import pandas as pd
import numpy as np
from datetime import datetime

app = Flask(__name__)


# =========================================
# CATEGORY THRESHOLDS (fallback)
# =========================================

CATEGORY_THRESHOLDS = {
    'Industrial': 1,
    'TBB': 2,
    'TBR': 2,
    'TRAC REAR': 2,
    'LTB': 2,
    'LTR - AS': 2,
    'LTR-AS': 2,
    'TRAC FRONT': 2,
    'JEP': 2,
    'SCV Radial': 2,
    'ADV': 3,
    'Pickup Radial': 3,
    'PCR': 4,
    'SCV Bias': 4,
    '2/3W': 5,
    'PCTR': 5,
    'Pouch tube': 5
}


# =========================================
# EXCEL SERIAL DATE
# =========================================

def excel_serial_date(date_val):
    if pd.isna(date_val):
        return ''
    try:
        date_val = pd.to_datetime(date_val)
        return int((date_val - datetime(1899, 12, 30)).days)
    except:
        return ''


# =========================================
# CATEGORY DERIVATION
# Mirrors Excel formula:
#   =IF(MID(D,2,1)="P","PCTR",
#    IF(MID(D,2,1)="W","Pouch tube",
#    VLOOKUP(MID(D,3,1), Category Master!A:B, 2, 0)))
# =========================================

def build_category_lookup(category_master_df):
    lookup = {}
    for _, row in category_master_df.iterrows():
        key = str(row.iloc[0]).strip()
        val = str(row.iloc[1]).strip()
        if key and key not in ('nan', 'Third letter'):
            lookup[key] = val
    return lookup

def build_threshold_lookup(category_master_df):
    lookup = {}
    for _, row in category_master_df.iterrows():
        cat = str(row.iloc[1]).strip()
        thr = row.iloc[2]
        if cat and cat != 'nan' and not pd.isna(thr):
            lookup[cat] = int(thr)
    return lookup

def derive_category(material, cat_lookup):
    material = str(material).strip()
    if len(material) < 2:
        return 'PCR'
    char2 = material[1]           # MID(D,2,1)
    if char2 == 'P':
        return 'PCTR'
    elif char2 == 'W':
        return 'Pouch tube'
    else:
        if len(material) >= 3:
            return cat_lookup.get(material[2], 'PCR')   # MID(D,3,1)
        return 'PCR'


# =========================================
# HOME
# =========================================

@app.route('/')
def home():
    return render_template('index.html')


# =========================================
# MAIN CALCULATION
# =========================================

@app.route('/calculate', methods=['POST'])
def calculate_adherence():

    try:

        # ── READ FILES ────────────────────────────────────────
        apo_file_1 = request.files['apo']
        apo_file_2 = request.files['apo2']
        yvr_file_1 = request.files['yvr']
        yvr_file_2 = request.files['yvr2']
        calc_date  = request.form.get('calc_date', '')

        apo_df_1 = pd.read_excel(apo_file_1)
        apo_df_2 = pd.read_excel(apo_file_2)
        yvr_df_1 = pd.read_excel(yvr_file_1)
        yvr_df_2 = pd.read_excel(yvr_file_2)

        # Optional Category Master upload
        cat_master_file = request.files.get('category_master')
        if cat_master_file:
            cat_master_df    = pd.read_excel(cat_master_file)
            cat_lookup       = build_category_lookup(cat_master_df)
            threshold_lookup = build_threshold_lookup(cat_master_df)
        else:
            cat_lookup       = {}
            threshold_lookup = CATEGORY_THRESHOLDS

        # ── COMBINE & CLEAN ───────────────────────────────────
        apo_df = pd.concat([apo_df_1, apo_df_2], ignore_index=True)
        yvr_df = pd.concat([yvr_df_1, yvr_df_2], ignore_index=True)

        apo_df.columns = apo_df.columns.str.strip()
        yvr_df.columns = yvr_df.columns.str.strip()

        # ── APO CLEANING ──────────────────────────────────────
        apo_df['From Date']     = pd.to_datetime(apo_df['From Date'], errors='coerce')
        apo_df['Material']      = apo_df['Material'].astype(str).str.strip().str.replace('.0', '', regex=False)
        apo_df['To Location']   = apo_df['To Location'].astype(str).str.strip()
        apo_df['From Location'] = apo_df['From Location'].astype(str).str.strip()
        apo_df['Load Quantity'] = pd.to_numeric(apo_df['Load Quantity'], errors='coerce').fillna(0)
        apo_df['Truck Number']  = pd.to_numeric(apo_df['Truck Number'],  errors='coerce').fillna(0)

        if 'No of vehicles sent' in apo_df.columns:

            apo_df['No of vehicles sent'] = pd.to_numeric(
            apo_df['No of vehicles sent'],
            errors='coerce'
                ).fillna(0)

        else:

            apo_df['No of vehicles sent'] = 0

        # ── CATEGORY & THRESHOLD ──────────────────────────────
        apo_df['Category']  = apo_df['Material'].apply(lambda m: derive_category(m, cat_lookup))
        apo_df['Threshold'] = apo_df['Category'].map(threshold_lookup).fillna(1)

        # ── ELIGIBILITY FLAGS (row level) ─────────────────────
        #
        # The APO sheet has one row PER TRUCK per SKU per DO per date.
        # Excel logic:
        #   INDICATOR          = Load_Qty > Threshold          (SKU meets min qty)
        #   Check for inclusion = Truck_Number <= No_of_vehicles_sent
        #                         (this truck was actually dispatched)
        #
        # Both must be 1 for a row to count.
        #
        apo_df['INDICATOR'] = np.where(
            apo_df['Load Quantity'] > apo_df['Threshold'], 1, 0
        )
        apo_df['Check_for_inclusion'] = np.where(
            (apo_df['No of vehicles sent'] > 0) &
            (apo_df['Truck Number'] <= apo_df['No of vehicles sent']),
            1, 0
        )
        apo_df['Eligible'] = np.where(
            (apo_df['INDICATOR'] == 1) & (apo_df['Check_for_inclusion'] == 1),
            1, 0
        )

        # ── UNIQUE ID ─────────────────────────────────────────
        #   = To_Location + Excel_date_serial(From_Date) + Material
        #   (no day shift — matches Excel Date&ABU formula exactly)
        apo_df['DateSerial'] = apo_df['From Date'].apply(excel_serial_date)
        apo_df['Unique_ID']  = (
            apo_df['To Location']
            + apo_df['DateSerial'].astype(str)
            + apo_df['Material']
        )

        # ── AGGREGATE ELIGIBLE APO ROWS BY UNIQUE_ID ─────────
        #
        # Multiple truck rows per SKU+DO+Date are summed to get
        # total planned load — this is what the RESULT sheet shows.
        #
        eligible_apo = apo_df[apo_df['Eligible'] == 1]

        apo_grouped = (
            eligible_apo.groupby(['Unique_ID', 'From Location', 'To Location', 'Material', 'Category', 'Threshold'])
            ['Load Quantity'].sum()
            .reset_index()
            .rename(columns={'Load Quantity': 'Planned_Qty'})
        )

        # ── YVR CLEANING ──────────────────────────────────────
        yvr_df['Billing Dt'] = pd.to_datetime(yvr_df['Billing Dt'], errors='coerce')

        customer_col = next(
            (c for c in ['R.Plnt / Cust Code', 'R.Plnt', 'Cust Code', 'Ship-to party', 'Customer']
             if c in yvr_df.columns),
            None
        )
        if customer_col is None:
            return jsonify({'error': 'Customer column not found in YVR file'})

        # MID(col, 3, 4) in Excel = str[2:6] in Python
        # e.g. "ZCAP01" -> "AP01"
        yvr_df['DO_CODE'] = yvr_df[customer_col].astype(str).str.strip().str[2:6]

        material_col = next(
            (c for c in ['Mat.Code', 'Material', 'Material Number', 'SKU']
             if c in yvr_df.columns),
            None
        )
        if material_col is None:
            return jsonify({'error': 'Material column not found in YVR file'})

        yvr_df[material_col] = (
            yvr_df[material_col].astype(str).str.strip()
            .str.replace('.0', '', regex=False)
        )

        qty_col = next(
            (c for c in ['Quantity', 'Billing Qty.', 'Qty', 'Actual Qty']
             if c in yvr_df.columns),
            None
        )
        if qty_col is None:
            return jsonify({'error': 'Quantity column not found in YVR file'})

        yvr_df[qty_col]      = pd.to_numeric(yvr_df[qty_col], errors='coerce').fillna(0)
        yvr_df['DateSerial'] = yvr_df['Billing Dt'].apply(excel_serial_date)
        yvr_df['Unique_ID']  = (
            yvr_df['DO_CODE']
            + yvr_df['DateSerial'].astype(str)
            + yvr_df[material_col]
        )

        # ── AGGREGATE ACTUALS ─────────────────────────────────
        actual_dispatch = (
            yvr_df.groupby('Unique_ID')[qty_col]
            .sum()
            .reset_index()
            .rename(columns={qty_col: 'Actual_Qty'})
        )

        # ── MERGE ─────────────────────────────────────────────
        result_df = apo_grouped.merge(actual_dispatch, on='Unique_ID', how='left')
        result_df['Actual_Qty'] = result_df['Actual_Qty'].fillna(0)

        # ── ADHERENCE FLAGS ───────────────────────────────────
        result_df['Adherence_Ratio'] = np.where(
            result_df['Planned_Qty'] > 0,
            result_df['Actual_Qty'] / result_df['Planned_Qty'],
            0
        )

        # Strictly > 0.8 confirmed from RESULT sheet:
        # rows with exactly 0.800 ratio → Result = 0
        result_df['Result'] = np.where(
            result_df['Adherence_Ratio'] > 0.8,
            1, 0
        )

        # ── SUMMARY PER RDC ───────────────────────────────────
        rdc_summary = (
            result_df.groupby('From Location')
            .agg(
                numerator=('Result', 'sum'),
                denominator=('Result', 'count')
            )
            .reset_index()
            .rename(columns={'From Location': 'rdc'})
        )
        rdc_summary['adherence'] = (
            rdc_summary['numerator'] / rdc_summary['denominator'] * 100
        ).round(2)

        # ── OVERALL ───────────────────────────────────────────
        total_den = int(rdc_summary['denominator'].sum())
        total_num = int(rdc_summary['numerator'].sum())
        overall_adh = round(total_num / total_den * 100, 2) if total_den > 0 else 0

        # ── DETAIL ROWS — field names match index.html exactly ─
        detail = []
        for _, row in result_df.iterrows():
            detail.append({
                'rdc':        str(row['From Location']),
                'do':         str(row['To Location']),
                'material':   str(row['Material']),
                'category':   str(row['Category']),
                'apo_qty':    int(row['Planned_Qty']),
                'threshold':  int(row['Threshold']),
                'actual':     int(row['Actual_Qty']),
                'dispatched': bool(row['Actual_Qty'] > 0),
                'included':   True,   # all rows here are already eligible
                'adhered':    bool(row['Result'])
            })

        return jsonify({
            'calc_date': calc_date,
            'overall': {
                'adherence':   overall_adh,
                'numerator':   total_num,
                'denominator': total_den
            },
            'rdc_summary': rdc_summary.to_dict(orient='records'),
            'detail':      detail
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)})


# =========================================
# RUN
# =========================================

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)

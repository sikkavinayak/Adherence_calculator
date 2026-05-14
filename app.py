from flask import Flask, request, jsonify, render_template
import pandas as pd
import numpy as np
from datetime import datetime

app = Flask(__name__)


# =========================================
# CATEGORY THRESHOLDS
# =========================================

CATEGORY_THRESHOLDS = {
    'Industrial': 1,
    'TBB': 2,
    'TBR': 2,
    'TRAC REAR': 2,
    'LTB': 2,
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
        excel_start = datetime(1899, 12, 30)
        return int((date_val - excel_start).days)
    except:
        return ''


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

        # =========================================
        # READ FILES
        # =========================================

        apo_file = request.files['apo_file']
        yvr_file = request.files['yvr_file']

        apo_df = pd.read_excel(apo_file)
        yvr_df = pd.read_excel(yvr_file)

        # =========================================
        # CLEAN COLUMN NAMES
        # =========================================

        apo_df.columns = apo_df.columns.str.strip()
        yvr_df.columns = yvr_df.columns.str.strip()

        # =========================================
        # APO CLEANING
        # =========================================

        apo_df['From Date'] = pd.to_datetime(
            apo_df['From Date'],
            errors='coerce'
        )

        # PREVIOUS DAY LOGIC
        apo_df['Calc Date'] = (
            apo_df['From Date']
            - pd.Timedelta(days=1)
        )

        # CLEAN MATERIAL
        apo_df['Material'] = (
            apo_df['Material']
            .astype(str)
            .str.strip()
            .str.replace('.0', '', regex=False)
        )

        # CLEAN TO LOCATION
        apo_df['To Location'] = (
            apo_df['To Location']
            .astype(str)
            .str.strip()
        )

        # DATE SERIAL
        apo_df['DateSerial'] = (
            apo_df['Calc Date']
            .apply(excel_serial_date)
        )

        # UNIQUE ID
        apo_df['Unique_ID'] = (
            apo_df['To Location']
            + apo_df['DateSerial'].astype(str)
            + apo_df['Material']
        )

        # =========================================
        # CATEGORY LOGIC
        # =========================================

        def derive_category(material):

            material = str(material).upper()

            if 'PCR' in material:
                return 'PCR'

            elif 'TBR' in material:
                return 'TBR'

            elif '2W' in material or '3W' in material:
                return '2/3W'

            else:
                return 'PCR'

        apo_df['Category'] = (
            apo_df['Material']
            .apply(derive_category)
        )

        apo_df['Threshold'] = (
            apo_df['Category']
            .map(CATEGORY_THRESHOLDS)
            .fillna(1)
        )

        # =========================================
        # LOAD QUANTITY CLEANING
        # =========================================

        apo_df['Load Quantity'] = pd.to_numeric(
            apo_df['Load Quantity'],
            errors='coerce'
        ).fillna(0)

        # =========================================
        # YVR CLEANING
        # =========================================

        yvr_df['Billing Dt'] = pd.to_datetime(
            yvr_df['Billing Dt'],
            errors='coerce'
        )

        # =========================================
        # CUSTOMER / DO CLEANING
        # =========================================

        customer_col = None

        possible_customer_cols = [
            'R.Plnt',
            'Cust Code',
            'Ship-to party',
            'Customer'
        ]

        for col in possible_customer_cols:

            if col in yvr_df.columns:
                customer_col = col
                break

        if customer_col is None:
            return jsonify({
                'error': 'Customer column not found'
            })

        yvr_df['DO_CODE'] = (
            yvr_df[customer_col]
            .astype(str)
            .str.replace('ZC', '', regex=False)
            .str.strip()
        )

        # =========================================
        # MATERIAL COLUMN
        # =========================================

        material_col = None

        possible_material_cols = [
            'Material',
            'Material Number',
            'SKU'
        ]

        for col in possible_material_cols:

            if col in yvr_df.columns:
                material_col = col
                break

        if material_col is None:
            return jsonify({
                'error': 'Material column not found'
            })

        yvr_df[material_col] = (
            yvr_df[material_col]
            .astype(str)
            .str.strip()
            .str.replace('.0', '', regex=False)
        )

        # =========================================
        # QUANTITY COLUMN
        # =========================================

        qty_col = None

        possible_qty_cols = [
            'Billing Qty.',
            'Quantity',
            'Qty',
            'Actual Qty'
        ]

        for col in possible_qty_cols:

            if col in yvr_df.columns:
                qty_col = col
                break

        if qty_col is None:
            return jsonify({
                'error': 'Quantity column not found'
            })

        yvr_df[qty_col] = pd.to_numeric(
            yvr_df[qty_col],
            errors='coerce'
        ).fillna(0)

        # =========================================
        # DATE SERIAL
        # =========================================

        yvr_df['DateSerial'] = (
            yvr_df['Billing Dt']
            .apply(excel_serial_date)
        )

        # =========================================
        # UNIQUE ID
        # =========================================

        yvr_df['Unique_ID'] = (
            yvr_df['DO_CODE']
            + yvr_df['DateSerial'].astype(str)
            + yvr_df[material_col]
        )

        # =========================================
        # AGGREGATE ACTUALS
        # =========================================

        actual_dispatch = (
            yvr_df.groupby('Unique_ID')[qty_col]
            .sum()
            .reset_index()
        )

        actual_dispatch.columns = [
            'Unique_ID',
            'Actual_Qty'
        ]

        # =========================================
        # DEBUGGING
        # =========================================

        print("========== APO IDS ==========")
        print(apo_df['Unique_ID'].head(10))

        print("========== YVR IDS ==========")
        print(actual_dispatch['Unique_ID'].head(10))

        # =========================================
        # MERGE
        # =========================================

        result_df = apo_df.merge(
            actual_dispatch,
            on='Unique_ID',
            how='left'
        )

        result_df['Actual_Qty'] = (
            result_df['Actual_Qty']
            .fillna(0)
        )

        # =========================================
        # ADHERENCE LOGIC
        # =========================================

        result_df['Adherence_Ratio'] = np.where(
            result_df['Load Quantity'] > 0,
            result_df['Actual_Qty']
            / result_df['Load Quantity'],
            0
        )

        result_df['Result'] = np.where(
            result_df['Adherence_Ratio'] >= 0.8,
            1,
            0
        )

        # =========================================
        # ELIGIBILITY
        # =========================================

        result_df['Eligible'] = np.where(
            result_df['Load Quantity']
            >= result_df['Threshold'],
            1,
            0
        )

        # TEMPORARILY KEEP ALL ROWS
        # TO MATCH EXCEL BETTER

        eligible_df = result_df.copy()

        # =========================================
        # SUMMARY
        # =========================================

        summary = (
            eligible_df.groupby('From Location')
            .agg(
                Total_Planned=('Result', 'count'),
                Adhered=('Result', 'sum')
            )
            .reset_index()
        )

        summary['Adherence_Percent'] = (
            summary['Adhered']
            / summary['Total_Planned']
            * 100
        ).round(2)

        # =========================================
        # DEBUG OUTPUT
        # =========================================

        print("========== MERGED SAMPLE ==========")

        print(
            result_df[[
                'Unique_ID',
                'Load Quantity',
                'Actual_Qty',
                'Adherence_Ratio',
                'Result'
            ]].head(20)
        )

        # =========================================
        # RESPONSE
        # =========================================

        return jsonify({

            'summary': summary.to_dict(
                orient='records'
            ),

            'details': eligible_df[[
                'From Location',
                'To Location',
                'Material',
                'Load Quantity',
                'Actual_Qty',
                'Adherence_Ratio',
                'Result'
            ]].to_dict(orient='records')

        })

    except Exception as e:

        print("ERROR:", str(e))

        return jsonify({
            'error': str(e)
        })


# =========================================
# RUN
# =========================================

if __name__ == '__main__':

    app.run(
        debug=False,
        host='0.0.0.0',
        port=5000
    )

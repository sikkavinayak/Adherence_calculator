# Dispatch Adherence Calculator

A web app that calculates dispatch adherence from SAP APO and YVR18 exports.

## Calculation Logic

**Unique ID** = `To_Location + Excel_date_serial(From_Date) + Material`  
Links each APO planned line to its YVR18 actual dispatch record.

**Denominator (Check for inclusion):**  
APO rows where:
- A truck was actually dispatched (actual qty > 0)
- Planned Load Qty ≥ Category threshold

**Numerator (Result = 1):**  
Included rows where actual dispatch ≥ 80% of planned Load Qty

**Adherence % per RDC** = Numerator / Denominator × 100

### Category Thresholds
| Category | Threshold |
|---|---|
| Industrial | 1 |
| TBB, TBR, TRAC REAR, LTB, LTR-AS, TRAC FRONT, JEP, SCV Radial | 2 |
| ADV, Pickup Radial | 3 |
| PCR, SCV Bias | 4 |
| 2/3W, PCTR, Pouch tube | 5 |

---

## Running Locally

```bash
pip install -r requirements.txt
python app.py
```
Open http://localhost:5000

---

## Deploying on Render (Free)

1. Push this folder to a GitHub repo
2. Go to https://render.com → New → Web Service
3. Connect your GitHub repo
4. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT`
   - **Instance type:** Free
5. Click Deploy

---

## Deploying on Railway

1. Push to GitHub
2. Go to https://railway.app → New Project → Deploy from GitHub
3. Add environment variable: `PORT=8080`
4. Deploy — Railway auto-detects the Procfile

---

## Deploying on Heroku

```bash
heroku create dispatch-adherence
git push heroku main
heroku open
```

---

## File Upload Format

### APO Files (2 files — RDC and NDC)
Required columns:
- `From Location` — RDC code (e.g. ASR1)
- `To Location` — DO/ABU code (e.g. AS01)
- `Material` — SKU code
- `From Date` — planning date
- `Load Quantity` — planned quantity
- Optional: `Category`, `Threshold` (auto-derived from material if absent)

### YVR18 Files (2 files — RDC and NDC)
Required columns:
- `Deliv.Plant` — RDC code
- `R.Plnt / Cust Code` — DO code with ZC prefix (e.g. ZCAS01)
- `Mat.Code` — SKU code
- `Billing Dt` — dispatch date
- `Quantity` — actual dispatched quantity

# RoadPulse

RoadPulse is a municipal road surveillance dashboard for reporting potholes, mapping hotspots, tracking repair work, and verifying completion.

## Features
- Real-time pothole detection dashboard
- GPS-stamped road condition mapping
- Severity and priority scoring
- Duplicate and cluster detection
- Road-segment unevenness detection
- Heatmap overlay for recurring pothole hotspots
- Contractor work-order and repair proof workflow
- Municipality verification lifecycle
- Perungudi, Chennai-based demo data around 12.96095, 80.24094

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open:

```text
http://localhost:5000
```

## Default demo accounts

- admin / admin123
- public / public123
- contractor / contract123

These can be overridden with environment variables:

```bash
export ADMIN_PASSWORD=your-admin-password
export PUBLIC_PASSWORD=your-public-password
export CONTRACTOR_PASSWORD=your-contractor-password
```

## Project structure

```text
RoadPulse/
├── app.py
├── wsgi.py
├── requirements.txt
├── README.md
├── data/
│   └── roadpulse.db
├── templates/
│   └── dashboard.html
└── static/
```

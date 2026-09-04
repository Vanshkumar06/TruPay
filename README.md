# TruPay

## Transparent UPI Payment Sandbox with Smart Budgeting

TruPay is a hackathon-ready Django financial sandbox that blends explainable risk scoring, UPI-style transfers, budget management, and savings goals into one trust-first experience.

Built for the **Finvasia Hackathon** (Open Innovation Category), TruPay demonstrates how financial applications can be transparent, educational, and user-friendly.

---

## Problem Statement

Modern payment systems fail on three key fronts:

- **Lack of transparency**: Users do not understand why a payment is allowed or blocked.
- **Disconnected budgeting**: Payments and savings are treated separately, not as part of the same financial flow.
- **Opaque risk decisioning**: Fraud and trust scoring happen silently without meaningful feedback.

TruPay solves these gaps by providing a single platform where every transaction is analyzed, explained, and reflected against the user’s budget and goals.

---

## Solution Overview

TruPay is a Django web application that simulates a UPI-style payment platform while adding practical financial controls:

- **Explainable trust engine** that produces a risk score and human-readable rationale for each payment
- **Budget-aware approvals** that warn users when they exceed their monthly spending plan
- **Goal-based savings** so users can create, fund, and track progress toward financial targets
- **Audit log transparency** with a complete transaction history and decision details
- **Demo-ready onboarding** for judges and evaluators with seeded users and activity

---

## Key Features

- **UPI-style payment flow** with recipient selection, amount, category, and payment method
- **Risk preview** before completing a payment
- **Trust levels** classified as Low, Medium, or High
- **Contextual explanations** for each approval or confirmation requirement
- **Monthly budget tracking** with cycle resets and overspending alerts
- **Automatic savings recommendations** after transactions
- **Piggy bank savings goals** with priority, progress, and contributions
- **Audit trail** for transaction history, risk score, and advisory messages
- **TOTP security support** and backup-code handling

---

## ML Risk Engine and Technical Details

### Tech Stack

- **Backend**: Django (4.2+ compatible)
- **Database**: SQLite by default for local development
- **Frontend**: HTML5, CSS3, Vanilla JavaScript
- **ML / Data processing**: pandas for feature assembly and optional XGBoost model inference
- **Model loading**: optional `joblib` support for serialized `xgb_fraud_model.pkl` and `preprocessor.pkl`
- **Security**: Django auth, CSRF/session protection, optional TOTP

### Explainable Risk Model

TruPay ships with an explainable transaction risk engine in `core/risk_engine.py`.

- The engine builds a feature snapshot from each transaction, including:
  - payment amount vs historic average
  - recent transaction velocity (1h, 24h, 7d)
  - merchant category and payment channel
  - foreign / travel location flags
  - new payee / new device flags
  - budget remaining and overspending signals
  - receiver history count and merchant success history
- If serialized model artifacts are available under `core/ml_models/` or `models/`, TruPay uses them to infer a fraud probability with a trained XGBoost classifier.
- When model artifacts are absent, the app falls back to a deterministic behavioral scoring function that still evaluates the same explainable risk features.
- Each transaction analysis returns:
  - `trust_score` (0-100)
  - `trust_level` (Low / Medium / High)
  - `decision` type for the UI
  - `explanations` list of human-readable reasons
  - `advisory` guidance for spending and budget impact
  - `model_source` either `trained_xgboost` or `behavioral_fallback`

### Architecture

- `core/` contains models, views, forms, risk logic, and demo seed data
- `core/risk_engine.py` computes explainable transaction trust scores and handles both trained-model and fallback scoring
- `core/management/commands/seed_demo.py` creates demo accounts, goals, and transactions
- `trupay/settings.py` configures the app, database, and security settings
- `templates/core/` renders the UI pages for dashboard, transfers, goals, and audit log
- `static/` stores CSS, JavaScript, and PWA manifest assets

---

## Local Setup and Execution

### Prerequisites

- Python 3.10 or higher
- pip
- Git

### Installation

Open a terminal in the project root and run:

```bash
cd "c:\Users\vansh\Downloads\trupay_final\trupay 5"
python -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install django pandas
```

> If you add a `requirements.txt`, install with `pip install -r requirements.txt`.

### Database setup

```bash
python manage.py migrate
```

### Seed demo data

```bash
python manage.py seed_demo
```

### Run the app

```bash
python manage.py runserver
```

Open `http://127.0.0.1:8000` in your browser.

---

## Demo Credentials

The seeded demo command creates judge-ready accounts.

- **Showcase user**
  - Username: `showcase`
  - Password: `Hackathon@123`
  - UPI PIN: `2468`

- **Sample user**
  - Username: `alice`
  - Password: `Hackathon@123`
  - UPI PIN: `4321`

Other seeded accounts: `bob`, `charlie` (same password: `Hackathon@123`).

---

## Important Commands

- `python manage.py migrate` — apply database migrations
- `python manage.py createsuperuser` — create an administrative account
- `python manage.py seed_demo` — populate demo users, goals, and transactions
- `python manage.py runserver` — start the local server

---

## Project Files to Review

- `core/models.py` — data models and user profiles
- `core/views.py` — web page and API logic
- `core/forms.py` — payment and goal form validation
- `core/risk_engine.py` — explainable risk and trust scoring
- `core/tests.py` — test coverage for user flows
- `trupay/settings.py` — Django settings and security configuration

---

## Why TruPay?

TruPay demonstrates a complete payment experience with clear decisioning, budget awareness, and savings guidance. It is built for evaluation, extension, and rapid prototyping.

---

## Future Enhancements

- Add real UPI or payment gateway integration
- Add recurring payments and bill scheduling
- Build group/shared goals for family or team savings
- Improve risk scoring with production model training
- Create a mobile-friendly PWA or React-based front end

---

## License

This repository is intended for hackathon evaluation, learning, and prototyping. Extend it with proper security hardening before production use.

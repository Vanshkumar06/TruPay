from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import pandas as pd
from django.db.models import Avg
from django.utils import timezone

from .models import Transaction


MERCHANT_CHOICES = [
    ('grocery', 'Grocery'),
    ('electronics', 'Electronics'),
    ('travel', 'Travel'),
    ('restaurant', 'Restaurant'),
    ('atm', 'ATM'),
    ('online_retail', 'Online Retail'),
    ('gas_station', 'Gas Station'),
    ('pharmacy', 'Pharmacy'),
    ('entertainment', 'Entertainment'),
]

PAYMENT_METHOD_CHOICES = [
    ('UPI', 'UPI'),
    ('Wallet', 'Wallet'),
    ('Card', 'Card'),
    ('Bank Transfer', 'Bank Transfer'),
]

LOCATION_CHOICES = [
    ('home', 'Near home'),
    ('travel', 'Out of town'),
    ('foreign', 'International'),
]

MODEL_DIR_CANDIDATES = [
    Path(__file__).resolve().parents[2] / 'models',
    Path(__file__).resolve().parents[1] / 'models',
    Path(__file__).resolve().parent / 'ml_models',
]


@dataclass
class RiskAnalysis:
    risk_score: float
    risk_level: str
    decision: str
    explanations: list[str]
    advisory: str
    feature_snapshot: dict
    model_source: str

    @property
    def trust_score(self) -> int:
        return max(1, min(99, int(round((1 - self.risk_score) * 100))))


class RiskEngine:
    threshold_low = 0.35
    threshold_high = 0.70

    def analyze(self, user, receiver, payload: dict) -> RiskAnalysis:
        features = self._build_feature_snapshot(user, receiver, payload)
        score, model_source = self._predict_probability(features)
        risk_level = self._risk_level(score, features)
        explanations = self._build_explanations(features, risk_level)
        advisory = self._build_advisory(user.profile, payload, risk_level)
        decision = self._decision_for_level(risk_level)
        return RiskAnalysis(
            risk_score=round(score, 4),
            risk_level=risk_level,
            decision=decision,
            explanations=explanations,
            advisory=advisory,
            feature_snapshot=features,
            model_source=model_source,
        )

    def _build_feature_snapshot(self, user, receiver, payload: dict) -> dict:
        profile = user.profile
        profile.refresh_budget_cycle()

        now = timezone.localtime()
        recent_transactions = Transaction.objects.filter(
            sender=user,
            timestamp__gte=now - timedelta(days=30),
        ).order_by('-timestamp')
        sent_successful = recent_transactions.filter(status='SUCCESS')
        last_transaction = sent_successful.first()

        amount = Decimal(payload['amount'])
        velocity_1h = sent_successful.filter(timestamp__gte=now - timedelta(hours=1)).count()
        velocity_24h = sent_successful.filter(timestamp__gte=now - timedelta(hours=24)).count()
        velocity_7d = sent_successful.filter(timestamp__gte=now - timedelta(days=7)).count()
        avg_amount_30d = sent_successful.aggregate(value=Avg('amount'))['value'] or amount
        avg_amount_30d = Decimal(avg_amount_30d)
        amount_vs_avg_ratio = float(amount / avg_amount_30d) if avg_amount_30d else 1.0
        prior_fraud_count = recent_transactions.filter(risk_level='High').count()

        if last_transaction:
            time_since_last_tx_hours = round(
                (now - timezone.localtime(last_transaction.timestamp)).total_seconds() / 3600,
                2,
            )
        else:
            time_since_last_tx_hours = 72.0

        merchant_category = payload['merchant_category']
        payment_method = payload['payment_method']
        location = payload['location']
        is_foreign = int(location == 'foreign')
        distance_from_home = 8.0 if location == 'home' else 120.0 if location == 'travel' else 2800.0
        is_new_device = int(payload.get('is_new_device', False))
        is_high_value_merchant = int(merchant_category in {'electronics', 'travel', 'atm'})
        budget_remaining = profile.budget_remaining
        overspending_amount = (profile.spent_this_cycle + amount) - profile.monthly_budget
        is_overspending = int(overspending_amount > 0)
        merchant_success_count = sent_successful.filter(merchant_category=merchant_category).count()

        receiver_history_count = 0
        if getattr(receiver, 'pk', None):
            if receiver.__class__.__name__ == 'ExternalRecipient':
                receiver_history_count = Transaction.objects.filter(sender=user, external_receiver=receiver).count()
            else:
                receiver_history_count = Transaction.objects.filter(sender=user, receiver=receiver).count()

        return {
            'amount': float(amount),
            'hour': now.hour,
            'day_of_week': now.weekday(),
            'distance_from_home': distance_from_home,
            'velocity_1h': velocity_1h,
            'velocity_24h': velocity_24h,
            'velocity_7d': velocity_7d,
            'avg_amount_30d': float(avg_amount_30d),
            'amount_vs_avg_ratio': round(amount_vs_avg_ratio, 3),
            'prior_fraud_count': prior_fraud_count,
            'account_age_days': max((now.date() - user.date_joined.date()).days, 1),
            'time_since_last_tx_hours': time_since_last_tx_hours,
            'merchant_category': merchant_category,
            'device_type': self._device_type_for_method(payment_method),
            'channel': self._channel_for_method(payment_method),
            'is_foreign': is_foreign,
            'is_weekend': int(now.weekday() >= 5),
            'is_night': int(now.hour >= 23 or now.hour <= 5),
            'is_new_device': is_new_device,
            'is_high_value_merchant': is_high_value_merchant,
            'is_overspending': is_overspending,
            'budget_remaining': float(budget_remaining),
            'overspending_amount': float(overspending_amount) if overspending_amount > 0 else 0.0,
            'receiver_history_count': receiver_history_count,
            'receiver_is_external': int(receiver.__class__.__name__ == 'ExternalRecipient'),
            'merchant_success_count': merchant_success_count,
        }

    def _predict_probability(self, features: dict) -> tuple[float, str]:
        artifact_bundle = _load_artifacts()
        if artifact_bundle:
            model, preprocessor = artifact_bundle
            frame = pd.DataFrame([{
                key: features[key]
                for key in [
                    'amount', 'hour', 'day_of_week', 'distance_from_home',
                    'velocity_1h', 'velocity_24h', 'velocity_7d',
                    'avg_amount_30d', 'amount_vs_avg_ratio', 'prior_fraud_count',
                    'account_age_days', 'time_since_last_tx_hours',
                    'merchant_category', 'device_type', 'channel',
                    'is_foreign', 'is_weekend', 'is_night', 'is_new_device',
                    'is_high_value_merchant', 'is_overspending',
                ]
            }])
            transformed = preprocessor.transform(frame)
            score = float(model.predict_proba(transformed)[0, 1])
            return score, 'trained_xgboost'

        score = 0.08
        score += min(features['amount_vs_avg_ratio'] / 16, 0.22)
        score += min(features['velocity_24h'] / 20, 0.14)
        score += min(features['distance_from_home'] / 4000, 0.14)
        score += 0.10 if features['is_new_device'] else 0.0
        score += 0.12 if features['is_foreign'] else 0.0
        score += 0.09 if features['is_night'] else 0.0
        score += 0.12 if features['is_overspending'] else 0.0
        score += 0.07 if features['receiver_history_count'] == 0 else -0.03
        score += 0.03 if features['receiver_is_external'] else -0.02
        score -= min(features['merchant_success_count'] / 18, 0.08)
        score += 0.05 if features['merchant_category'] in {'atm', 'electronics', 'travel'} else 0.0
        return max(0.02, min(score, 0.98)), 'behavioral_fallback'

    def _build_explanations(self, features: dict, risk_level: str) -> list[str]:
        reasons = []
        if features['amount_vs_avg_ratio'] >= 2:
            reasons.append('This amount is much higher than your usual spending.')
        if features['receiver_history_count'] == 0:
            reasons.append('This payee is new for your account, so TruPay is being a little more careful.')
        if features['is_foreign']:
            reasons.append('The payment is being made from an unusual location.')
        elif features['distance_from_home'] >= 100:
            reasons.append('The payment is happening away from your normal area.')
        if features['is_new_device']:
            reasons.append('A new device is being used for this payment.')
        if features['merchant_success_count'] >= 3 and risk_level == 'Low':
            reasons.append('You have handled this kind of payment safely before.')
        if features['velocity_24h'] >= 5:
            reasons.append('You have made several payments recently, which raises risk.')
        if features['is_overspending']:
            reasons.append('This payment pushes you beyond the current budget cycle.')
        if features['is_night']:
            reasons.append('Late-night payments are treated as slightly riskier.')
        if not reasons:
            reasons.append('This transaction matches your usual payment pattern and looks familiar.')
        return reasons[:3] if risk_level != 'High' else reasons[:4]

    def _build_advisory(self, profile, payload: dict, risk_level: str) -> str:
        amount = Decimal(payload['amount'])
        budget_after_payment = profile.monthly_budget - (profile.spent_this_cycle + amount)
        if budget_after_payment >= 0:
            return (
                f"After this payment, you will still have Rs.{budget_after_payment:.2f} "
                "left in this budget cycle."
            )

        borrowed = abs(budget_after_payment)
        if risk_level == 'High':
            return (
                f"This payment would borrow Rs.{borrowed:.2f} from your next budget cycle "
                "and is being treated as high risk."
            )
        return (
            f"This payment exceeds the active budget by Rs.{borrowed:.2f}. "
            "TruPay can still continue, but that amount will be borrowed from the next cycle."
        )

    def _decision_for_level(self, risk_level: str) -> str:
        return {
            'Low': 'approved_silent',
            'Medium': 'warning_confirmation',
            'High': 'high_risk_confirmation',
        }[risk_level]

    def _risk_level(self, score: float, features: dict) -> str:
        escalation_signals = sum([
            int(features['receiver_history_count'] == 0),
            int(features['is_foreign']),
            int(features['is_overspending']),
            int(features['is_new_device']),
            int(features['amount_vs_avg_ratio'] >= 2.2),
            int(features['velocity_24h'] >= 5),
        ])

        if score >= 0.60 and escalation_signals >= 2:
            return 'High'
        if score >= 0.55 and escalation_signals >= 3:
            return 'High'
        if score >= 0.48 and features['is_foreign'] and features['is_overspending'] and features['receiver_history_count'] == 0:
            return 'High'
        if score < self.threshold_low:
            return 'Low'
        if score < self.threshold_high:
            return 'Medium'
        return 'High'

    def _device_type_for_method(self, payment_method: str) -> str:
        return {
            'UPI': 'mobile',
            'Wallet': 'mobile',
            'Card': 'pos_terminal',
            'Bank Transfer': 'desktop',
        }.get(payment_method, 'mobile')

    def _channel_for_method(self, payment_method: str) -> str:
        return {
            'UPI': 'app',
            'Wallet': 'app',
            'Card': 'branch',
            'Bank Transfer': 'web',
        }.get(payment_method, 'app')


@lru_cache(maxsize=1)
def _load_artifacts():
    try:
        import joblib
    except ImportError:
        return None

    for model_dir in MODEL_DIR_CANDIDATES:
        model_path = model_dir / 'xgb_fraud_model.pkl'
        preprocessor_path = model_dir / 'preprocessor.pkl'
        if model_path.exists() and preprocessor_path.exists():
            return joblib.load(model_path), joblib.load(preprocessor_path)
    return None

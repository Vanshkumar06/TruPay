import math
from calendar import month_abbr
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import models
from django.db import transaction as db_transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .auth_utils import build_otpauth_uri, generate_backup_codes, generate_totp_secret, verify_totp
from .forms import AccountSettingsForm, GoalForm, LoginForm, ProfileSettingsForm, SignupForm, SurplusAllocationForm, TransferForm, TwoFactorForm
from .models import ExternalRecipient, Goal, GoalContribution, GoalInvite, Transaction
from .risk_engine import RiskEngine


risk_engine = RiskEngine()
LOGIN_ATTEMPT_LIMIT = 5
LOGIN_LOCK_MINUTES = 10
BALANCE_PIN_ATTEMPT_LIMIT = 5
BALANCE_PIN_LOCK_MINUTES = 10


def landing_page(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'core/landing.html', {
        'feature_cards': [
            {
                'title': 'Payment sandbox',
                'description': 'Simulate UPI-style transfers and review safety checks before money moves.',
            },
            {
                'title': 'Explainable safety engine',
                'description': 'Plain-language approval guidance for every payment.',
            },
            {
                'title': 'Goals and savings',
                'description': 'Link payments to savings targets and keep your budget on track.',
            },
        ],
    })


def signup(request):
    if request.method == 'POST':
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            user.email = form.cleaned_data.get('email', '')
            user.save(update_fields=['email'])
            user.profile.upi_pin = form.cleaned_data['upi_pin']
            user.profile.save(update_fields=['upi_pin'])
            login(request, user)
            return redirect('dashboard')
    else:
        form = SignupForm()
    return render(request, 'core/signup.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    form = LoginForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        username = form.cleaned_data['username']
        cache_key = f'trupay_login_lock:{username.lower()}'
        attempts_key = f'trupay_login_attempts:{username.lower()}'
        if cache.get(cache_key):
            messages.error(request, 'Too many login attempts. Please wait a few minutes and try again.')
            return render(request, 'core/login.html', {'form': form})

        user = form.get_user()
        cache.delete(attempts_key)
        if user.profile.totp_enabled:
            request.session['pending_2fa_user_id'] = user.id
            request.session['pending_2fa_backend'] = user.backend
            return redirect('verify_2fa')
        login(request, user, backend=user.backend)
        return redirect('dashboard')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip().lower()
        if username:
            attempts_key = f'trupay_login_attempts:{username}'
            cache_key = f'trupay_login_lock:{username}'
            attempts = cache.get(attempts_key, 0) + 1
            cache.set(attempts_key, attempts, timeout=LOGIN_LOCK_MINUTES * 60)
            if attempts >= LOGIN_ATTEMPT_LIMIT:
                cache.set(cache_key, True, timeout=LOGIN_LOCK_MINUTES * 60)
                messages.error(request, 'Too many failed logins. Account access is temporarily paused.')

    return render(request, 'core/login.html', {'form': form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect('login')


def verify_2fa_view(request):
    user_id = request.session.get('pending_2fa_user_id')
    backend = request.session.get('pending_2fa_backend')
    if not user_id or not backend:
        return redirect('login')

    user = get_object_or_404(User, id=user_id)
    form = TwoFactorForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        code = form.cleaned_data['code'].strip().upper()
        valid_totp = verify_totp(user.profile.totp_secret, code)
        valid_backup = code in user.profile.backup_codes
        if valid_totp or valid_backup:
            if valid_backup:
                user.profile.backup_codes = [item for item in user.profile.backup_codes if item != code]
                user.profile.save(update_fields=['backup_codes'])
            login(request, user, backend=backend)
            request.session.pop('pending_2fa_user_id', None)
            request.session.pop('pending_2fa_backend', None)
            messages.success(request, 'Two-factor authentication verified.')
            return redirect('dashboard')
        messages.error(request, 'Invalid authenticator code or backup code.')
    return render(request, 'core/verify_2fa.html', {'form': form})


def demo_overview_api(request):
    user_count = User.objects.count()
    tx_count = Transaction.objects.count()
    goal_count = Goal.objects.count()
    high_risk = Transaction.objects.filter(risk_level='High').count()
    return JsonResponse({
        'name': 'TruPay',
        'status': 'ok',
        'users': user_count,
        'transactions': tx_count,
        'goals': goal_count,
        'high_risk_transactions': high_risk,
        'features': [
            'payment_sandbox',
            'risk_engine',
            'explainability',
            'budgeting',
            'goal_tracking',
            'audit_log',
        ],
    })


@login_required
def user_summary_api(request):
    profile = request.user.profile
    recent_transactions = list(_base_transaction_queryset(request.user)[:5])
    return JsonResponse({
        'user': request.user.username,
        'wallet_balance': float(profile.wallet_balance),
        'budget_remaining': float(profile.budget_remaining),
        'savings_balance': float(profile.savings_balance),
        'recent_transactions': [
            {
                'counterparty': tx.counterparty_name if tx.sender_id == request.user.id else tx.sender.username,
                'amount': float(tx.amount),
                'risk_level': tx.risk_level,
                'decision': tx.decision,
            }
            for tx in recent_transactions
        ],
    })


def _base_transaction_queryset(user):
    return (Transaction.objects.filter(sender=user) | Transaction.objects.filter(receiver=user)).select_related(
        'sender', 'receiver', 'external_receiver'
    ).order_by('-timestamp')


def _redirect_back(request, fallback):
    next_url = request.POST.get('next') or request.GET.get('next') or request.META.get('HTTP_REFERER')
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        parsed = urlparse(next_url)
        target = parsed.path or next_url
        if parsed.query:
            target = f'{target}?{parsed.query}'
        return redirect(target)
    return redirect(fallback)


def _get_or_create_receiver(username):
    normalized = username.strip().replace('@trupay', '')
    if not normalized:
        raise ValueError('Receiver username is required.')
    receiver = User.objects.filter(username=normalized).first()
    if receiver:
        return receiver, None

    external_handle = f'{normalized}@external'
    external_receiver, _ = ExternalRecipient.objects.get_or_create(
        handle=external_handle,
        defaults={'display_name': normalized},
    )
    return None, external_receiver


def _get_receiver_for_analysis(username):
    normalized = username.strip().replace('@trupay', '')
    if not normalized:
        raise ValueError('Receiver username is required.')
    receiver = User.objects.filter(username=normalized).first()
    if receiver:
        return receiver

    return ExternalRecipient(handle=f'{normalized}@external', display_name=normalized)


def _parse_amount(raw_value):
    try:
        amount = Decimal(str(raw_value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def _model_source_label(source):
    return {
        'trained_xgboost': 'Learned trust model',
        'behavioral_fallback': 'Behavioral trust engine',
    }.get(source, 'Trust engine')


def _goal_projection(goals, monthly_saving_capacity):
    projections = []
    for goal in goals:
        months_to_goal = (
            math.ceil(goal.remaining_amount / monthly_saving_capacity)
            if monthly_saving_capacity > 0 and goal.remaining_amount > 0 else None
        )
        projections.append({'goal': goal, 'months_to_goal': months_to_goal})
    return projections


def _advisory_cards(profile, transactions, goals):
    cards = []
    recent_sent = [tx for tx in transactions if tx.sender_id == profile.user_id]
    restaurant_spend = sum(
        tx.amount for tx in recent_sent
        if tx.merchant_category == 'restaurant' and tx.status == 'SUCCESS'
    )
    if restaurant_spend:
        cards.append(
            f"Reducing restaurant spending by Rs.{restaurant_spend / 4:.2f} this month would protect more of your budget."
        )

    if profile.borrowed_from_next_cycle > 0:
        cards.append(
            f"You have already borrowed Rs.{profile.borrowed_from_next_cycle} from the next cycle. A few lighter payments now will recover faster."
        )

    nearest_goal = next((goal for goal in goals if goal.remaining_amount > 0), None)
    if nearest_goal:
        cards.append(
            f"Adding Rs.{min(nearest_goal.remaining_amount, Decimal('500.00'))} toward '{nearest_goal.title}' would move the goal meaningfully closer."
        )

    if not cards:
        cards.append('Your current spending pattern is healthy. TruPay can keep approvals mostly frictionless at this pace.')
    return cards[:3]


def _transparency_items():
    return [
        {
            'title': 'Transaction pattern',
            'description': 'Amount, merchant type, and payment method are compared against your recent behavior to identify abnormal spending.',
            'purpose': 'Risk detection and trust scoring',
        },
        {
            'title': 'Context signals',
            'description': 'Time of day, location, and whether the payment is from a new device help TruPay spot unusual payment contexts.',
            'purpose': 'Real-time warning or blocking decisions',
        },
        {
            'title': 'Budget and goals',
            'description': 'Budget usage, future borrowing, and goal progress help TruPay explain the downstream impact of each payment.',
            'purpose': 'Advisory guidance and savings recommendations',
        },
    ]


@login_required
def dashboard(request):
    profile = request.user.profile
    profile.refresh_budget_cycle()
    transactions = list(_base_transaction_queryset(request.user)[:10])
    goals = list(Goal.objects.filter(user=request.user).order_by('-created_at'))
    monthly_saving_capacity = max(profile.monthly_budget - profile.spent_this_cycle, Decimal('0.00'))

    return render(request, 'core/dashboard.html', {
        'profile': profile,
        'transactions': transactions,
        'goals': goals,
        'goal_projection': _goal_projection(goals, monthly_saving_capacity),
        'high_risk_count': sum(1 for tx in transactions if tx.risk_level == 'High'),
        'transparency_items': _transparency_items(),
        'advisory_cards': _advisory_cards(profile, transactions, goals),
        'cycle_days_left': profile.budget_cycle_days_left,
        'surplus_form': SurplusAllocationForm(user=request.user, initial={'amount': profile.budget_remaining}),
    })


@login_required
def analytics(request):
    profile = request.user.profile
    profile.refresh_budget_cycle()
    transactions = list(_base_transaction_queryset(request.user)[:80])
    sent_transactions = [tx for tx in transactions if tx.sender_id == request.user.id]
    category_totals = Counter()
    risk_counts = Counter()
    blocked_count = 0
    monthly_lookup = {}
    now = timezone.localtime()
    current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_starts = []
    for offset in range(5, -1, -1):
        month = current_month_start.month - offset
        year = current_month_start.year
        while month <= 0:
            month += 12
            year -= 1
        month_starts.append((year, month))

    for year, month in month_starts:
        monthly_lookup[(year, month)] = {
            'label': f"{month_abbr[month]} '{str(year)[-2:]}",
            'spent': Decimal('0.00'),
            'count': 0,
            'trust_total': 0,
        }

    for tx in sent_transactions:
        category_totals[tx.merchant_category] += float(tx.amount)
        risk_counts[tx.risk_level] += 1
        if tx.status == 'BLOCKED':
            blocked_count += 1
        key = (tx.timestamp.year, tx.timestamp.month)
        if key in monthly_lookup and tx.status == 'SUCCESS':
            monthly_lookup[key]['spent'] += tx.amount
            monthly_lookup[key]['count'] += 1
            monthly_lookup[key]['trust_total'] += tx.trust_score

    top_categories = sorted(category_totals.items(), key=lambda item: item[1], reverse=True)[:5]
    spend_total = sum(Decimal(str(amount)) for _, amount in top_categories) or Decimal('0.00')
    category_breakdown = [
        {
            'category': category.replace('_', ' ').title(),
            'amount': Decimal(str(amount)).quantize(Decimal('0.01')),
            'share': round((amount / float(spend_total)) * 100, 1) if spend_total else 0,
        }
        for category, amount in top_categories
    ]
    monthly_spend_peak = max((bucket['spent'] for bucket in monthly_lookup.values()), default=Decimal('1.00')) or Decimal('1.00')
    monthly_summary = []
    budget_success_months = 0
    for bucket in monthly_lookup.values():
        avg_trust = round(bucket['trust_total'] / bucket['count'], 1) if bucket['count'] else 0
        on_budget = bucket['spent'] <= profile.monthly_budget if profile.monthly_budget > 0 else False
        if bucket['count'] and on_budget:
            budget_success_months += 1
        monthly_summary.append({
            'label': bucket['label'],
            'spent': bucket['spent'].quantize(Decimal('0.01')),
            'transaction_count': bucket['count'],
            'avg_trust': avg_trust,
            'bar_width': round((bucket['spent'] / monthly_spend_peak) * 100, 1) if monthly_spend_peak else 0,
            'on_budget': on_budget,
        })

    avg_trust_score = round(sum(tx.trust_score for tx in sent_transactions) / len(sent_transactions), 1) if sent_transactions else 0
    trusted_months = sum(1 for bucket in monthly_summary if bucket['avg_trust'] >= 70)

    return render(request, 'core/analytics.html', {
        'profile': profile,
        'transactions': transactions[:12],
        'category_breakdown': category_breakdown,
        'risk_counts': {
            'low': risk_counts.get('Low', 0),
            'medium': risk_counts.get('Medium', 0),
            'high': risk_counts.get('High', 0),
        },
        'blocked_count': blocked_count,
        'avg_trust_score': avg_trust_score,
        'monthly_summary': monthly_summary,
        'budget_success_months': budget_success_months,
        'trusted_months': trusted_months,
        'advisory_cards': _advisory_cards(profile, transactions, Goal.objects.filter(user=request.user)),
    })


@login_required
def goals(request):
    profile = request.user.profile
    profile.refresh_budget_cycle()
    goals = list(
        Goal.objects.filter(
            models.Q(user=request.user)
            | models.Q(contributions__contributor=request.user)
            | models.Q(invites__invited_user=request.user, invites__status='accepted')
        ).distinct().order_by('-created_at')
    )
    monthly_saving_capacity = max(profile.monthly_budget - profile.spent_this_cycle, Decimal('0.00'))
    incoming_invites = GoalInvite.objects.filter(invited_user=request.user, status='pending')
    pending_invites = GoalInvite.objects.filter(goal__user=request.user, status='pending')

    return render(request, 'core/goals.html', {
        'profile': profile,
        'goals': goals,
        'goal_projection': _goal_projection(goals, monthly_saving_capacity),
        'surplus_form': SurplusAllocationForm(user=request.user, initial={'amount': profile.budget_remaining}),
        'incoming_invites': incoming_invites,
        'pending_invites': pending_invites,
    })


@login_required
def transparency(request):
    transactions = _base_transaction_queryset(request.user)[:12]
    return render(request, 'core/transparency.html', {
        'transparency_items': _transparency_items(),
        'transactions': transactions,
    })


@login_required
def settings_view(request):
    profile = request.user.profile
    account_form = AccountSettingsForm(instance=request.user)
    form = ProfileSettingsForm(instance=profile)
    pending_secret = request.session.get('pending_totp_secret')
    backup_codes = request.session.get('new_backup_codes', [])
    if request.method == 'POST':
        action = request.POST.get('action', 'profile')
        if action == 'account':
            account_form = AccountSettingsForm(request.POST, instance=request.user)
            if account_form.is_valid():
                account_form.save()
                messages.success(request, 'Your account details have been updated.')
                return redirect('settings')
        elif action == 'profile':
            form = ProfileSettingsForm(request.POST, instance=profile)
            if form.is_valid():
                form.save()
                messages.success(request, 'Your TruPay settings have been updated.')
                return redirect('settings')
        elif action == 'start_2fa':
            secret = generate_totp_secret()
            request.session['pending_totp_secret'] = secret
            messages.success(request, 'Scan the secret with your authenticator app, then confirm with a 6-digit code.')
            return redirect('settings')
        elif action == 'confirm_2fa':
            secret = request.session.get('pending_totp_secret')
            code = request.POST.get('totp_code', '').strip()
            if secret and verify_totp(secret, code):
                profile.totp_secret = secret
                profile.totp_enabled = True
                codes = generate_backup_codes()
                profile.backup_codes = codes
                profile.save(update_fields=['totp_secret', 'totp_enabled', 'backup_codes'])
                request.session.pop('pending_totp_secret', None)
                request.session['new_backup_codes'] = codes
                messages.success(request, 'Authenticator-based 2FA is now enabled.')
                return redirect('settings')
            messages.error(request, 'The authenticator code did not match. Please try again.')
        elif action == 'disable_2fa':
            profile.totp_secret = ''
            profile.totp_enabled = False
            profile.backup_codes = []
            profile.save(update_fields=['totp_secret', 'totp_enabled', 'backup_codes'])
            request.session.pop('pending_totp_secret', None)
            request.session.pop('new_backup_codes', None)
            messages.success(request, 'Two-factor authentication has been disabled.')
            return redirect('settings')
        elif action == 'refresh_backup_codes':
            codes = generate_backup_codes()
            profile.backup_codes = codes
            profile.save(update_fields=['backup_codes'])
            request.session['new_backup_codes'] = codes
            messages.success(request, 'Generated a new set of backup codes.')
            return redirect('settings')
    otp_secret = pending_secret or profile.totp_secret
    return render(request, 'core/settings.html', {
        'account_form': account_form,
        'form': form,
        'profile': profile,
        'otp_secret': otp_secret,
        'otp_uri': build_otpauth_uri(request.user.username, otp_secret) if otp_secret else '',
        'pending_totp_secret': pending_secret,
        'backup_codes': backup_codes,
    })


@login_required
def audit_log(request):
    transactions = _base_transaction_queryset(request.user)[:50]
    return render(request, 'core/audit_log.html', {'transactions': transactions})


@login_required
def check_balance(request):
    if request.method == 'POST':
        cache_key = f'trupay_balance_pin_lock:{request.user.id}'
        attempts_key = f'trupay_balance_pin_attempts:{request.user.id}'
        if cache.get(cache_key):
            return JsonResponse({
                'success': False,
                'error': 'Too many incorrect PIN attempts. Please wait a few minutes and try again.',
            }, status=429)

        pin = request.POST.get('password')
        if pin == request.user.profile.upi_pin:
            cache.delete(attempts_key)
            return JsonResponse({'success': True, 'balance': str(request.user.profile.wallet_balance)})
        attempts = cache.get(attempts_key, 0) + 1
        cache.set(attempts_key, attempts, timeout=BALANCE_PIN_LOCK_MINUTES * 60)
        if attempts >= BALANCE_PIN_ATTEMPT_LIMIT:
            cache.set(cache_key, True, timeout=BALANCE_PIN_LOCK_MINUTES * 60)
        return JsonResponse({'success': False, 'error': 'Incorrect 4-digit TruPay PIN.'})
    return JsonResponse({'success': False, 'error': 'Invalid request'})


@login_required
def analyze_payment(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request'})

    if request.user.profile.monthly_budget <= 0:
        return JsonResponse({
            'success': False,
            'error': 'Set your budget in Profile before making a transfer.',
        }, status=400)

    form = TransferForm(request.POST, user=request.user)
    if not form.is_valid():
        return JsonResponse({'success': False, 'errors': form.errors.get_json_data()}, status=400)

    receiver = _get_receiver_for_analysis(form.cleaned_data['receiver_username'])
    if isinstance(receiver, User) and receiver.username == request.user.username:
        return JsonResponse({'success': False, 'error': 'You cannot send money to yourself.'}, status=400)

    analysis = risk_engine.analyze(request.user, receiver, form.cleaned_data)
    return JsonResponse({
        'success': True,
        'risk_level': analysis.risk_level,
        'risk_score': analysis.risk_score,
        'trust_score': analysis.trust_score,
        'decision': analysis.decision,
        'explanations': analysis.explanations,
        'advisory': analysis.advisory,
        'model_source': analysis.model_source,
        'model_label': _model_source_label(analysis.model_source),
    })


@login_required
def send_money(request):
    risk_result = None
    require_confirmation = False
    sender_profile = request.user.profile
    sender_profile.refresh_budget_cycle()
    user_goals = list(Goal.objects.filter(user=request.user).order_by('-created_at'))

    if sender_profile.monthly_budget <= 0:
        messages.error(request, 'Set your budget in Profile before making a transfer.')
        return redirect('profile')

    if request.method == 'POST':
        form = TransferForm(request.POST, user=request.user)
        if form.is_valid():
            receiver, external_receiver = _get_or_create_receiver(form.cleaned_data['receiver_username'])
            receiver_profile = receiver.profile if receiver else None

            if receiver and sender_profile.user == receiver:
                messages.error(request, 'You cannot send money to yourself.')
                return redirect('send_money')

            amount = form.cleaned_data['amount']
            if sender_profile.wallet_balance < amount:
                messages.error(request, 'Insufficient balance.')
                return render(request, 'core/send_money.html', {'form': form})

            upi_pin = form.cleaned_data.get('upi_pin')
            if not upi_pin:
                form.add_error('upi_pin', 'Enter your 4-digit UPI PIN to authorize the payment.')
            elif upi_pin != sender_profile.upi_pin:
                form.add_error('upi_pin', 'Incorrect UPI PIN.')

            if form.errors:
                return render(request, 'core/send_money.html', {'form': form})

            analysis_target = receiver or external_receiver
            analysis = risk_engine.analyze(request.user, analysis_target, form.cleaned_data)
            risk_result = {
                'risk_level': analysis.risk_level,
                'risk_score': analysis.risk_score,
                'trust_score': analysis.trust_score,
                'decision': analysis.decision,
                'explanations': analysis.explanations,
                'advisory': analysis.advisory,
                'model_source': analysis.model_source,
                'model_label': _model_source_label(analysis.model_source),
            }

            if analysis.risk_level == 'High' and not form.cleaned_data.get('risk_acknowledged'):
                require_confirmation = True
            else:
                try:
                    _process_transaction(
                        request=request,
                        receiver=receiver,
                        external_receiver=external_receiver,
                        sender_profile=sender_profile,
                        receiver_profile=receiver_profile,
                        form=form,
                        analysis=analysis,
                    )
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return render(request, 'core/send_money.html', {'form': form, 'goals': user_goals})
                return _redirect_back(request, 'send_money')
    else:
        form = TransferForm(user=request.user)

    return render(request, 'core/send_money.html', {
        'form': form,
        'risk_result': risk_result,
        'require_confirmation': require_confirmation,
        'goals': user_goals,
    })


def _process_transaction(request, receiver, external_receiver, sender_profile, receiver_profile, form, analysis):
    amount = form.cleaned_data['amount']
    roundup_amount = Decimal('0.00')
    roundup_goal = form.cleaned_data.get('roundup_goal')
    roundup_destination = form.cleaned_data.get('roundup_destination') or 'savings'
    if form.cleaned_data.get('enable_roundup'):
        rounded_target = (amount / Decimal('10')).to_integral_value(rounding=ROUND_CEILING) * Decimal('10')
        roundup_amount = rounded_target - amount
        if roundup_amount == Decimal('10'):
            roundup_amount = Decimal('0.00')
    total_debit = amount + roundup_amount

    with db_transaction.atomic():
        sender_profile.refresh_budget_cycle()
        if sender_profile.wallet_balance < total_debit:
            raise ValueError('Insufficient balance for payment and roundup.')

        sender_profile.wallet_balance -= amount
        sender_profile.apply_spend(amount)
        if roundup_amount > 0:
            sender_profile.wallet_balance -= roundup_amount
            if roundup_destination == 'goal' and roundup_goal:
                roundup_goal.saved_amount += roundup_amount
                roundup_goal.save(update_fields=['saved_amount'])
                contribution, _ = GoalContribution.objects.get_or_create(
                    goal=roundup_goal,
                    contributor=request.user,
                    defaults={'amount_contributed': Decimal('0.00')},
                )
                contribution.amount_contributed += roundup_amount
                contribution.save(update_fields=['amount_contributed'])
            else:
                sender_profile.savings_balance += roundup_amount
        sender_profile.save()

        if receiver_profile:
            receiver_profile.wallet_balance += amount
            receiver_profile.save(update_fields=['wallet_balance'])

        Transaction.objects.create(
            sender=request.user,
            receiver=receiver,
            external_receiver=external_receiver,
            amount=amount,
            status='SUCCESS',
            merchant_category=form.cleaned_data['merchant_category'],
            payment_method=form.cleaned_data['payment_method'],
            risk_score=Decimal(str(analysis.risk_score)),
            risk_level=analysis.risk_level,
            decision='approved_with_confirmation' if analysis.risk_level == 'High' else analysis.decision,
            explanations=analysis.explanations,
            advisory=analysis.advisory,
            risk_snapshot=analysis.feature_snapshot,
        )


@login_required
def allocate_surplus(request):
    if request.method != 'POST':
        return redirect('dashboard')

    profile = request.user.profile
    profile.refresh_budget_cycle()
    form = SurplusAllocationForm(request.POST, user=request.user)
    if not form.is_valid():
        messages.error(request, 'Please choose a valid surplus allocation option.')
        return _redirect_back(request, 'dashboard')

    amount = form.cleaned_data['amount']
    if amount > profile.budget_remaining:
        messages.error(request, 'That amount is larger than the available budget surplus.')
        return _redirect_back(request, 'dashboard')

    action = form.cleaned_data['action']
    goal = form.cleaned_data.get('goal')
    if action in {'savings', 'goal'} and amount > profile.wallet_balance:
        messages.error(request, 'Wallet balance is too low to allocate that surplus amount.')
        return _redirect_back(request, 'dashboard')

    if action == 'savings':
        profile.savings_balance += amount
        profile.wallet_balance -= amount
        profile.save(update_fields=['savings_balance', 'wallet_balance'])
        messages.success(request, f'Moved Rs.{amount} into your smart savings reserve.')
    elif action == 'goal' and goal:
        profile.wallet_balance -= amount
        profile.save(update_fields=['wallet_balance'])
        goal.saved_amount += amount
        goal.save(update_fields=['saved_amount'])
        contribution, _ = GoalContribution.objects.get_or_create(
            goal=goal,
            contributor=request.user,
            defaults={'amount_contributed': Decimal('0.00')},
        )
        contribution.amount_contributed += amount
        contribution.save(update_fields=['amount_contributed'])
        messages.success(request, f'Moved Rs.{amount} from your surplus into {goal.title}.')
    else:
        messages.success(request, 'Surplus will remain available for the next spending cycle.')
    return _redirect_back(request, 'dashboard')


@login_required
def confirm_roundup(request):
    if request.method == 'POST':
        amount = _parse_amount(request.POST.get('amount', 0))
        profile = request.user.profile

        if amount is None:
            messages.error(request, 'Enter a valid savings amount.')
        else:
            if profile.wallet_balance >= amount:
                with db_transaction.atomic():
                    profile.wallet_balance -= amount
                    profile.savings_balance += amount
                    profile.save(update_fields=['wallet_balance', 'savings_balance'])
                messages.success(request, f"Saved Rs.{amount} into smart savings.")
            else:
                messages.error(request, 'Not enough balance to save that amount after the transfer.')
    return _redirect_back(request, 'dashboard')


@login_required
def create_goal(request):
    if request.method == 'POST':
        form = GoalForm(request.POST)
        if form.is_valid():
            goal = form.save(commit=False)
            goal.user = request.user
            goal.save()
            GoalContribution.objects.create(goal=goal, contributor=request.user)
            messages.success(request, 'Piggy Bank created.')
        else:
            messages.error(request, 'Please check your goal details and try again.')
    return _redirect_back(request, 'dashboard')


@login_required
def fund_goal(request, goal_id):
    if request.method == 'POST':
        amount = _parse_amount(request.POST.get('amount', 0))
        goal = get_object_or_404(Goal, id=goal_id)
        profile = request.user.profile
        allowed = (
            goal.user == request.user
            or goal.contributions.filter(contributor=request.user).exists()
            or GoalInvite.objects.filter(goal=goal, invited_user=request.user, status='accepted').exists()
        )
        if not allowed:
            messages.error(request, 'You are not authorized to contribute to that goal.')
            return _redirect_back(request, 'goals')

        if amount is None:
            messages.error(request, 'Enter a valid contribution amount.')
        elif profile.wallet_balance >= amount:
            with db_transaction.atomic():
                profile.wallet_balance -= amount
                goal.saved_amount += amount
                profile.save(update_fields=['wallet_balance'])
                goal.save(update_fields=['saved_amount'])
                contribution, _ = GoalContribution.objects.get_or_create(
                    goal=goal,
                    contributor=request.user,
                    defaults={'amount_contributed': Decimal('0.00')},
                )
                contribution.amount_contributed += amount
                contribution.save(update_fields=['amount_contributed'])
            if goal.user == request.user:
                messages.success(request, f"Locked Rs.{amount} in '{goal.title}'.")
            else:
                messages.success(request, f"Your Rs.{amount} contribution has been added to '{goal.title}'.")
        else:
            messages.error(request, 'Insufficient balance.')
    return _redirect_back(request, 'goals')


@login_required
def invite_goal_contributor(request, goal_id):
    if request.method != 'POST':
        return redirect('goals')

    goal = get_object_or_404(Goal, id=goal_id, user=request.user)
    username = request.POST.get('username', '').strip()
    if not username:
        messages.error(request, 'Enter a valid TruPay username to invite.')
        return _redirect_back(request, 'goals')

    try:
        invited_user = User.objects.get(username=username)
    except User.DoesNotExist:
        messages.error(request, 'Only existing TruPay users can be invited to collaborative goals.')
        return _redirect_back(request, 'goals')

    if invited_user == request.user:
        messages.error(request, 'You already own this goal.')
        return _redirect_back(request, 'goals')

    if GoalInvite.objects.filter(goal=goal, invited_user=invited_user, status='pending').exists():
        messages.error(request, f'{username} already has a pending invite for this goal.')
        return _redirect_back(request, 'goals')

    if GoalContribution.objects.filter(goal=goal, contributor=invited_user).exists():
        messages.error(request, f'{username} is already a contributor to this goal.')
        return _redirect_back(request, 'goals')

    GoalInvite.objects.create(goal=goal, invited_user=invited_user, invited_by=request.user)
    messages.success(request, f'{username} has been invited to collaborate on {goal.title}.')
    return _redirect_back(request, 'goals')


@login_required
def accept_goal_invite(request, invite_id):
    if request.method != 'POST':
        return redirect('goals')

    invite = get_object_or_404(GoalInvite, id=invite_id, invited_user=request.user, status='pending')
    invite.status = 'accepted'
    invite.save(update_fields=['status'])
    GoalContribution.objects.get_or_create(goal=invite.goal, contributor=request.user)
    messages.success(request, f'You joined the shared goal "{invite.goal.title}".')
    return _redirect_back(request, 'goals')


@login_required
def decline_goal_invite(request, invite_id):
    if request.method != 'POST':
        return redirect('goals')

    invite = get_object_or_404(GoalInvite, id=invite_id, invited_user=request.user, status='pending')
    invite.status = 'declined'
    invite.save(update_fields=['status'])
    messages.info(request, f'You declined the invite to collaborate on "{invite.goal.title}".')
    return _redirect_back(request, 'goals')


@login_required
def break_piggy_bank(request, goal_id):
    if request.method == 'POST':
        goal = get_object_or_404(Goal, id=goal_id, user=request.user)
        profile = request.user.profile
        saved_money = goal.saved_amount

        with db_transaction.atomic():
            profile.wallet_balance += saved_money
            profile.save(update_fields=['wallet_balance'])
            goal.delete()

        messages.success(request, f"Piggy Bank broken. Rs.{saved_money} returned to your wallet.")
    return _redirect_back(request, 'dashboard')

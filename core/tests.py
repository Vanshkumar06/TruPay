import time
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from .auth_utils import _totp_at, generate_totp_secret
from .models import ExternalRecipient, Goal, GoalContribution, GoalInvite, Transaction
from .risk_engine import RiskAnalysis, RiskEngine


class TruPayFlowTests(TestCase):
    def setUp(self):
        self.client = Client()
        cache.clear()
        self.sender = User.objects.create_user(username='alice', password='StrongPass123')
        self.receiver = User.objects.create_user(username='bob', password='StrongPass123')
        self.sender.profile.wallet_balance = Decimal('10000.00')
        self.sender.profile.monthly_budget = Decimal('5000.00')
        self.sender.profile.save()
        self.goal = Goal.objects.create(
            user=self.sender,
            title='Phone Upgrade',
            target_amount=Decimal('30000.00'),
        )
        self.client.login(username='alice', password='StrongPass123')

    def test_analyze_payment_returns_risk_payload(self):
        response = self.client.post(reverse('analyze_payment'), {
            'receiver_username': 'bob',
            'amount': '250.00',
            'merchant_category': 'grocery',
            'payment_method': 'UPI',
            'location': 'home',
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertIn('risk_level', data)
        self.assertIn('explanations', data)
        self.assertIn('trust_score', data)
        self.assertIn('model_label', data)

    def test_send_money_creates_audited_transaction(self):
        response = self.client.post(reverse('send_money'), {
            'receiver_username': 'bob',
            'amount': '150.00',
            'merchant_category': 'grocery',
            'payment_method': 'UPI',
            'location': 'home',
            'upi_pin': self.sender.profile.upi_pin,
        }, HTTP_REFERER=reverse('send_money'))
        self.assertRedirects(response, reverse('send_money'))
        tx = Transaction.objects.get(sender=self.sender, receiver=self.receiver)
        self.assertEqual(tx.amount, Decimal('150.00'))
        self.assertIn(tx.risk_level, ['Low', 'Medium', 'High'])
        self.assertTrue(tx.explanations)

    def test_send_money_roundup_saves_into_smart_savings(self):
        response = self.client.post(reverse('send_money'), {
            'receiver_username': 'bob',
            'amount': '57.00',
            'merchant_category': 'grocery',
            'payment_method': 'UPI',
            'location': 'home',
            'upi_pin': self.sender.profile.upi_pin,
            'enable_roundup': 'on',
            'roundup_destination': 'savings',
        }, HTTP_REFERER=reverse('send_money'))
        self.assertRedirects(response, reverse('send_money'))
        self.sender.profile.refresh_from_db()
        self.assertEqual(self.sender.profile.savings_balance, Decimal('3.00'))
        self.assertEqual(self.sender.profile.wallet_balance, Decimal('9940.00'))

    def test_send_money_roundup_can_fund_goal(self):
        response = self.client.post(reverse('send_money'), {
            'receiver_username': 'bob',
            'amount': '57.00',
            'merchant_category': 'grocery',
            'payment_method': 'UPI',
            'location': 'home',
            'upi_pin': self.sender.profile.upi_pin,
            'enable_roundup': 'on',
            'roundup_destination': 'goal',
            'roundup_goal': self.goal.id,
        }, HTTP_REFERER=reverse('send_money'))
        self.assertRedirects(response, reverse('send_money'))
        self.goal.refresh_from_db()
        self.sender.profile.refresh_from_db()
        self.assertEqual(self.goal.saved_amount, Decimal('3.00'))
        self.assertEqual(self.sender.profile.wallet_balance, Decimal('9940.00'))

    def test_send_money_creates_external_recipient_for_unknown_handle(self):
        response = self.client.post(reverse('send_money'), {
            'receiver_username': 'newpayee@trupay',
            'amount': '125.00',
            'merchant_category': 'grocery',
            'payment_method': 'UPI',
            'location': 'home',
            'upi_pin': self.sender.profile.upi_pin,
        }, HTTP_REFERER=reverse('send_money'))
        self.assertRedirects(response, reverse('send_money'))
        self.assertFalse(User.objects.filter(username='newpayee').exists())
        self.assertTrue(ExternalRecipient.objects.filter(handle='newpayee@external').exists())
        self.assertTrue(Transaction.objects.filter(sender=self.sender, external_receiver__handle='newpayee@external').exists())

    def test_send_money_requires_upi_pin(self):
        response = self.client.post(reverse('send_money'), {
            'receiver_username': 'bob',
            'amount': '50.00',
            'merchant_category': 'grocery',
            'payment_method': 'UPI',
            'location': 'home',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Enter your 4-digit UPI PIN')

    def test_send_money_rejects_incorrect_upi_pin(self):
        response = self.client.post(reverse('send_money'), {
            'receiver_username': 'bob',
            'amount': '50.00',
            'merchant_category': 'grocery',
            'payment_method': 'UPI',
            'location': 'home',
            'upi_pin': '0000',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Incorrect UPI PIN')

    def test_check_balance_locks_after_repeated_incorrect_pins(self):
        for _ in range(5):
            response = self.client.post(reverse('check_balance'), {'password': '0000'})
        self.assertEqual(response.status_code, 200)
        locked_response = self.client.post(reverse('check_balance'), {'password': '0000'})
        self.assertEqual(locked_response.status_code, 429)
        self.assertIn('Too many incorrect PIN attempts', locked_response.json()['error'])

    def test_allocate_surplus_to_savings(self):
        response = self.client.post(reverse('allocate_surplus'), {
            'amount': '200.00',
            'action': 'savings',
        }, HTTP_REFERER=reverse('goals'))
        self.assertRedirects(response, reverse('goals'))
        self.sender.profile.refresh_from_db()
        self.assertEqual(self.sender.profile.savings_balance, Decimal('200.00'))

    def test_allocate_surplus_can_carry_forward(self):
        response = self.client.post(reverse('allocate_surplus'), {
            'amount': '120.00',
            'action': 'carry',
        }, HTTP_REFERER=reverse('goals'))
        self.assertRedirects(response, reverse('goals'))
        self.sender.profile.refresh_from_db()
        self.assertEqual(self.sender.profile.wallet_balance, Decimal('10000.00'))

    def test_allocate_surplus_to_savings_requires_wallet_balance(self):
        self.sender.profile.wallet_balance = Decimal('100.00')
        self.sender.profile.monthly_budget = Decimal('1000.00')
        self.sender.profile.save(update_fields=['wallet_balance', 'monthly_budget'])

        response = self.client.post(reverse('allocate_surplus'), {
            'amount': '250.00',
            'action': 'savings',
        }, HTTP_REFERER=reverse('goals'))

        self.assertRedirects(response, reverse('goals'))
        self.sender.profile.refresh_from_db()
        self.assertEqual(self.sender.profile.wallet_balance, Decimal('100.00'))
        self.assertEqual(self.sender.profile.savings_balance, Decimal('0.00'))

    def test_settings_page_updates_profile(self):
        response = self.client.post(reverse('settings'), {
            'monthly_budget': '7000.00',
            'home_city': 'Mumbai',
            'budget_cycle': 'monthly',
            'upi_pin': '4321',
            'action': 'profile',
        })
        self.assertEqual(response.status_code, 302)
        self.sender.profile.refresh_from_db()
        self.assertEqual(self.sender.profile.home_city, 'Mumbai')
        self.assertEqual(self.sender.profile.upi_pin, '4321')

    def test_settings_page_accepts_budget_cycle_choice(self):
        response = self.client.post(reverse('settings'), {
            'monthly_budget': '7000.00',
            'home_city': 'Mumbai',
            'budget_cycle': 'quarterly',
            'upi_pin': '4321',
            'action': 'profile',
        })
        self.assertEqual(response.status_code, 302)
        self.sender.profile.refresh_from_db()
        self.assertEqual(self.sender.profile.budget_cycle, 'quarterly')

    def test_settings_page_rejects_budget_greater_than_wallet(self):
        response = self.client.post(reverse('settings'), {
            'monthly_budget': '12000.00',
            'home_city': 'Mumbai',
            'budget_cycle': 'monthly',
            'upi_pin': '4321',
            'action': 'profile',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Budget cannot be greater than your wallet balance')
        self.sender.profile.refresh_from_db()
        self.assertEqual(self.sender.profile.monthly_budget, Decimal('5000.00'))

    def test_signup_creates_user_with_upi_pin(self):
        self.client.logout()
        response = self.client.post(reverse('signup'), {
            'username': 'charlie',
            'password1': 'StrongPass123',
            'password2': 'StrongPass123',
            'upi_pin': '4321',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username='charlie').exists())
        user = User.objects.get(username='charlie')
        self.assertEqual(user.profile.upi_pin, '4321')
        self.assertEqual(user.profile.monthly_budget, Decimal('0.00'))

    def test_signup_claims_placeholder_receiver_account(self):
        placeholder = User.objects.create(username='claimeduser', email='claimeduser@sandbox.trupay.local')
        placeholder.set_unusable_password()
        placeholder.save(update_fields=['password'])

        self.client.logout()
        response = self.client.post(reverse('signup'), {
            'username': 'claimeduser',
            'email': 'claimed@example.com',
            'password1': 'StrongPass123',
            'password2': 'StrongPass123',
            'upi_pin': '9876',
        })

        self.assertEqual(response.status_code, 302)
        placeholder.refresh_from_db()
        self.assertTrue(placeholder.has_usable_password())
        self.assertEqual(placeholder.email, 'claimed@example.com')
        self.assertEqual(placeholder.profile.upi_pin, '9876')

    def test_analyze_payment_supports_external_receiver(self):
        response = self.client.post(reverse('analyze_payment'), {
            'receiver_username': 'missinguser',
            'amount': '250.00',
            'merchant_category': 'grocery',
            'payment_method': 'UPI',
            'location': 'home',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])

    def test_analytics_page_shows_trust_help_and_monthly_activity(self):
        response = self.client.get(reverse('analytics'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'What does trust score mean?')
        self.assertContains(response, 'Past six months at a glance')
        self.assertContains(response, '85-100')
        self.assertContains(response, '1-44')

    def test_dashboard_no_longer_shows_save_after_transfer_modal(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Save After Transfer')

    def test_risk_engine_escalates_multiple_strong_signals_to_high(self):
        receiver = ExternalRecipient(handle='testpayee@external', display_name='testpayee')
        engine = RiskEngine()
        self.sender.profile.spent_this_cycle = Decimal('5000.00')
        self.sender.profile.save(update_fields=['spent_this_cycle'])
        analysis = engine.analyze(self.sender, receiver, {
            'amount': Decimal('3900.00'),
            'merchant_category': 'travel',
            'payment_method': 'UPI',
            'location': 'foreign',
            'is_new_device': False,
        })
        self.assertEqual(analysis.risk_level, 'High')

    def test_goal_owner_can_invite_existing_user(self):
        charlie = User.objects.create_user(username='charlie', password='StrongPass123')
        response = self.client.post(reverse('invite_goal_contributor', args=[self.goal.id]), {
            'username': 'charlie',
        }, HTTP_REFERER=reverse('goals'))
        self.assertRedirects(response, reverse('goals'))
        self.assertTrue(GoalInvite.objects.filter(goal=self.goal, invited_user=charlie, status='pending').exists())

    def test_contributor_can_accept_goal_invite_and_fund(self):
        charlie = User.objects.create_user(username='charlie', password='StrongPass123')
        GoalInvite.objects.create(goal=self.goal, invited_user=charlie, invited_by=self.sender)
        self.client.logout()
        self.client.login(username='charlie', password='StrongPass123')

        invite = GoalInvite.objects.get(goal=self.goal, invited_user=charlie)
        response = self.client.post(reverse('accept_goal_invite', args=[invite.id]), {}, HTTP_REFERER=reverse('goals'))
        self.assertRedirects(response, reverse('goals'))

        invite.refresh_from_db()
        self.assertEqual(invite.status, 'accepted')

        response = self.client.post(reverse('fund_goal', args=[self.goal.id]), {
            'amount': '150.00',
        }, HTTP_REFERER=reverse('goals'))
        self.assertRedirects(response, reverse('goals'))
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.saved_amount, Decimal('150.00'))
        contribution = GoalContribution.objects.get(goal=self.goal, contributor=charlie)
        self.assertEqual(contribution.amount_contributed, Decimal('150.00'))

    def test_create_goal_redirects_back_to_origin_page(self):
        response = self.client.post(reverse('create_goal'), {
            'title': 'Emergency Fund',
            'target_amount': '5000.00',
            'priority': 'high',
        }, HTTP_REFERER=reverse('goals'))
        self.assertRedirects(response, reverse('goals'))

    def test_confirm_roundup_redirects_back_to_origin_page(self):
        response = self.client.post(reverse('confirm_roundup'), {
            'amount': '50.00',
        }, HTTP_REFERER=reverse('send_money'))
        self.assertRedirects(response, reverse('send_money'))

    def test_confirm_roundup_rejects_invalid_amount(self):
        response = self.client.post(reverse('confirm_roundup'), {
            'amount': 'abc',
        }, HTTP_REFERER=reverse('dashboard'))
        self.assertRedirects(response, reverse('dashboard'))
        self.sender.profile.refresh_from_db()
        self.assertEqual(self.sender.profile.savings_balance, Decimal('0.00'))

    def test_fund_goal_rejects_invalid_amount(self):
        response = self.client.post(reverse('fund_goal', args=[self.goal.id]), {
            'amount': 'abc',
        }, HTTP_REFERER=reverse('goals'))
        self.assertRedirects(response, reverse('goals'))
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.saved_amount, Decimal('0.00'))

    def test_send_money_redirects_to_profile_when_budget_is_zero(self):
        self.sender.profile.monthly_budget = Decimal('0.00')
        self.sender.profile.save(update_fields=['monthly_budget'])

        response = self.client.get(reverse('send_money'))
        self.assertRedirects(response, reverse('profile'))

    def test_analyze_payment_rejects_transfer_when_budget_is_zero(self):
        self.sender.profile.monthly_budget = Decimal('0.00')
        self.sender.profile.save(update_fields=['monthly_budget'])

        response = self.client.post(reverse('analyze_payment'), {
            'receiver_username': 'bob',
            'amount': '250.00',
            'merchant_category': 'grocery',
            'payment_method': 'UPI',
            'location': 'home',
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('Set your budget', response.json()['error'])

    def test_analyze_payment_does_not_create_receiver_user(self):
        response = self.client.post(reverse('analyze_payment'), {
            'receiver_username': 'previewonly@trupay',
            'amount': '250.00',
            'merchant_category': 'grocery',
            'payment_method': 'UPI',
            'location': 'home',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        self.assertFalse(User.objects.filter(username='previewonly').exists())
        self.assertFalse(ExternalRecipient.objects.filter(handle='previewonly@external').exists())

    def test_confirm_roundup_saves_to_smart_savings(self):
        response = self.client.post(reverse('confirm_roundup'), {
            'amount': '50.00',
        })
        self.assertRedirects(response, reverse('dashboard'))
        self.sender.profile.refresh_from_db()
        self.assertEqual(self.sender.profile.savings_balance, Decimal('50.00'))
        self.assertEqual(self.sender.profile.wallet_balance, Decimal('9950.00'))

    @patch('core.views.risk_engine.analyze')
    def test_medium_risk_payment_does_not_require_confirmation(self, analyze_mock):
        analyze_mock.return_value = RiskAnalysis(
            risk_score=0.51,
            risk_level='Medium',
            decision='warning_confirmation',
            explanations=['You have not sent money to this user before.'],
            advisory='This payment needs your attention.',
            feature_snapshot={},
            model_source='behavioral_fallback',
        )

        response = self.client.post(reverse('send_money'), {
            'receiver_username': 'bob',
            'amount': '50.00',
            'merchant_category': 'grocery',
            'payment_method': 'UPI',
            'location': 'home',
            'upi_pin': self.sender.profile.upi_pin,
        }, HTTP_REFERER=reverse('send_money'))
        self.assertRedirects(response, reverse('send_money'))
        tx = Transaction.objects.get(sender=self.sender, receiver=self.receiver)
        self.assertEqual(tx.decision, 'warning_confirmation')

    @patch('core.views.risk_engine.analyze')
    def test_high_risk_payment_requires_confirmation(self, analyze_mock):
        analyze_mock.return_value = RiskAnalysis(
            risk_score=0.91,
            risk_level='High',
            decision='high_risk_confirmation',
            explanations=['The payment is being made from an unusual location.'],
            advisory='This payment is high risk.',
            feature_snapshot={},
            model_source='behavioral_fallback',
        )

        response = self.client.post(reverse('send_money'), {
            'receiver_username': 'bob',
            'amount': '50.00',
            'merchant_category': 'grocery',
            'payment_method': 'UPI',
            'location': 'foreign',
            'upi_pin': self.sender.profile.upi_pin,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Low trust: confirm before paying')
        self.assertEqual(Transaction.objects.filter(sender=self.sender, receiver=self.receiver).count(), 0)

    def test_logout_requires_post(self):
        response = self.client.get(reverse('logout'))
        self.assertEqual(response.status_code, 405)

    def test_login_requires_2fa_when_enabled(self):
        secret = generate_totp_secret()
        self.sender.profile.totp_secret = secret
        self.sender.profile.totp_enabled = True
        self.sender.profile.backup_codes = ['ABCD1234']
        self.sender.profile.save(update_fields=['totp_secret', 'totp_enabled', 'backup_codes'])
        self.client.logout()

        response = self.client.post(reverse('login'), {
            'username': 'alice',
            'password': 'StrongPass123',
        })
        self.assertRedirects(response, reverse('verify_2fa'))

        verify_response = self.client.post(reverse('verify_2fa'), {
            'code': _totp_at(secret, time.time()),
        })
        self.assertRedirects(verify_response, reverse('dashboard'))

    def test_landing_page_is_public(self):
        self.client.logout()
        response = self.client.get(reverse('landing'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'AI-assisted payments')

    def test_demo_overview_api_is_public(self):
        self.client.logout()
        response = self.client.get(reverse('demo_overview_api'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'TruPay')

    def test_seed_demo_command_creates_demo_users(self):
        call_command('seed_demo')
        self.assertTrue(User.objects.filter(username='alice').exists())
        self.assertTrue(User.objects.filter(username='charlie').exists())

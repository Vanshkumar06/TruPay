from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction
from django.utils import timezone

from core.models import ExternalRecipient, Goal, GoalContribution, Transaction


class Command(BaseCommand):
    help = 'Create judge-ready TruPay demo accounts with rich transactions, insights, goals, and mixed trust patterns.'

    def handle(self, *args, **options):
        with db_transaction.atomic():
            showcase = self._create_user('showcase', 'Hackathon@123', 'Pune', '2468', Decimal('84250.00'))
            alice = self._create_user('alice', 'Hackathon@123', 'Mumbai', '4321', Decimal('27500.00'))
            bob = self._create_user('bob', 'Hackathon@123', 'Bengaluru', '1234', Decimal('18000.00'))
            charlie = self._create_user('charlie', 'Hackathon@123', 'Delhi', '5678', Decimal('22000.00'))

            self._seed_goals(showcase, alice)
            self._seed_transactions(showcase, alice, bob, charlie)

        self.stdout.write(self.style.SUCCESS('TruPay showcase data is ready.'))
        self.stdout.write('Showcase account: showcase')
        self.stdout.write('Password: Hackathon@123')
        self.stdout.write('UPI PIN: 2468')
        self.stdout.write('Other demo users: alice, bob, charlie')
        self.stdout.write('Password for each sample user: Hackathon@123')

    def _create_user(self, username, password, city, pin, wallet_balance):
        user, created = User.objects.get_or_create(username=username, defaults={'email': f'{username}@trupay.demo'})
        if created:
            user.set_password(password)
            user.save()
        else:
            user.email = f'{username}@trupay.demo'
            user.set_password(password)
            user.save(update_fields=['email', 'password'])

        profile = user.profile
        profile.home_city = city
        profile.upi_pin = pin
        profile.wallet_balance = wallet_balance
        profile.monthly_budget = Decimal('18000.00' if username == 'showcase' else '15000.00')
        profile.spent_this_cycle = Decimal('9650.00' if username == 'showcase' else '4200.00')
        profile.savings_balance = Decimal('14650.00' if username == 'showcase' else '3500.00')
        profile.borrowed_from_next_cycle = Decimal('0.00')
        profile.save()
        return user

    def _seed_goals(self, showcase, alice):
        goals = [
            ('Emergency Fund', Decimal('150000.00'), Decimal('48500.00'), 'high'),
            ('Goa Friends Trip', Decimal('60000.00'), Decimal('18200.00'), 'medium'),
            ('New Camera Kit', Decimal('85000.00'), Decimal('25950.00'), 'medium'),
        ]
        for title, target, saved, priority in goals:
            goal, _ = Goal.objects.update_or_create(
                user=showcase,
                title=title,
                defaults={
                    'target_amount': target,
                    'saved_amount': saved,
                    'priority': priority,
                },
            )
            contribution, _ = GoalContribution.objects.get_or_create(
                goal=goal,
                contributor=showcase,
                defaults={'amount_contributed': saved},
            )
            contribution.amount_contributed = saved
            contribution.save(update_fields=['amount_contributed'])

        Goal.objects.update_or_create(
            user=alice,
            title='MacBook Fund',
            defaults={
                'target_amount': Decimal('120000.00'),
                'saved_amount': Decimal('28000.00'),
                'priority': 'high',
            },
        )

    def _seed_transactions(self, showcase, alice, bob, charlie):
        Transaction.objects.filter(sender=showcase).delete()
        ExternalRecipient.objects.filter(handle__in=[
            'rent-owner@external',
            'weekend-market@external',
            'flightdesk@external',
            'camera-store@external',
            'medicorner@external',
            'fuelbay@external',
            'musicfest@external',
        ]).delete()

        rent_owner = self._external('rent-owner@external', 'Rent Owner')
        weekend_market = self._external('weekend-market@external', 'Weekend Market')
        flightdesk = self._external('flightdesk@external', 'Flight Desk')
        camera_store = self._external('camera-store@external', 'Camera Store')
        medicorner = self._external('medicorner@external', 'Medi Corner')
        fuelbay = self._external('fuelbay@external', 'Fuel Bay')
        musicfest = self._external('musicfest@external', 'Music Fest')

        now = timezone.localtime()
        seed_rows = [
            (showcase, alice, None, '1250.00', 'restaurant', 'UPI', 'Low', 'approved_silent', ['A familiar payment pattern kept trust high.', 'You have handled this kind of payment safely before.'], 'Payment fits well within the current budget cycle.', now - timedelta(days=2)),
            (showcase, bob, None, '880.00', 'grocery', 'UPI', 'Low', 'approved_silent', ['This transaction matches your usual payment pattern and looks familiar.'], 'After this payment, you still have comfortable room left in the cycle.', now - timedelta(days=5)),
            (showcase, charlie, None, '2140.00', 'electronics', 'Card', 'Medium', 'warning_confirmation', ['This amount is much higher than your usual spending.', 'This payee is new for your account, so TruPay is being a little more careful.'], 'This payment uses a noticeable share of the active budget.', now - timedelta(days=8)),
            (showcase, None, rent_owner, '7800.00', 'travel', 'Bank Transfer', 'High', 'approved_with_confirmation', ['This payee is new for your account, so TruPay is being a little more careful.', 'The payment is happening away from your normal area.', 'This payment pushes you beyond the current budget cycle.'], 'This payment would borrow from the next cycle, so TruPay asks for a stronger confirmation step.', now - timedelta(days=12)),
            (showcase, None, weekend_market, '670.00', 'grocery', 'Wallet', 'Low', 'approved_silent', ['This transaction matches your usual payment pattern and looks familiar.'], 'A light wallet payment that stays well inside budget.', now - timedelta(days=17)),
            (showcase, None, flightdesk, '4380.00', 'travel', 'UPI', 'High', 'approved_with_confirmation', ['This payee is new for your account, so TruPay is being a little more careful.', 'The payment is being made from an unusual location.', 'This amount is much higher than your usual spending.'], 'This travel payment deserves extra confirmation before continuing.', now - timedelta(days=28)),
            (showcase, None, camera_store, '3150.00', 'electronics', 'Card', 'Medium', 'warning_confirmation', ['This amount is much higher than your usual spending.', 'The payment is happening away from your normal area.'], 'This is still manageable, but it is a heavier spend than usual.', now - timedelta(days=34)),
            (showcase, alice, None, '920.00', 'restaurant', 'UPI', 'Low', 'approved_silent', ['You have handled this kind of payment safely before.', 'This transaction matches your usual payment pattern and looks familiar.'], 'No trust concerns here and the payment remains budget friendly.', now - timedelta(days=41)),
            (showcase, bob, None, '540.00', 'gas_station', 'UPI', 'Low', 'approved_silent', ['This transaction matches your usual payment pattern and looks familiar.'], 'Routine payment with good trust and low budget impact.', now - timedelta(days=49)),
            (showcase, None, medicorner, '1160.00', 'pharmacy', 'UPI', 'Low', 'approved_silent', ['You have handled this kind of payment safely before.'], 'Necessary payment that stays inside your normal spending rhythm.', now - timedelta(days=58)),
            (showcase, None, fuelbay, '750.00', 'gas_station', 'UPI', 'Low', 'approved_silent', ['This transaction matches your usual payment pattern and looks familiar.'], 'Fuel payment remains within a healthy range for the month.', now - timedelta(days=67)),
            (showcase, charlie, None, '2800.00', 'entertainment', 'Wallet', 'Medium', 'warning_confirmation', ['This payee is new for your account, so TruPay is being a little more careful.', 'This amount is much higher than your usual spending.'], 'This entertainment spend is allowed, but worth a second look.', now - timedelta(days=76)),
            (showcase, None, musicfest, '4950.00', 'entertainment', 'UPI', 'High', 'approved_with_confirmation', ['This amount is much higher than your usual spending.', 'The payment is being made from an unusual location.', 'This payment pushes you beyond the current budget cycle.'], 'This event payment is meaningful enough to trigger a stronger trust review.', now - timedelta(days=88)),
            (showcase, bob, None, '690.00', 'restaurant', 'UPI', 'Low', 'approved_silent', ['This transaction matches your usual payment pattern and looks familiar.'], 'A familiar restaurant spend with strong trust.', now - timedelta(days=101)),
            (showcase, alice, None, '1450.00', 'online_retail', 'Wallet', 'Medium', 'warning_confirmation', ['This payee is new for your account, so TruPay is being a little more careful.'], 'Online retail is fine here, but the new payee lowers trust a little.', now - timedelta(days=116)),
            (showcase, None, rent_owner, '7600.00', 'travel', 'Bank Transfer', 'Medium', 'warning_confirmation', ['This payment pushes you beyond the current budget cycle.', 'The payment is happening away from your normal area.'], 'This large transfer is still manageable, but it leans on next-cycle budget room.', now - timedelta(days=132)),
            (showcase, bob, None, '980.00', 'grocery', 'UPI', 'Low', 'approved_silent', ['This transaction matches your usual payment pattern and looks familiar.'], 'Healthy trust and comfortable budget impact.', now - timedelta(days=149)),
            (showcase, None, weekend_market, '430.00', 'grocery', 'Wallet', 'Low', 'approved_silent', ['You have handled this kind of payment safely before.'], 'Small recurring spend with strong trust support.', now - timedelta(days=163)),
        ]

        for sender, receiver, external_receiver, amount, category, method, level, decision, explanations, advisory, timestamp in seed_rows:
            tx = Transaction.objects.create(
                sender=sender,
                receiver=receiver,
                external_receiver=external_receiver,
                amount=Decimal(amount),
                status='SUCCESS',
                merchant_category=category,
                payment_method=method,
                risk_score=self._score_for(level),
                risk_level=level,
                decision=decision,
                explanations=explanations,
                advisory=advisory,
                risk_snapshot={
                    'seeded': True,
                    'merchant_category': category,
                    'payment_method': method,
                    'demo_timestamp': timestamp.isoformat(),
                },
            )
            Transaction.objects.filter(id=tx.id).update(timestamp=timestamp)

    def _external(self, handle, display_name):
        recipient, _ = ExternalRecipient.objects.get_or_create(
            handle=handle,
            defaults={'display_name': display_name},
        )
        recipient.display_name = display_name
        recipient.save(update_fields=['display_name'])
        return recipient

    def _score_for(self, level):
        return {
            'Low': Decimal('0.1400'),
            'Medium': Decimal('0.4700'),
            'High': Decimal('0.7800'),
        }[level]

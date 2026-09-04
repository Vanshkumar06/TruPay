import math
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Sum
from django.utils import timezone

CONTRIBUTION_COLORS = ['#0f9d8a', '#5b83f6', '#fbbf24', '#fb7185', '#8b5cf6', '#14b8a6', '#f97316']


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    wallet_balance = models.DecimalField(max_digits=12, decimal_places=2, default=10000.00)
    upi_pin = models.CharField(max_length=4, default='1234')
    monthly_budget = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    spent_this_cycle = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    borrowed_from_next_cycle = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    budget_cycle_started_at = models.DateTimeField(default=timezone.now)
    home_city = models.CharField(max_length=100, default='Pune')
    savings_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    BUDGET_CYCLE_CHOICES = [
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('6_months', '6 Months'),
    ]
    budget_cycle = models.CharField(max_length=10, choices=BUDGET_CYCLE_CHOICES, default='monthly')
    totp_secret = models.CharField(max_length=64, blank=True)
    totp_enabled = models.BooleanField(default=False)
    backup_codes = models.JSONField(default=list, blank=True)

    @property
    def upi_id(self):
        return f"{self.user.username}@trupay"

    def refresh_budget_cycle(self, now=None):
        now = now or timezone.now()
        if not self.budget_cycle_started_at:
            self.budget_cycle_started_at = now
            self.save(update_fields=['budget_cycle_started_at'])
            return

        if (now - self.budget_cycle_started_at).days >= self.budget_cycle_length_days:
            carried_amount = self.borrowed_from_next_cycle
            self.spent_this_cycle = carried_amount
            self.borrowed_from_next_cycle = Decimal('0.00')
            self.budget_cycle_started_at = now
            self.save(update_fields=[
                'spent_this_cycle',
                'borrowed_from_next_cycle',
                'budget_cycle_started_at',
            ])

    @property
    def budget_remaining(self):
        remaining = self.monthly_budget - self.spent_this_cycle
        return remaining if remaining > 0 else Decimal('0.00')

    @property
    def budget_cycle_length_days(self):
        return {
            'weekly': 7,
            'monthly': 30,
            'quarterly': 91,
            '6_months': 182,
            '15_days': 15,
            'yearly': 365,
        }.get(self.budget_cycle, 30)

    @property
    def budget_cycle_end_date(self):
        return self.budget_cycle_started_at + timedelta(days=self.budget_cycle_length_days)

    @property
    def budget_cycle_days_left(self):
        days_left = (self.budget_cycle_end_date - timezone.now()).days
        return max(days_left, 0)

    @property
    def budget_progress(self):
        if not self.monthly_budget:
            return 0
        percentage = int((self.spent_this_cycle / self.monthly_budget) * 100)
        return max(0, min(percentage, 100))

    def apply_spend(self, amount):
        amount = Decimal(amount)
        self.spent_this_cycle += amount
        overshoot = self.spent_this_cycle - self.monthly_budget
        self.borrowed_from_next_cycle = overshoot if overshoot > 0 else Decimal('0.00')

    def __str__(self):
        return f"{self.user.username}'s Profile"


class ExternalRecipient(models.Model):
    handle = models.CharField(max_length=150, unique=True)
    display_name = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def normalized_handle(self):
        return self.handle.replace('@trupay', '')

    @property
    def username(self):
        return self.normalized_handle

    def __str__(self):
        return self.handle


class Transaction(models.Model):
    STATUS_CHOICES = [
        ('SUCCESS', 'Success'),
        ('BLOCKED', 'Blocked'),
    ]
    RISK_LEVEL_CHOICES = [
        ('Low', 'Low'),
        ('Medium', 'Medium'),
        ('High', 'High'),
    ]

    sender = models.ForeignKey(User, related_name='sent_transactions', on_delete=models.CASCADE)
    receiver = models.ForeignKey(User, related_name='received_transactions', on_delete=models.CASCADE, null=True, blank=True)
    external_receiver = models.ForeignKey(
        ExternalRecipient,
        related_name='transactions',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    timestamp = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='SUCCESS')
    merchant_category = models.CharField(max_length=50, default='general')
    payment_method = models.CharField(max_length=30, default='UPI')
    risk_score = models.DecimalField(max_digits=6, decimal_places=4, default=0.0000)
    risk_level = models.CharField(max_length=10, choices=RISK_LEVEL_CHOICES, default='Low')
    decision = models.CharField(max_length=50, default='approved_silent')
    explanations = models.JSONField(default=list, blank=True)
    advisory = models.TextField(blank=True)
    risk_snapshot = models.JSONField(default=dict, blank=True)

    @property
    def counterparty_name(self):
        if self.receiver_id:
            return self.receiver.username
        if self.external_receiver_id:
            return self.external_receiver.handle
        return 'Unknown recipient'

    @property
    def counterparty_display(self):
        if self.sender_id and self.receiver_id:
            return self.receiver.username
        if self.external_receiver_id:
            return self.external_receiver.handle
        return 'Unknown recipient'

    @property
    def trust_score(self):
        return max(1, min(99, int(round((1 - float(self.risk_score)) * 100))))

    def __str__(self):
        return f"{self.sender.username} sent Rs.{self.amount} to {self.counterparty_name}"


class Goal(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='goals')
    title = models.CharField(max_length=100)
    target_amount = models.DecimalField(max_digits=10, decimal_places=2)
    saved_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(auto_now_add=True)
    target_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=20, default='medium')
    contributors = models.ManyToManyField(
        User,
        through='GoalContribution',
        related_name='shared_goals',
        blank=True,
    )

    @property
    def progress_percentage(self):
        if self.target_amount > 0:
            return min(int((self.saved_amount / self.target_amount) * 100), 100)
        return 0

    @property
    def remaining_amount(self):
        remaining = self.target_amount - self.saved_amount
        return remaining if remaining > 0 else Decimal('0.00')

    @property
    def required_monthly_saving(self):
        if not self.target_date or self.remaining_amount <= 0:
            return Decimal('0.00')
        months = max(
            ((self.target_date.year - timezone.now().date().year) * 12) +
            (self.target_date.month - timezone.now().date().month),
            1,
        )
        return (self.remaining_amount / Decimal(months)).quantize(Decimal('0.01'))

    @property
    def predicted_completion_date(self):
        if self.remaining_amount <= 0:
            return timezone.now().date()

        days_active = max((timezone.now().date() - self.created_at.date()).days, 1)
        smoothing_days = max(days_active, 7)
        avg_daily_saved = self.saved_amount / Decimal(smoothing_days)
        if avg_daily_saved <= 0:
            return None

        days_needed = math.ceil(self.remaining_amount / avg_daily_saved)
        return timezone.now().date() + timedelta(days=days_needed)

    def contributor_summary(self):
        return self.contributions.select_related('contributor').all()

    @property
    def contributions_total(self):
        result = self.contributions.aggregate(total=Sum('amount_contributed'))
        tracked_total = result['total'] or Decimal('0.00')
        return self.saved_amount if self.saved_amount > tracked_total else tracked_total

    @property
    def contribution_segments(self):
        contributions = list(self.contributions.select_related('contributor').all())
        segments = []
        total_tracked = sum((c.amount_contributed for c in contributions), Decimal('0.00'))
        for index, contribution in enumerate(contributions):
            percent = contribution.percent_of_goal
            segments.append({
                'label': contribution.contributor.username,
                'amount': contribution.amount_contributed,
                'percent': percent,
                'color': CONTRIBUTION_COLORS[index % len(CONTRIBUTION_COLORS)],
            })

        if self.user and self.user not in [c.contributor for c in contributions] and self.saved_amount > total_tracked:
            owner_amount = self.saved_amount - total_tracked
            owner_percent = float(min((owner_amount / self.target_amount) * Decimal('100'), Decimal('100'))) if self.target_amount > 0 else 0.0
            segments.insert(0, {
                'label': self.user.username,
                'amount': owner_amount,
                'percent': owner_percent,
                'color': CONTRIBUTION_COLORS[len(contributions) % len(CONTRIBUTION_COLORS)],
            })
        return segments

    @property
    def contribution_pie_style(self):
        segments = self.contribution_segments
        if not segments:
            return 'linear-gradient(180deg, rgba(255,255,255,0.08), rgba(255,255,255,0.04))'

        current = 0
        stops = []
        for segment in segments:
            start = current
            end = start + float(segment['percent'])
            stops.append(f"{segment['color']} {start:.2f}% {end:.2f}%")
            current = end
        if current < 100:
            stops.append(f"rgba(255,255,255,0.08) {current:.2f}% 100%")
        return f"conic-gradient({', '.join(stops)})"

    def is_participant(self, user):
        return (
            self.user == user or
            self.contributions.filter(contributor=user).exists() or
            self.invites.filter(invited_user=user, status='accepted').exists()
        )

    def __str__(self):
        return self.title


class GoalContribution(models.Model):
    goal = models.ForeignKey(Goal, on_delete=models.CASCADE, related_name='contributions')
    contributor = models.ForeignKey(User, on_delete=models.CASCADE, related_name='goal_contributions')
    amount_contributed = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        unique_together = ('goal', 'contributor')

    @property
    def percent_of_goal(self):
        if self.goal.target_amount > 0:
            percent = (self.amount_contributed / self.goal.target_amount) * Decimal('100')
            return float(min(percent, Decimal('100')))
        return 0.0

    def __str__(self):
        return f"{self.contributor.username} -> {self.goal.title}: Rs.{self.amount_contributed}"


class GoalInvite(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
    ]

    goal = models.ForeignKey(Goal, on_delete=models.CASCADE, related_name='invites')
    invited_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='goal_invitations')
    invited_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_goal_invites')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('goal', 'invited_user')

    def __str__(self):
        return f"Invite {self.invited_user.username} to '{self.goal.title}' ({self.status})"

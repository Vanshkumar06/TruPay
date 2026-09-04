from django.contrib import admin

from .models import Goal, GoalContribution, GoalInvite, Profile, Transaction


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'wallet_balance', 'monthly_budget', 'spent_this_cycle', 'borrowed_from_next_cycle', 'savings_balance')
    search_fields = ('user__username', 'home_city')


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'sender', 'receiver', 'amount', 'merchant_category', 'payment_method', 'risk_level', 'risk_score', 'status', 'timestamp')
    list_filter = ('risk_level', 'status', 'merchant_category', 'payment_method')
    search_fields = ('sender__username', 'receiver__username')
    ordering = ('-timestamp',)


@admin.register(Goal)
class GoalAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'target_amount', 'saved_amount', 'target_date', 'priority')
    list_filter = ('priority',)
    search_fields = ('title', 'user__username')


@admin.register(GoalContribution)
class GoalContributionAdmin(admin.ModelAdmin):
    list_display = ('goal', 'contributor', 'amount_contributed')
    search_fields = ('goal__title', 'contributor__username')


@admin.register(GoalInvite)
class GoalInviteAdmin(admin.ModelAdmin):
    list_display = ('goal', 'invited_user', 'invited_by', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('goal__title', 'invited_user__username', 'invited_by__username')

from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_profile_backup_codes_profile_totp_enabled_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='GoalContribution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount_contributed', models.DecimalField(default=Decimal('0.00'), max_digits=12, decimal_places=2)),
                ('contributor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='goal_contributions', to=settings.AUTH_USER_MODEL)),
                ('goal', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='contributions', to='core.goal')),
            ],
            options={
                'unique_together': {('goal', 'contributor')},
            },
        ),
        migrations.CreateModel(
            name='GoalInvite',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('declined', 'Declined')], default='pending', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('goal', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='invites', to='core.goal')),
                ('invited_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sent_goal_invites', to=settings.AUTH_USER_MODEL)),
                ('invited_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='goal_invitations', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('goal', 'invited_user')},
            },
        ),
        migrations.AddField(
            model_name='goal',
            name='contributors',
            field=models.ManyToManyField(blank=True, through='core.GoalContribution', related_name='shared_goals', to=settings.AUTH_USER_MODEL),
        ),
    ]

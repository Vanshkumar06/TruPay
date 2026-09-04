from django.db import migrations, models


def normalize_legacy_budget_cycles(apps, schema_editor):
    Profile = apps.get_model('core', 'Profile')
    Profile.objects.filter(budget_cycle='15_days').update(budget_cycle='monthly')
    Profile.objects.filter(budget_cycle='yearly').update(budget_cycle='monthly')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_goal_collaboration'),
    ]

    operations = [
        migrations.AlterField(
            model_name='profile',
            name='monthly_budget',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=12),
        ),
        migrations.AlterField(
            model_name='profile',
            name='budget_cycle',
            field=models.CharField(
                choices=[
                    ('weekly', 'Weekly'),
                    ('monthly', 'Monthly'),
                    ('quarterly', 'Quarterly'),
                    ('6_months', '6 Months'),
                ],
                default='monthly',
                max_length=10,
            ),
        ),
        migrations.RunPython(normalize_legacy_budget_cycles, migrations.RunPython.noop),
    ]

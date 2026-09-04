from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from .models import Goal, Profile
from .risk_engine import LOCATION_CHOICES, MERCHANT_CHOICES, PAYMENT_METHOD_CHOICES


class StyledFormMixin:
    def apply_styles(self):
        for field in self.fields.values():
            css = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = (css + ' form-control').strip()


class SignupForm(StyledFormMixin, UserCreationForm):
    email = forms.EmailField(required=False)
    upi_pin = forms.CharField(max_length=4, min_length=4, help_text='Set a 4-digit TruPay PIN.')

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email', 'password1', 'password2', 'upi_pin')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.placeholder_user = None
        self.apply_styles()

    def clean_username(self):
        username = self.cleaned_data['username']
        existing_user = User.objects.filter(username=username).first()
        if not existing_user:
            return username

        if (not existing_user.has_usable_password()) and existing_user.email.endswith('@sandbox.trupay.local'):
            self.placeholder_user = existing_user
            self.instance = existing_user
            return username

        raise forms.ValidationError('A user with that username already exists.')

    def clean_upi_pin(self):
        pin = self.cleaned_data['upi_pin']
        if not pin.isdigit():
            raise forms.ValidationError('PIN must be exactly 4 digits.')
        return pin

    def save(self, commit=True):
        if not self.placeholder_user:
            return super().save(commit=commit)

        user = self.placeholder_user
        user.email = self.cleaned_data.get('email', '')
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save(update_fields=['email', 'password'])
        return user


class LoginForm(StyledFormMixin, AuthenticationForm):
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()


class TwoFactorForm(StyledFormMixin, forms.Form):
    code = forms.CharField(max_length=12, label='Authenticator or backup code')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()


class TransferForm(StyledFormMixin, forms.Form):
    receiver_username = forms.CharField(max_length=150, label="Receiver UPI handle")
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=1.00, label='Amount (Rs.)')
    merchant_category = forms.ChoiceField(choices=MERCHANT_CHOICES)
    payment_method = forms.ChoiceField(choices=PAYMENT_METHOD_CHOICES)
    location = forms.ChoiceField(choices=LOCATION_CHOICES, initial='home')
    upi_pin = forms.CharField(
        max_length=4,
        min_length=4,
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label='UPI PIN',
        help_text='Enter your 4-digit TruPay PIN to authorize the payment.',
    )
    is_new_device = forms.BooleanField(required=False, label='Using a new device')
    enable_roundup = forms.BooleanField(required=False, label='Round up this payment')
    roundup_destination = forms.ChoiceField(
        choices=[
            ('savings', 'Smart savings'),
            ('goal', 'Piggy Bank'),
        ],
        required=False,
    )
    roundup_goal = forms.ModelChoiceField(queryset=Goal.objects.none(), required=False)
    risk_acknowledged = forms.BooleanField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()
        self.fields['is_new_device'].widget.attrs['class'] = 'trust-checkbox'
        self.fields['enable_roundup'].widget.attrs['class'] = 'trust-checkbox'
        self.fields['roundup_destination'].label = 'Save into'
        self.fields['roundup_goal'].label = 'Choose Piggy Bank'
        if user:
            self.fields['roundup_goal'].queryset = Goal.objects.filter(user=user).order_by('-created_at')

    def clean_upi_pin(self):
        pin = self.cleaned_data.get('upi_pin', '')
        if pin and not pin.isdigit():
            raise forms.ValidationError('PIN must contain only digits.')
        return pin

    def clean_receiver_username(self):
        username = self.cleaned_data['receiver_username'].strip().replace('@trupay', '')
        if not username:
            raise forms.ValidationError('Enter a receiver username or UPI handle.')
        return username

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('enable_roundup') and cleaned_data.get('roundup_destination') == 'goal' and not cleaned_data.get('roundup_goal'):
            self.add_error('roundup_goal', 'Choose a Piggy Bank for roundup savings.')
        return cleaned_data


class GoalForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Goal
        fields = ['title', 'target_amount', 'target_date', 'priority']
        widgets = {
            'target_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()


class ProfileSettingsForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['monthly_budget', 'home_city', 'budget_cycle', 'upi_pin']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()
        self.fields['monthly_budget'].label = 'Budget amount (Rs.)'
        self.fields['budget_cycle'].label = 'Budget cycle'
        self.fields['home_city'].label = 'Home city'
        self.fields['upi_pin'].label = 'Change UPI PIN'
        wallet_balance = getattr(self.instance, 'wallet_balance', None)
        if wallet_balance is not None:
            self.fields['monthly_budget'].help_text = f'Budget cannot be more than your wallet balance of Rs.{wallet_balance}.'

    def clean_upi_pin(self):
        pin = self.cleaned_data['upi_pin']
        if not (pin.isdigit() and len(pin) == 4):
            raise forms.ValidationError('PIN must be exactly 4 digits.')
        return pin

    def clean_monthly_budget(self):
        budget = self.cleaned_data['monthly_budget']
        if budget < 0:
            raise forms.ValidationError('Budget cannot be negative.')
        wallet_balance = getattr(self.instance, 'wallet_balance', None)
        if wallet_balance is not None and budget > wallet_balance:
            raise forms.ValidationError(
                f'Budget cannot be greater than your wallet balance of Rs.{wallet_balance}.'
            )
        return budget


class AccountSettingsForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()
        self.fields['email'].required = False
        self.fields['first_name'].required = False
        self.fields['last_name'].required = False


class SurplusAllocationForm(StyledFormMixin, forms.Form):
    action = forms.ChoiceField(choices=[
        ('carry', 'Carry forward'),
        ('savings', 'Move to smart savings'),
    ])
    goal = forms.ModelChoiceField(queryset=Goal.objects.none(), required=False)
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user:
            self.fields['goal'].queryset = Goal.objects.filter(user=user).order_by('-created_at')
        self.apply_styles()

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('action') == 'goal' and not cleaned_data.get('goal'):
            self.add_error('goal', 'Choose a Piggy Bank when moving surplus to a goal.')
        return cleaned_data

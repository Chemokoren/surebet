from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User


class EmailAuthenticationForm(AuthenticationForm):
    """Custom authentication form that uses email instead of username"""

    username = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={
            'class': 'form-control border-primary',
            'placeholder': 'you@example.com',
            'required': True
        })
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Remove the default username field styling and rename to email
        self.fields['username'].label = "Email Address"
        self.fields['password'].widget.attrs.update({
            'class': 'form-control border-primary',
            'placeholder': 'Enter your password',
            'required': True
        })

    def clean_username(self):
        """Clean and validate the email field"""
        email = self.cleaned_data.get('username')
        if email:
            try:
                user = User.objects.get(email=email)
                return user.username  # Return the username for authentication
            except User.DoesNotExist:
                raise forms.ValidationError("No account found with this email address.")
        return email


class UserRegistrationForm(UserCreationForm):
    """Custom registration form with email requirement"""
    email = forms.EmailField(required=True, help_text='Required. Inform a valid email address.')

    class Meta:
        model = User
        fields = ("username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields:
            self.fields[field].widget.attrs.update({
                'class': 'form-control',
                'placeholder': field.replace('_', ' ').capitalize()
            })

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user
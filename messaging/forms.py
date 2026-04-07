# messaging/forms.py

from django import forms
from .models import Message
from billing.models import CustomUser


class MessageForm(forms.ModelForm):

    class Meta:
        model = Message
        fields = ["recipient", "subject", "body"]

    recipient = forms.ModelChoiceField(
        queryset=CustomUser.objects.none(),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Recipient",
    )

    def __init__(self, *args, **kwargs):
        hospital = kwargs.pop("hospital", None)
        super().__init__(*args, **kwargs)

        if hospital:
            users = CustomUser.objects.filter(
                hospital=hospital,
                is_active=True
            )

            roles = users.values_list("role", flat=True).distinct()

            grouped_choices = []
            for role in roles:
                role_users = users.filter(role=role)
                choices = [(u.id, u.username) for u in role_users]
                grouped_choices.append((role.capitalize(), choices))

            self.fields["recipient"].choices = grouped_choices

        self.fields["subject"].widget.attrs.update({"class": "form-control"})
        self.fields["body"].widget.attrs.update(
            {"class": "form-control", "rows": 5}
        )
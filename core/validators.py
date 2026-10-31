"""Password rule from the problem statement:
more than eight characters, with an uppercase letter, a lowercase letter
and a special character.

Length is enforced by Django's MinimumLengthValidator (min_length=9 in
settings.py). This validator adds the three character-class rules, so
every failed rule is reported at once.
"""
from django.core.exceptions import ValidationError


class CharacterClassValidator:
    def validate(self, password, user=None):
        errors = []
        if not any(c.isupper() for c in password):
            errors.append(ValidationError(
                "Password must contain at least one uppercase letter.", code="no_upper"))
        if not any(c.islower() for c in password):
            errors.append(ValidationError(
                "Password must contain at least one lowercase letter.", code="no_lower"))
        if not any((not c.isalnum()) and (not c.isspace()) for c in password):
            errors.append(ValidationError(
                "Password must contain at least one special character (e.g. ! @ # $ %).",
                code="no_special"))
        if errors:
            raise ValidationError(errors)

    def get_help_text(self):
        return ("Your password must be longer than 8 characters and contain an uppercase "
                "letter, a lowercase letter and a special character.")

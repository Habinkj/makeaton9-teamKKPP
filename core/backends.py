"""Log in with email (case-insensitive). Falls back to ModelBackend so
`createsuperuser` accounts (which log in by username) still work."""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if not username or password is None:
            return None
        try:
            user = User.objects.get(email__iexact=username.strip())
        except (User.DoesNotExist, User.MultipleObjectsReturned):
            User().set_password(password)  # equalise timing; don't leak which emails exist
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

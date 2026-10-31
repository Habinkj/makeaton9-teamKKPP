# brightloop/settings.py — 3 edits

1) AUTH_PASSWORD_VALIDATORS: add this entry at the END of the list (keep the existing four, incl. min_length 9):

    {'NAME': 'core.validators.CharacterClassValidator'},

2) Add anywhere (e.g. under AUTH_USER_MODEL):

    AUTHENTICATION_BACKENDS = [
        'core.backends.EmailBackend',                  # log in with email, any case
        'django.contrib.auth.backends.ModelBackend',   # createsuperuser accounts by username
    ]

3) TEMPLATES -> OPTIONS -> context_processors: add as the last item:

    'core.context.role',

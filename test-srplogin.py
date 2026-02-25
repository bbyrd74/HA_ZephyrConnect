#!/usr/bin/env python3
# srplogin.py — SRP login using pycognito
# Usage:
# 1) install: pip install pycognito
# 2) export ZEPHYREMAIL and ZEPHYRPASS (recommended) or edit the USERNAME/PASSWORD below
# 3) python3 srplogin.py

from pycognito import Cognito
import os
import sys

USER_POOL_ID = "us-west-2_McuoKpkna"
CLIENT_ID = "5a2qiskdvvu7gre1jvbjnunu20"
CLIENT_SECRET = "3b085l2fkgph4kt734k5e26tirb9hjasgb4rn8sjpp4mheo5kga"  # include if client requires secret

USERNAME = os.environ.get("ZEPHYREMAIL", "your@email.com")
PASSWORD = os.environ.get("ZEPHYRPASS", "yourpassword")

def main():
    # Pass client_secret using the parameter name pycognito expects
    try:
        u = Cognito(USER_POOL_ID, CLIENT_ID, username=USERNAME, client_secret=CLIENT_SECRET)
    except TypeError:
        # Fallback: some versions accept 'client_secret' but if not, try without it
        u = Cognito(USER_POOL_ID, CLIENT_ID, username=USERNAME)

    try:
        u.authenticate(PASSWORD)
    except Exception as e:
        # Print a helpful message for common Cognito challenges
        print("Authentication failed or raised a challenge:", e)
        print("If MFA or NEW_PASSWORD_REQUIRED is required, you'll need to handle that.")
        sys.exit(2)

    # Tokens
    print("IdToken:", getattr(u, "id_token", None))
    print()
    print("AccessToken:", getattr(u, "access_token", None))
    print()
    print("RefreshToken:", getattr(u, "refresh_token", None))
    print()

if __name__ == "__main__":
    main()

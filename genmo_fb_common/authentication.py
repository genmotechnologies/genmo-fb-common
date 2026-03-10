from rest_framework import authentication
class SessionTokenAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        bank_customer_id = getattr(request, 'bank_customer_id', None)
        if not bank_customer_id:
            return None
        return (None, None)
"""
genmo-fb-common's own user-facing texts (PL8 / XC-01): the refusals the shared middleware,
ownership checks and permission classes give the app, in the bank's languages.

French and Arabic are drafts: to be reviewed by a translator (PL8).
"""

from .i18n import Messages

MESSAGES = Messages(
    {
        "token.invalid": {
            "en": "The access token is not valid.",
            "fr": "Le jeton d'accès n'est pas valide.",
            "ar": "رمز الوصول غير صالح.",
        },
        "token.unavailable": {
            "en": "Sign-in can't be checked right now. Please try again.",
            "fr": "La connexion ne peut pas être vérifiée pour le moment. Veuillez réessayer.",
            "ar": "لا يمكن التحقق من تسجيل الدخول الآن. يرجى المحاولة مرة أخرى.",
        },
        "family.not_your_child": {
            "en": "This child is not in your family.",
            "fr": "Cet enfant ne fait pas partie de votre famille.",
            "ar": "هذا الطفل ليس من عائلتك.",
        },
        "family.permission_missing": {
            "en": "The family holder has not given you this permission.",
            "fr": "Le titulaire de la famille ne vous a pas donné cette autorisation.",
            "ar": "لم يمنحك صاحب حساب العائلة هذا الإذن.",
        },
        "family.unavailable": {
            "en": "Family details are unavailable right now. Please try again.",
            "fr": "Les informations de la famille ne sont pas disponibles pour le moment. "
            "Veuillez réessayer.",
            "ar": "تفاصيل العائلة غير متاحة الآن. يرجى المحاولة مرة أخرى.",
        },
        "family.parents_only": {
            "en": "Only parents can perform this action.",
            "fr": "Seuls les parents peuvent effectuer cette action.",
            "ar": "يمكن للوالدين فقط القيام بهذا الإجراء.",
        },
        "family.children_only": {
            "en": "Only children can perform this action.",
            "fr": "Seuls les enfants peuvent effectuer cette action.",
            "ar": "يمكن للأطفال فقط القيام بهذا الإجراء.",
        },
        "family.members_only": {
            "en": "You must be a member of this family.",
            "fr": "Vous devez être membre de cette famille.",
            "ar": "يجب أن تكون فردًا في هذه العائلة.",
        },
        "auth.required": {
            "en": "Authentication credentials were not provided or are invalid.",
            "fr": "Les informations d'authentification sont absentes ou non valides.",
            "ar": "لم يتم تقديم بيانات المصادقة أو أنها غير صالحة.",
        },
    }
)


class LocalisedDetail:
    """
    For a DRF exception: its text is looked up when it is raised, in that request's
    language (a `default_detail` set on the class would be frozen in one language).
    """

    message_key = ""

    def __init__(self, detail=None, code=None):
        super().__init__(detail or MESSAGES.text(self.message_key), code)


class LocalisedMessage:
    """For a DRF permission class: `message` is looked up when the request is refused."""

    message_key = ""

    @property
    def message(self):
        return MESSAGES.text(self.message_key)

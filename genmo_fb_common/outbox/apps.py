from django.apps import AppConfig


class OutboxConfig(AppConfig):
    """
    The outbox and processed-event tables (FC-02, FC-03). Add "genmo_fb_common.outbox" to
    INSTALLED_APPS and run `migrate`; each service keeps them in its own database.
    """

    name = "genmo_fb_common.outbox"
    label = "gfc_outbox"
    default_auto_field = "django.db.models.BigAutoField"
